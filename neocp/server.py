"""NeoCP server – exposes Steinberg Cubase / Nuendo to MCP clients (Claude etc.)."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

TrackRef = Union[int, str]

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server, Image
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image

from . import __version__
from . import music as M
from .bridge import CubaseBridge, CubaseError, SETUP_HINT, _parse_number

logging.basicConfig(level=os.environ.get("NEOCP_LOG", "INFO"),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("neocp")

INSTRUCTIONS = """Control Steinberg Cubase/Nuendo (13+) live through its MIDI Remote API.

How Cubase thinks (important):
* Tracks are addressed by index (0 = first channel in the MixConsole, excluding input/output busses).
* Deep editing (inserts, EQ, pre-filter, strip, instrument, quick controls, plug-in parameters)
  always acts on the SELECTED track: tools that take track_index select it first.
* Anything in Cubase's menus/key commands can be run with execute_command; use search_commands
  to find the exact category/command pair (1400+ commands: add tracks, quantize, bounce, export...).
* Notes cannot be written directly into parts by the API. Use record_notes_to_track: it arms the
  track, records the notes in real time from the 'NeoCP Notes' MIDI port and stops (optionally
  faster than real time and quantized). Or export_midi_file for drag & drop.
* Plug-in parameters / loading plug-ins use DirectAccess (Cubase 13.0.50+, plug-in loading 15+).
* You can LISTEN: listen_to_cubase plays a section and analyses the audio (LUFS, spectrum, key, chords,
  tempo, mix suggestions); listen_to_meters works without any audio routing; transcribe_audio turns a hummed
  melody into notes; compare_to_reference compares with a reference track; show_spectrogram lets you SEE it;
  analyze_song_structure finds intro/verse/chorus; gain_stage_tracks, fix_resonances, match_loudness and
  auto_mix_pass close the loop (listen -> adjust -> listen again).
* transcribe_drums turns drum audio into a GM drum pattern; check_mix_against_genre compares a mix with
  typical genre ranges; run_selftest diagnoses the setup; bind_new_command exposes any new Cubase 16
  key command.
* transcribe_polyphonic turns audio with chords + bass + melody into MIDI parts (and chord names),
  and can record them into tracks.
* write_automation performs fader/pan/send/plug-in moves in real time (ramps, fades, LFO, sidechain duck,
  trance gate, breakpoints). load_instrument / load_effect / create_instrument_track load plug-ins by
  name (Cubase 15+).
* Values are reported with Cubase's own display strings (e.g. "-6.02 dB"), use them to verify.
Always call get_session_info first to see tracks and transport state.
"""

mcp = _Server("NeoCP", instructions=INSTRUCTIONS)

_bridge: Optional[CubaseBridge] = None
_bridge_lock = threading.Lock()
DATA = Path(__file__).parent / "data"
_COMMANDS: Optional[List[Dict[str, Any]]] = None


def bridge() -> CubaseBridge:
    """Return a connected bridge (created lazily so the server starts without Cubase)."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            from .link import MidoLink
            try:
                link = MidoLink()
            except Exception as e:
                raise CubaseError(f"cannot open MIDI ports: {e}. " + SETUP_HINT) from e
            _bridge = CubaseBridge(link)
        b = _bridge
    b.ensure()
    return b


def set_bridge(b: Optional[CubaseBridge]) -> None:
    """Inject a bridge (tests / alternative transports)."""
    global _bridge
    _bridge = b


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def tool(fn):
    """Register an MCP tool that converts errors into readable messages."""
    import functools

    exempt = fn.__name__ in ("stop_notes", "connection_status", "search_commands", "list_command_categories")

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            if exempt or _bridge is None:
                res = fn(*a, **kw)
            else:
                with _bridge.op_lock:
                    res = fn(*a, **kw)
            if isinstance(res, (str, list)) or type(res).__name__ == "Image":
                return res
            return _ok(res)
        except CubaseError as e:
            return _ok({"error": str(e)})
        except (ValueError, KeyError, TypeError, IndexError) as e:
            return _ok({"error": f"{type(e).__name__}: {e}"})
    return mcp.tool()(wrapper)


def commands() -> List[Dict[str, Any]]:
    global _COMMANDS
    if _COMMANDS is None:
        _COMMANDS = json.loads((DATA / "commands.json").read_text(encoding="utf-8"))
        try:
            from .install import extra_commands
            for cat, cmd in extra_commands():
                _COMMANDS.append({"group": cat, "label": cmd, "category": cat, "command": cmd, "user_added": True})
        except Exception:
            pass
    return _COMMANDS


# ============================================================ value helpers

def _flag(b: CubaseBridge, key: str) -> Optional[bool]:
    v = b.value(key)
    return None if v is None else v > 0.5


def _val(b: CubaseBridge, key: str) -> Dict[str, Any]:
    if not b.has(key):
        return {}
    st = b.get(key)
    out = {"value": None if st["value"] is None else round(st["value"], 4)}
    if st["display"] is not None:
        out["display"] = b.display(key)
    if st["value_title"]:
        out["name"] = st["value_title"]
    return out


def _set_toggle(b: CubaseBridge, key: str, on: bool) -> Dict[str, Any]:
    st = b.set_and_wait(key, 1.0 if on else 0.0)
    return {"control": key, "on": st["value"] is not None and st["value"] > 0.5}


def _set_level(b: CubaseBridge, key: str, db: Optional[float], normalized: Optional[float]) -> Dict[str, Any]:
    if normalized is not None:
        st = b.set_and_wait(key, float(normalized))
    elif db is not None:
        if db <= -120:
            st = b.set_and_wait(key, 0.0)
        else:
            st = b.set_display_target(key, float(db), tolerance=0.1)
    else:
        raise ValueError("give db or normalized")
    return {"control": key, "value": st["value"], "display": b.display(key)}


def _channel_summary(b: CubaseBridge, pre: str, index: Optional[int] = None) -> Dict[str, Any]:
    o = b.objects.get(pre) or {}
    d: Dict[str, Any] = {"name": b.channel_title(pre)}
    if index is not None:
        d["index"] = index
    if o.get("color"):
        d["color_rgba"] = o["color"]
    d["volume"] = _val(b, f"{pre}.volume")
    d["pan"] = _val(b, f"{pre}.pan")
    for k in ("mute", "solo", "rec", "monitor", "selected", "aread", "awrite"):
        if b.has(f"{pre}.{k}"):
            d[{"rec": "record_arm", "aread": "automation_read", "awrite": "automation_write"}.get(k, k)] = _flag(b, f"{pre}.{k}")
    return d


def _sends(b: CubaseBridge, pre: str) -> List[Dict[str, Any]]:
    out = []
    for i in range(int(b.info.get("send_slots", 8))):
        sp = f"{pre}.send{i}"
        if not b.has(f"{sp}.level"):
            continue
        o = b.objects.get(sp) or {}
        dest = o.get("title")
        out.append({"slot": i, "destination": dest, "on": _flag(b, f"{sp}.on"),
                    "level": _val(b, f"{sp}.level"), "pre_fader": _flag(b, f"{sp}.prepost")})
    return out


def _plugin(b: CubaseBridge, pre: str) -> Dict[str, Any]:
    o = b.objects.get(pre) or {}
    p = o.get("plugin") or {}
    d = {"plugin": p.get("name") or None, "vendor": p.get("vendor") or None, "title": o.get("title")}
    for k, n in (("on", "active"), ("bypass", "bypassed"), ("edit", "editor_open")):
        if b.has(f"{pre}.{k}"):
            d[n] = _flag(b, f"{pre}.{k}")
    return d


def _track_count_hint(b: CubaseBridge) -> int:
    return len(b.bank_channels())


def _resolve_track(b: CubaseBridge, track: Any) -> int:
    """Accept an index or a (case-insensitive) track name."""
    if isinstance(track, int) or (isinstance(track, str) and track.strip().lstrip("-").isdigit()):
        return int(track)
    name = str(track).strip().lower()
    for ch in b.bank_channels():
        if ch["name"].lower() == name:
            return ch["index"]
    for ch in b.bank_channels():
        if name in ch["name"].lower():
            return ch["index"]
    raise CubaseError(f"no track named '{track}'")


def _select(b: CubaseBridge, track: Any) -> Dict[str, Any]:
    idx = _resolve_track(b, track)
    name = b.select_track(idx)
    return {"index": idx, "name": name}


# ============================================================ connection / session

@tool
def connection_status() -> Dict[str, Any]:
    """Check the link to Cubase: MIDI ports, script version, API features (DirectAccess etc.)."""
    b = bridge()
    return {"server_version": __version__, "connected": b.connected, "write_mode": b.write_mode,
            "ports": b.link.describe(), "script": b.info, "controls": len(b.keys),
            "commands_bound": len(b.command_ids), "last_event_s_ago": round(time.time() - b.last_event_time, 2)
            if b.last_event_time else None}


@tool
def reconnect() -> Dict[str, Any]:
    """Re-run the handshake (use after reloading the MIDI Remote script or restarting Cubase)."""
    b = bridge()
    b.connected = False
    return b.connect()


@tool
def get_session_info() -> Dict[str, Any]:
    """Overview of the project: tempo, transport, locators, selected track and all tracks with mixer state."""
    b = bridge()
    tr = b.transport
    return {
        "tempo_bpm": tr.get("tempo"),
        "playing": _flag(b, "transport.play"), "recording": _flag(b, "transport.record"),
        "cycle": _flag(b, "transport.cycle"), "metronome": _flag(b, "transport.metronome"),
        "cursor": tr.get("cursor"), "cursor_format": tr.get("cursor_format"),
        "cursor_secondary": tr.get("cursor2"), "left_locator": tr.get("left"), "right_locator": tr.get("right"),
        "selected_track": b.channel_title("sel"),
        "tracks": [_channel_summary(b, f"ch{c['bank_slot']}", c["index"]) for c in b.bank_channels()],
        "bank_offset": b.bank_offset, "bank_size": b.bank_size,
        "busses": [{"name": (b.objects.get(f"io{i}") or {}).get("title"), "volume": _val(b, f"io{i}.volume")}
                   for i in range(8) if (b.objects.get(f"io{i}") or {}).get("title")],
    }


@tool
def list_tracks(page: int = 0) -> Dict[str, Any]:
    """List tracks (name, index, color). Projects with more tracks than the bank size are paged."""
    b = bridge()
    if page * b.bank_size != b.bank_offset:
        b.ensure_track_in_bank(page * b.bank_size)
    return {"page": page, "bank_size": b.bank_size, "tracks": b.bank_channels()}


@tool
def get_track_info(track: TrackRef) -> Dict[str, Any]:
    """Mixer info for a track (index or name): volume, pan, mute, solo, arm, monitor, automation, sends."""
    b = bridge()
    idx = _resolve_track(b, track)
    pre = b.track_prefix(idx)
    d = _channel_summary(b, pre, idx)
    d["sends"] = _sends(b, pre)
    d["editor_open"] = _flag(b, f"{pre}.editor")
    d["instrument_open"] = _flag(b, f"{pre}.instrument")
    return d


@tool
def get_selected_track_info(include_parameters: bool = True) -> Dict[str, Any]:
    """Full channel strip of the selected track: inserts (plug-in names), sends, EQ, pre-filter,
    strip modules, instrument + its parameter bank, quick controls."""
    b = bridge()
    d = _channel_summary(b, "sel")
    d["sends"] = _sends(b, "sel")
    d["inserts"] = []
    for i in range(int(b.info.get("insert_slots", 16))):
        p = _plugin(b, f"sel.ins{i}")
        if p.get("plugin") or p.get("title"):
            d["inserts"].append({"slot": i, **p})
    d["instrument"] = _plugin(b, "sel.inst")
    if include_parameters and d["instrument"].get("plugin"):
        d["instrument"]["parameters"] = [
            {"index": i, **_val(b, f"sel.inst.p{i}")} for i in range(int(b.info.get("instrument_params", 16)))
            if b.has(f"sel.inst.p{i}") and b.get(f"sel.inst.p{i}")["value_title"]]
    d["strip"] = {m: _plugin(b, f"sel.strip.{m}") for m in ("gate", "comp", "tools", "sat", "limit")}
    d["eq"] = [{"band": n, "on": _flag(b, f"sel.eq{n}.on"), "gain": _val(b, f"sel.eq{n}.gain"),
                "freq": _val(b, f"sel.eq{n}.freq"), "q": _val(b, f"sel.eq{n}.q"), "type": _val(b, f"sel.eq{n}.type")}
               for n in range(1, 5)]
    d["pre_filter"] = {k: _val(b, f"sel.pre.{k}") for k in
                       ("bypass", "gain", "phase", "lcon", "lcfreq", "lcslope", "hcon", "hcfreq", "hcslope")}
    d["quick_controls"] = [{"index": i, **_val(b, f"sel.qc{i}")} for i in range(8) if b.has(f"sel.qc{i}")]
    return d


@tool
def review_project() -> Dict[str, Any]:
    """Instant project review from the live mirror (no audio needed): forgotten solos/mutes, armed tracks,
    hot or untouched faders, everything panned centre, unused sends, tempo left at default, empty tracks.
    Returns prioritised findings with the tool to fix each one."""
    b = bridge()
    b.refresh()
    tr = b.bank_channels()
    find: List[Dict[str, Any]] = []

    def add(level: str, msg: str, fix: str) -> None:
        find.append({"level": level, "finding": msg, "fix": fix})
    solos = [c["name"] for c in tr if _flag(b, f"ch{c['bank_slot']}.solo")]
    mutes = [c["name"] for c in tr if _flag(b, f"ch{c['bank_slot']}.mute")]
    armed = [c["name"] for c in tr if _flag(b, f"ch{c['bank_slot']}.rec")]
    vols = {c["name"]: _fader_db(b, f"ch{c['bank_slot']}.volume") for c in tr}
    pans = {c["name"]: b.value(f"ch{c['bank_slot']}.pan") for c in tr}
    if solos:
        add("high", f"Solo active on {solos}: the rest of the mix is silent.", "project_action('unsolo_all')")
    if armed:
        add("medium", f"Record-armed tracks {armed}: pressing record will overwrite takes.", "set_track_state(track, record_arm=False)")
    if mutes:
        add("info", f"Muted tracks {mutes}.", "set_track_state(track, mute=False) if not intended")
    hot = [n for n, v in vols.items() if v is not None and v > 3]
    if hot:
        add("medium", f"Faders above +3 dB on {hot}: gain-stage at the source instead.", "gain_stage_tracks()")
    known = [v for v in vols.values() if v is not None and v != float("-inf")]
    if len(known) >= 3 and max(known) - min(known) < 0.5:
        add("medium", "All faders at the same level: the mix is not balanced yet.", "gain_stage_tracks() then set_track_volume")
    pv = [v for v in pans.values() if v is not None]
    if len(pv) >= 4 and all(abs(v - 0.5) < 0.01 for v in pv):
        add("low", "Every track is panned centre: no stereo width.", "set_track_pan on guitars/keys/percussion")
    sends_used = sum(1 for c in tr for i in range(int(b.info.get("send_slots", 8)))
                     if _flag(b, f"ch{c['bank_slot']}.send{i}.on"))
    if tr and not sends_used:
        add("low", "No sends active: no shared reverb/delay space.", "create_track('fx') + load_effect('REVerence') + set_send")
    tempo = b.transport.get("tempo")
    if tempo is not None and abs(tempo - 120) < 0.01:
        add("info", "Tempo is still the default 120 BPM.", "set_tempo(bpm)")
    if not tr:
        add("high", "No tracks found in the mirrored bank.", "create_track / produce_song")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    find.sort(key=lambda f: order[f["level"]])
    return {"tracks": len(tr), "tempo": tempo, "findings": find or [{"level": "info", "finding": "Nothing obvious to fix.",
                                                                       "fix": "listen_to_cubase for an audio check"}]}


# ============================================================ transport

@tool
def transport(action: str) -> Dict[str, Any]:
    """Transport control. action: play | stop | record | record_stop | rewind | forward | toggle_play |
    go_to_start | go_to_end | go_to_left_locator | go_to_right_locator | next_marker | previous_marker."""
    b = bridge()
    a = action.lower()
    if a == "play":
        b.set("transport.play", 1.0)
    elif a == "stop":
        b.set("transport.stop", 1.0)
        time.sleep(0.05)
        b.set("transport.stop", 0.0)
    elif a == "record":
        b.set("transport.record", 1.0)
    elif a == "record_stop":
        b.set("transport.record", 0.0)
    elif a == "toggle_play":
        b.set("transport.play", 0.0 if _flag(b, "transport.play") else 1.0)
    elif a in ("rewind", "forward"):
        b.set(f"transport.{a}", 1.0)
        time.sleep(0.3)
        b.set(f"transport.{a}", 0.0)
    else:
        cmd = {"go_to_start": ("Transport", "Return to Zero"), "go_to_end": ("Transport", "Goto End"),
               "go_to_left_locator": ("Transport", "To Left Locator"),
               "go_to_right_locator": ("Transport", "To Right Locator"),
               "next_marker": ("Transport", "Locate Next Marker"),
               "previous_marker": ("Transport", "Locate Previous Marker")}.get(a)
        if not cmd:
            raise ValueError(f"unknown transport action '{action}'")
        b.command(*cmd)
    time.sleep(0.15)
    return get_transport_state.__wrapped__()


@tool
def get_transport_state() -> Dict[str, Any]:
    """Tempo, play/record state, cursor/locator positions, cycle and metronome state."""
    b = bridge()
    tr = b.transport
    return {"tempo_bpm": tr.get("tempo"), "playing": _flag(b, "transport.play"),
            "recording": _flag(b, "transport.record"), "cycle": _flag(b, "transport.cycle"),
            "metronome": _flag(b, "transport.metronome"), "click_level": _val(b, "transport.clicklevel"),
            "cursor": tr.get("cursor"), "cursor_format": tr.get("cursor_format"),
            "cursor_secondary": tr.get("cursor2"), "left_locator": tr.get("left"), "right_locator": tr.get("right")}


@tool
def set_tempo(bpm: float) -> Dict[str, Any]:
    """Set the project tempo (BPM, 20-300). Affects the fixed tempo (or the tempo track at the cursor)."""
    b = bridge()
    if not 20 <= bpm <= 300:
        raise ValueError("bpm must be between 20 and 300")
    b.request("tempo", bpm=float(bpm))
    b.wait_for(lambda: b.transport.get("tempo") is not None and abs(b.transport["tempo"] - bpm) < 0.01, 1.0)
    return {"tempo_bpm": b.transport.get("tempo")}


def _is_musical(fmt: Any) -> bool:
    f = str(fmt or "").lower()
    return (not f) or "bar" in f or "beat" in f or "musical" in f


def _position_string(b: CubaseBridge, bar: Optional[float], beat: float = 1, position: Optional[str] = None,
                     beats_per_bar: int = 4) -> str:
    """Position string in the PRIMARY time format (bars.beats.16th.ticks, or seconds)."""
    if position:
        return position
    if bar is None:
        raise ValueError("give bar or position")
    total16 = int(round(((bar - 1) * beats_per_bar + (beat - 1)) * 4))
    total16 = max(0, total16)
    if _is_musical(b.transport.get("cursor_format")):
        bars_, rem = divmod(total16, beats_per_bar * 4)
        beats_, six = divmod(rem, 4)
        return f"{bars_ + 1}.{beats_ + 1}.{six + 1}.0"
    tempo = float(b.transport.get("tempo") or 120.0)
    secs = total16 / 4 * 60.0 / tempo
    h, rem = divmod(secs, 3600)
    m, s_ = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s_:06.3f}"


def _locate(b: CubaseBridge, locator: str, bar: Optional[float], beat: float = 1, position: Optional[str] = None,
            beats_per_bar: int = 4) -> str:
    """Set a locator; uses the secondary display when only it is musical (e.g. primary = timecode)."""
    if position is None and not _is_musical(b.transport.get("cursor_format")) and \
            _is_musical(b.transport.get("cursor2_format")) and b.transport.get("cursor2_format"):
        total16 = max(0, int(round(((bar - 1) * beats_per_bar + (beat - 1)) * 4)))
        bars_, rem = divmod(total16, beats_per_bar * 4)
        beats_, six = divmod(rem, 4)
        pos = f"{bars_ + 1}.{beats_ + 1}.{six + 1}.0"
        b.request("time", locator=locator, value=pos, display="secondary")
        return pos
    pos = _position_string(b, bar, beat, position, beats_per_bar)
    b.request("time", locator=locator, value=pos)
    return pos


@tool
def set_position(bar: Optional[float] = None, beat: float = 1, position: Optional[str] = None,
                 locator: str = "cursor") -> Dict[str, Any]:
    """Move the project cursor (or the left/right locator). Use bar (1-based, may be fractional) and beat,
    or a raw position string in Cubase's primary time format (e.g. "17.1.1.0" or "0:01:30.000").
    locator: cursor | left | right."""
    b = bridge()
    pos = _locate(b, locator, bar, beat, position)
    time.sleep(0.1)
    return {"set": pos, **get_transport_state.__wrapped__()}


@tool
def set_loop_range(start_bar: float, end_bar: float, enable_cycle: bool = True) -> Dict[str, Any]:
    """Set left/right locators to a bar range (end exclusive) and optionally enable cycle."""
    b = bridge()
    _locate(b, "left", start_bar)
    _locate(b, "right", end_bar)
    if enable_cycle:
        b.set("transport.cycle", 1.0)
    time.sleep(0.1)
    return get_transport_state.__wrapped__()


@tool
def set_cycle(on: bool) -> Dict[str, Any]:
    """Enable/disable cycle (loop) playback."""
    return _set_toggle(bridge(), "transport.cycle", on)


@tool
def set_metronome(on: bool, click_level: Optional[float] = None) -> Dict[str, Any]:
    """Enable/disable the metronome click; optional click_level 0..1."""
    b = bridge()
    r = _set_toggle(b, "transport.metronome", on)
    if click_level is not None:
        r["click_level"] = b.set_and_wait("transport.clicklevel", click_level)
    return r


# ============================================================ self test

@tool
def run_selftest(write_tests: bool = True) -> Dict[str, Any]:
    """Check every NeoCP feature against the running Cubase and report what works on THIS setup:
    handshake, live events, tracks, tempo, NRPN and SysEx value writes, display strings, commands,
    DirectAccess, plug-in manager, notes port and audio devices. Write tests are reversible (each value is
    restored). If NRPN writes fail but SysEx works, the session switches to SysEx automatically."""
    results: List[Dict[str, Any]] = []

    def rec(name: str, ok: Optional[bool], detail: Any = "", fix: str = "") -> None:
        results.append({"test": name, "status": "ok" if ok else ("skip" if ok is None else "FAIL"),
                        "detail": detail, **({"fix": fix} if fix and not ok else {})})
    try:
        b = bridge()
    except CubaseError as e:
        return {"summary": "cannot reach Cubase", "results": [{"test": "connection", "status": "FAIL", "detail": str(e)}]}
    info = b.info
    feats = info.get("features", {})
    rec("handshake", True, f"script {info.get('script_version')} / server {__version__}, {len(b.keys)} controls")
    if info.get("script_version") != __version__:
        rec("script version", False, f"script {info.get('script_version')} != server {__version__}",
            "run neocp-install and press Reload Scripts in Cubase")
    rec("host", True, f"{feats.get('app')} {feats.get('app_version')}, sends={feats.get('send_slots')}, "
                      f"inserts={feats.get('insert_slots')}")
    rec("idle callbacks", bool(feats.get("idle")), "API 1.1+ idle loop" if feats.get("idle") else "Cubase 12 fallback mode")
    try:
        b.request("ping", timeout=2)
        rec("round trip", True, "ping ok")
    except CubaseError as e:
        rec("round trip", False, str(e))
    b.refresh()
    tracks = b.bank_channels()
    if not tracks:
        vols = [b.get(f"ch{i}.volume") for i in range(b.bank_size) if b.has(f"ch{i}.volume")]
        with_value = sum(1 for v in vols if v["value"] is not None)
        detail = (f"no track names received ({with_value} bank channels report a volume value)"
                  if with_value else "no tracks: the mixer bank is empty")
    else:
        detail = f"{len(tracks)} tracks: " + ", ".join(t["name"] for t in tracks[:8])
    rec("tracks mirrored", bool(tracks), detail,
        "open a project with audio/instrument/MIDI tracks; if it has tracks and this still fails, send this "
        "report (title callbacks not delivered)")
    tempo = b.transport.get("tempo")
    rec("tempo read", tempo is not None, tempo)
    rec("cursor read", b.transport.get("cursor") is not None, b.transport.get("cursor"),
        "the transport position callback did not fire: move the cursor once in Cubase")
    sel = b.channel_title("sel")
    rec("selected track", bool(sel), sel or "none", "click a track in Cubase's project window to select it")
    if write_tests:
        if tempo:
            try:
                b.request("tempo", bpm=float(tempo) + 1)
                okt = b.wait_for(lambda: abs((b.transport.get("tempo") or 0) - (float(tempo) + 1)) < 0.01, 1.5)
                b.request("tempo", bpm=float(tempo))
                rec("tempo write", okt, "set +1 BPM and restored")
            except CubaseError as e:
                rec("tempo write", False, str(e))
        key = f"ch{tracks[0]['bank_slot']}.pan" if tracks else None
        if key:
            orig = b.value(key) if b.value(key) is not None else 0.5
            target = 0.3 if orig > 0.5 else 0.7
            results_mode = {}
            for mode in ("nrpn", "sysex"):
                b.write_mode = mode
                b.set(key, target)
                results_mode[mode] = b.wait_for(lambda: b.value(key) is not None and abs(b.value(key) - target) < 0.01, 1.2)
                b.set(key, orig)
                b.wait_for(lambda: b.value(key) is not None and abs(b.value(key) - orig) < 0.01, 1.2)
            rec("value write (NRPN)", results_mode["nrpn"], f"pan of '{tracks[0]['name']}' moved and restored",
                "NRPN not reaching Cubase: NEOCP_WRITE_MODE=sysex")
            rec("value write (SysEx)", results_mode["sysex"], "setProcessValue path")
            b.write_mode = "nrpn" if results_mode["nrpn"] or not results_mode["sysex"] else "sysex"
            if b.write_mode == "sysex":
                rec("write mode", True, "switched this session to SysEx writes (set NEOCP_WRITE_MODE=sysex to persist)")
            disp = b.display(f"ch{tracks[0]['bank_slot']}.volume")
            rec("display strings", disp is not None, disp, "Cubase did not send display values")
        met = b.value("transport.metronome")
        met = 0.0 if met is None else met
        if True:
            b.set("transport.metronome", 0.0 if met > 0.5 else 1.0)
            okm = b.wait_for(lambda: ((b.value("transport.metronome") or 0) > 0.5) != (met > 0.5), 1.0)
            b.set("transport.metronome", met)
            rec("transport toggle", okm, "metronome toggled and restored")
    try:
        r = b.request("can_perform", category="Edit", command="Undo")
        rec("commands bound", r.get("bound"), f"{len(b.command_ids)} commands; canPerform={r.get('can_perform')}")
    except CubaseError as e:
        rec("commands bound", False, str(e))
    if feats.get("direct_access"):
        try:
            t = b.request("da_tree", root="selected", depth=1, max_nodes=50, timeout=5)
            rec("DirectAccess", True, f"'{t.get('title')}' with {t.get('children')} child objects")
        except CubaseError as e:
            rec("DirectAccess", False, str(e), "select a track; needs Cubase 13.0.50+")
        try:
            sl = b.request("da_plugin_slots", root="selected", depth=4, timeout=10)["slots"]
            rec("plug-in manager", bool(sl), f"{len(sl)} plug-in slots on the selected track",
                "needs Cubase 15.0.20+ (load_instrument / load_effect)")
        except CubaseError as e:
            rec("plug-in manager", None, f"not available: {e}")
    else:
        rec("DirectAccess", None, "not available (Cubase < 13.0.50): plug-in parameter tools disabled")
    d = b.link.describe()
    rec("notes port", bool(d.get("notes")), d.get("notes") or "missing",
        "create the 'NeoCP Notes' port (loopMIDI on Windows) for record_notes_to_track / play_notes")
    try:
        from . import capture
        devs = capture.list_input_devices()
        loop = [x["name"] for x in devs if x["looks_like_loopback"]]
        rec("audio capture", bool(devs), f"{len(devs)} inputs")
        rec("loopback for listening", bool(loop) or bool(os.environ.get("NEOCP_LISTEN_DEVICE")),
            loop or os.environ.get("NEOCP_LISTEN_DEVICE") or "none",
            "install BlackHole (macOS) / VoiceMeeter or VB-Cable (Windows), see docs section 6; "
            "listen_to_meters works without it")
    except Exception as e:
        rec("audio capture", False, str(e), "install PortAudio / sounddevice")
    fails = [r["test"] for r in results if r["status"] == "FAIL"]
    return {"summary": "all good" if not fails else f"{len(fails)} problem(s): {', '.join(fails)}",
            "write_mode": b.write_mode, "results": results}


# ============================================================ track mixer

@tool
def select_track(track: TrackRef) -> Dict[str, Any]:
    """Select a track by index or name (makes it the target of channel-strip / plug-in tools)."""
    return _select(bridge(), track)


@tool
def set_track_volume(track: TrackRef, db: Optional[float] = None, normalized: Optional[float] = None) -> Dict[str, Any]:
    """Set fader level of a track (index or name) in dB (e.g. -6.0; <= -120 = -inf) or as normalized 0..1."""
    b = bridge()
    pre = b.track_prefix(_resolve_track(b, track))
    return _set_level(b, f"{pre}.volume", db, normalized)


@tool
def set_track_pan(track: TrackRef, pan: float) -> Dict[str, Any]:
    """Set pan: -1.0 = hard left, 0 = center, 1.0 = hard right."""
    b = bridge()
    pre = b.track_prefix(_resolve_track(b, track))
    st = b.set_and_wait(f"{pre}.pan", (max(-1.0, min(1.0, pan)) + 1.0) / 2.0)
    return {"pan": pan, "display": b.display(f"{pre}.pan"), "value": st["value"]}


@tool
def set_track_state(track: TrackRef, mute: Optional[bool] = None, solo: Optional[bool] = None,
                    record_arm: Optional[bool] = None, monitor: Optional[bool] = None,
                    automation_read: Optional[bool] = None, automation_write: Optional[bool] = None,
                    open_editor: Optional[bool] = None, open_instrument: Optional[bool] = None) -> Dict[str, Any]:
    """Set any of mute / solo / record-arm / monitor / automation read / write, or open the track's
    editor / instrument window. Only given flags change."""
    b = bridge()
    idx = _resolve_track(b, track)
    pre = b.track_prefix(idx)
    for key, val in (("mute", mute), ("solo", solo), ("rec", record_arm), ("monitor", monitor),
                     ("aread", automation_read), ("awrite", automation_write),
                     ("editor", open_editor), ("instrument", open_instrument)):
        if val is not None:
            b.set_and_wait(f"{pre}.{key}", 1.0 if val else 0.0)
    return _channel_summary(b, pre, idx)


@tool
def set_send(track: TrackRef, send_slot: int, level_db: Optional[float] = None, on: Optional[bool] = None,
             pre_fader: Optional[bool] = None, normalized: Optional[float] = None) -> Dict[str, Any]:
    """Configure a send (slot 0-7) of a track: level in dB, on/off, pre/post fader. The send's destination
    must already be assigned in Cubase (see get_track_info → sends[].destination)."""
    b = bridge()
    pre = b.track_prefix(_resolve_track(b, track))
    sp = f"{pre}.send{int(send_slot)}"
    if on is not None:
        b.set_and_wait(f"{sp}.on", 1.0 if on else 0.0)
    if pre_fader is not None:
        b.set_and_wait(f"{sp}.prepost", 1.0 if pre_fader else 0.0)
    if level_db is not None or normalized is not None:
        _set_level(b, f"{sp}.level", level_db, normalized)
    return {"destination": (b.objects.get(sp) or {}).get("title"), "on": _flag(b, f"{sp}.on"),
            "level": _val(b, f"{sp}.level"), "pre_fader": _flag(b, f"{sp}.prepost")}


@tool
def mixer_overview() -> Dict[str, Any]:
    """Compact mixer table of all mirrored tracks (volume dB, pan, M/S/R) plus live meters."""
    b = bridge()
    meters = {}
    try:
        for sid, v in b.request("meters")["slots"]:
            k = b.key_of(sid)
            if k:
                meters[k] = v
    except CubaseError:
        pass
    rows = []
    for c in b.bank_channels():
        pre = f"ch{c['bank_slot']}"
        rows.append({"index": c["index"], "name": c["name"], "volume": b.display(f"{pre}.volume"),
                     "pan": b.display(f"{pre}.pan"), "mute": _flag(b, f"{pre}.mute"), "solo": _flag(b, f"{pre}.solo"),
                     "arm": _flag(b, f"{pre}.rec"), "meter": meters.get(f"{pre}.meter"),
                     "peak": meters.get(f"{pre}.meterpeak"), "clip": meters.get(f"{pre}.meterclip")})
    return {"tracks": rows}


@tool
def get_meters() -> Dict[str, Any]:
    """Live VU meter values (0..1) for all mirrored tracks and output busses."""
    b = bridge()
    res = b.request("meters")
    out: Dict[str, Any] = {}
    for sid, v in res["slots"]:
        key = b.key_of(sid)
        if not key:
            continue
        pre, _, kind = key.partition(".")
        name = (b.objects.get(pre) or {}).get("title")
        if not name:
            continue
        out.setdefault(name, {})[kind] = v
    return out


@tool
def bank(action: str = "reset", times: int = 1) -> Dict[str, Any]:
    """Move the mirrored mixer bank (for projects with many tracks): next | prev | reset | page (times = page
    number). Movement is verified with Cubase, so the reported indexes are always real."""
    b = bridge()
    cur = b.bank_offset // b.bank_size
    target = {"reset": 0, "next": cur + times, "prev": max(0, cur - times), "page": times}.get(action)
    if target is None:
        raise ValueError("action must be next | prev | reset | page")
    moved = b.move_bank(target)
    return {"bank_page": moved, "bank_offset": b.bank_offset, "requested_page": target,
            "at_last_page": moved < target, "tracks": b.bank_channels()}


# ============================================================ tracks: create / edit

TRACK_TYPES = {
    "audio": ("AddTrack", "Audio"), "audio_mono": ("AddTrack", "Audio Mono"),
    "instrument": ("AddTrack", "Instrument"), "midi": ("AddTrack", "MIDI"), "sampler": ("AddTrack", "Sampler"),
    "fx": ("AddTrack", "FX Channel"), "group": ("AddTrack", "Group Channel"), "folder": ("AddTrack", "Folder"),
    "marker": ("AddTrack", "Marker"), "chord": ("AddTrack", "Chord"), "arranger": ("AddTrack", "Arranger"),
    "tempo": ("AddTrack", "Tempo"), "signature": ("AddTrack", "Signature"), "ruler": ("AddTrack", "Ruler"),
    "transpose": ("AddTrack", "TransposeTrack"), "vca": ("AddTrack", "VCA Fader"), "video": ("AddTrack", "Video"),
    "dialog": ("AddTrack", "OpenDialog"), "from_preset": ("AddTrack", "From Track Presets"),
}


@tool
def create_track(track_type: str = "midi", count: int = 1, after_track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Add tracks. track_type: audio, audio_mono, instrument, midi, sampler, fx, group, folder, marker, chord,
    arranger, tempo, signature, ruler, transpose, vca, video, dialog (opens Add Track dialog), from_preset.
    New tracks are inserted below the selected track (after_track selects first).
    Note: 'instrument' may open Cubase's instrument chooser; 'midi' + load_plugin is fully scriptable."""
    b = bridge()
    if track_type not in TRACK_TYPES:
        raise ValueError(f"track_type must be one of {', '.join(TRACK_TYPES)}")
    if after_track is not None:
        _select(b, after_track)
    before = len(b.bank_channels())
    for _ in range(max(1, int(count))):
        b.command(*TRACK_TYPES[track_type])
        time.sleep(0.35)
    b.wait_for(lambda: len(b.bank_channels()) >= min(b.bank_size, before + count), 2.0)
    return {"created": track_type, "count": count, "selected_track": b.channel_title("sel"),
            "tracks": b.bank_channels()}


@tool
def track_operation(operation: str, track: Optional[TrackRef] = None, confirm: bool = False) -> Dict[str, Any]:
    """Operations on the selected track (track selects first): duplicate | remove (needs confirm=True) |
    remove_empty (needs confirm) | fold | unfold | move_to_folder | lock | freeze | disable | colorize |
    select_next | select_prev | select_all_events | show_automation | hide_automation | rename (starts inline rename)."""
    b = bridge()
    if track is not None:
        _select(b, track)
    ops = {
        "duplicate": ("Project", "Duplicate Tracks"),
        "remove": ("Project", "Remove Selected Tracks"),
        "remove_empty": ("Project", "Remove Empty Tracks"),
        "fold": ("Project", "Folding: Fold Tracks"), "unfold": ("Project", "Folding: Unfold Tracks"),
        "move_to_folder": ("Project", "Folding: Tracks To Folder"),
        "lock": ("Edit", "Lock/Unlock Track"),
        "freeze": ("Edit", "Freeze/Unfreeze Selected Tracks (with Current Settings)"),
        "disable": ("Audio", "Disable/Enable Track"),
        "colorize": ("Project", "Set Track/Event Color"),
        "select_next": ("Project", "Select Track: Next"), "select_prev": ("Project", "Select Track: Prev"),
        "select_all_events": ("Edit", "Select All on Tracks"),
        "show_automation": ("Automation", "Show Used Automation (Selected Tracks)"),
        "hide_automation": ("Automation", "Hide All Automation"),
        "rename": ("Edit", "Rename First Selected Track"),
    }
    if operation not in ops:
        raise ValueError(f"operation must be one of {', '.join(ops)}")
    if operation in ("remove", "remove_empty") and not confirm:
        return {"error": "destructive: call again with confirm=True"}
    cat, cmd = ops[operation]
    if (cat, cmd) not in b.command_ids:
        cat, cmd = _best_command(cmd)
    b.command(cat, cmd)
    time.sleep(0.3)
    return {"done": operation, "selected_track": b.channel_title("sel"), "tracks": b.bank_channels()}


# ============================================================ commands

def _best_command(text: str) -> tuple:
    hits = _search(text, 1)
    if not hits:
        raise CubaseError(f"no command matching '{text}'")
    return hits[0]["category"], hits[0]["command"]


def _search(query: str, limit: int, category: Optional[str] = None) -> List[Dict[str, Any]]:
    q = query.lower().strip()
    words = [w for w in re.split(r"\W+", q) if w]
    scored = []
    for c in commands():
        if category and c["category"].lower() != category.lower():
            continue
        hay = f'{c["category"]} {c["group"]} {c["label"]} {c["command"]}'.lower()
        if not all(w in hay for w in words):
            continue
        score = 0
        lab = c["label"].lower()
        if lab == q or c["command"].lower() == q:
            score += 100
        if lab.startswith(q):
            score += 30
        score -= len(lab) * 0.1
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


@tool
def search_commands(query: str, category: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
    """Search Cubase's 1400+ key commands (menu functions) by words, e.g. 'quantize', 'bounce', 'export mixdown',
    'add marker', 'zoom full', 'render in place'. Returns category/command pairs for execute_command."""
    hits = _search(query, limit, category)
    b = _bridge if (_bridge and _bridge.connected) else None
    for h in hits:
        if b:
            h["available"] = (h["category"], h["command"]) in b.command_ids
    return {"results": hits}


@tool
def list_command_categories() -> Dict[str, int]:
    """All key-command categories with their number of commands."""
    out: Dict[str, int] = {}
    for c in commands():
        out[c["category"]] = out.get(c["category"], 0) + 1
    return dict(sorted(out.items()))


@tool
def execute_command(category: str, command: str, times: int = 1) -> Dict[str, Any]:
    """Run a Cubase key command by its internal category/command (see search_commands), e.g.
    ("Edit","Undo"), ("Quantize Category","Quantize"), ("AddTrack","MIDI"), ("File","Save")."""
    b = bridge()
    if (category, command) not in b.command_ids:
        hits = _search(command, 1, category) or _search(f"{category} {command}", 1)
        if hits and (hits[0]["category"], hits[0]["command"]) in b.command_ids:
            category, command = hits[0]["category"], hits[0]["command"]
    if (category, command) not in b.command_ids:
        return {"error": f"'{category}/{command}' is not bound in the Cubase script.",
                "fix": "If it is a valid Cubase key command (e.g. new in Cubase 16, see Edit > Key Commands), call "
                       "bind_new_command with the exact category and command name, then reload scripts in Cubase."}
    for _ in range(max(1, int(times))):
        b.command(category, command)
        time.sleep(0.08)
    return {"executed": f"{category}/{command}", "times": times}


@tool
def bind_new_command(category: str, command: str) -> Dict[str, Any]:
    """Make any Cubase key command available (e.g. new Cubase 16 functions such as stem separation, Strum,
    Stretch Selection, Instrument Stacks), using the category and the INTERNAL command name as shown in
    Edit > Key Commands. Re-installs the MIDI Remote script; then press 'Reload Scripts' in Cubase's MIDI Remote
    tab and call reconnect."""
    from .install import add_extra_command, reinstall_everywhere
    global _COMMANDS
    lst = add_extra_command(category, command)
    paths = reinstall_everywhere()
    _COMMANDS = None
    return {"bound": f"{category}/{command}", "extra_commands": lst, "script_reinstalled": paths,
            "next": "In Cubase: MIDI Remote tab > Reload Scripts, then call reconnect."}


QUICK = {
    "undo": ("Edit", "Undo"), "redo": ("Edit", "Redo"), "save": ("File", "Save"),
    "save_new_version": ("File", "Save New Version"), "copy": ("Edit", "Copy"), "cut": ("Edit", "Cut"),
    "paste": ("Edit", "Paste"), "delete": ("Edit", "Delete"), "duplicate": ("Edit", "Duplicate"),
    "select_all": ("Edit", "Select All"), "select_none": ("Edit", "Select None"),
    "split_at_cursor": ("Edit", "Split at Cursor"), "glue": ("Edit", "Glue"),
    "quantize": ("Quantize Category", "Quantize"), "undo_quantize": ("Quantize Category", "Undo Quantize"),
    "quantize_ends": ("Quantize Category", "Quantize Ends"), "quantize_lengths": ("Quantize Category", "Quantize Lengths"),
    "legato": ("MIDI", "Legato"), "delete_doubles": ("MIDI", "Delete Doubles"), "dissolve_part": ("MIDI", "Dissolve Part"),
    "bounce_midi": ("MIDI", "Bounce"), "insert_marker": ("Marker", "Insert Marker"),
    "insert_cycle_marker": ("Marker", "Insert Cycle Marker"), "snap_on_off": ("Edit", "Snap On/Off"),
    "zoom_full": ("Zoom", "Zoom Full"), "open_mixer": ("Devices", "Mixer"), "open_editor": ("Edit", "Open"),
    "loop_selection": ("Transport", "Loop Selection"), "locators_to_selection": ("Transport", "Locators to Selection"),
    "export_mixdown": ("File", "Export Audio Mixdown"), "import_midi": ("File", "Import MIDI File"),
    "import_audio": ("File", "Import Audio File"), "mute_events": ("Edit", "Mute Events"),
    "unmute_all": ("Edit", "Unmute All"), "unsolo_all": ("Edit", "Deactivate All Solo"),
    "render_in_place": ("Render in Place", "Render"), "send_all_notes_off": ("MIDI", "Send All Notes Off Message"),
}


def project_action(action: str) -> Dict[str, Any]:
    b = bridge()
    if action not in QUICK:
        raise ValueError(f"action must be one of {', '.join(QUICK)}")
    cat, cmd = QUICK[action]
    if (cat, cmd) not in b.command_ids:
        cat, cmd = _best_command(cmd)
    b.command(cat, cmd)
    time.sleep(0.15)
    return {"done": action, "command": f"{cat}/{cmd}"}


project_action.__doc__ = ("Common edit/project actions. action: " + ", ".join(QUICK) +
                          ". (Edit actions act on the current selection in the project window.)")
project_action = tool(project_action)


@tool
def set_quantize_grid(grid: str = "1/16", triplet: bool = False, dotted: bool = False) -> Dict[str, Any]:
    """Set the project quantize value: 1/1, 1/2, 1/4, 1/8, 1/16, 1/32, 1/64, 1/128 (+ triplet / dotted toggles)."""
    b = bridge()
    m = {"1/1": "1th", "1/2": "2th", "1/4": "4th", "1/8": "8th", "1/16": "16th", "1/32": "32th", "1/64": "64th", "1/128": "128th"}
    if grid not in m:
        raise ValueError(f"grid must be one of {', '.join(m)}")
    b.command("Quantize Category", f"Set Quantize to {m[grid]}")
    if triplet:
        b.command("Quantize Category", "Toggle Quantize Triplet")
    if dotted:
        b.command("Quantize Category", "Toggle Quantize Dotted")
    return {"grid": grid, "triplet_toggled": triplet, "dotted_toggled": dotted}


# ============================================================ channel strip (selected track)

def _target(b: CubaseBridge, track: Optional[TrackRef]) -> Dict[str, Any]:
    if track is not None:
        return _select(b, track)
    return {"name": b.channel_title("sel")}


@tool
def set_eq_band(band: int, track: Optional[TrackRef] = None, on: Optional[bool] = None, gain_db: Optional[float] = None,
                freq_hz: Optional[float] = None, q: Optional[float] = None, filter_type: Optional[float] = None) -> Dict[str, Any]:
    """Channel EQ of a track (selected track if omitted). band 1-4. gain_db (-24..24), freq_hz (20..20000),
    q (display value), filter_type as normalized 0..1 (Cubase cycles through band types)."""
    b = bridge()
    tgt = _target(b, track)
    p = f"sel.eq{int(band)}"
    if on is not None:
        b.set_and_wait(f"{p}.on", 1.0 if on else 0.0)
    elif any(v is not None for v in (gain_db, freq_hz, q)):
        b.set_and_wait(f"{p}.on", 1.0)
    if gain_db is not None:
        b.set_display_target(f"{p}.gain", gain_db, tolerance=0.1)
    if freq_hz is not None:
        b.set_display_target(f"{p}.freq", freq_hz, tolerance=max(1.0, freq_hz * 0.01), iterations=16)
    if q is not None:
        b.set_display_target(f"{p}.q", q, tolerance=0.05)
    if filter_type is not None:
        b.set_and_wait(f"{p}.type", filter_type)
    return {"track": tgt.get("name"), "band": band, "on": _flag(b, f"{p}.on"), "gain": _val(b, f"{p}.gain"),
            "freq": _val(b, f"{p}.freq"), "q": _val(b, f"{p}.q"), "type": _val(b, f"{p}.type")}


@tool
def set_pre_filter(track: Optional[TrackRef] = None, low_cut_hz: Optional[float] = None, low_cut_on: Optional[bool] = None,
                   high_cut_hz: Optional[float] = None, high_cut_on: Optional[bool] = None,
                   gain_db: Optional[float] = None, phase_invert: Optional[bool] = None,
                   bypass: Optional[bool] = None) -> Dict[str, Any]:
    """Pre-section of the channel strip: low/high cut filters, input gain, phase, bypass."""
    b = bridge()
    tgt = _target(b, track)
    if low_cut_on is not None or low_cut_hz is not None:
        b.set_and_wait("sel.pre.lcon", 1.0 if (low_cut_on is not False) else 0.0)
    if low_cut_hz is not None:
        b.set_display_target("sel.pre.lcfreq", low_cut_hz, tolerance=max(1.0, low_cut_hz * 0.01), iterations=16)
    if high_cut_on is not None or high_cut_hz is not None:
        b.set_and_wait("sel.pre.hcon", 1.0 if (high_cut_on is not False) else 0.0)
    if high_cut_hz is not None:
        b.set_display_target("sel.pre.hcfreq", high_cut_hz, tolerance=max(5.0, high_cut_hz * 0.01), iterations=16)
    if gain_db is not None:
        b.set_display_target("sel.pre.gain", gain_db, tolerance=0.1)
    if phase_invert is not None:
        b.set_and_wait("sel.pre.phase", 1.0 if phase_invert else 0.0)
    if bypass is not None:
        b.set_and_wait("sel.pre.bypass", 1.0 if bypass else 0.0)
    return {"track": tgt.get("name"), **{k: _val(b, f"sel.pre.{k}") for k in
                                         ("bypass", "gain", "phase", "lcon", "lcfreq", "hcon", "hcfreq")}}


@tool
def set_strip_module(module: str, track: Optional[TrackRef] = None, active: Optional[bool] = None,
                     bypass: Optional[bool] = None, open_editor: Optional[bool] = None) -> Dict[str, Any]:
    """Channel-strip modules: module = gate | comp | tools | sat | limit. Toggle active / bypass / editor."""
    b = bridge()
    tgt = _target(b, track)
    pre = f"sel.strip.{module}"
    if not b.has(f"{pre}.on"):
        raise ValueError("module must be gate|comp|tools|sat|limit")
    for k, v in (("on", active), ("bypass", bypass), ("edit", open_editor)):
        if v is not None:
            b.set_and_wait(f"{pre}.{k}", 1.0 if v else 0.0)
    return {"track": tgt.get("name"), "module": module, **_plugin(b, pre)}


@tool
def set_insert_state(slot: int, track: Optional[TrackRef] = None, active: Optional[bool] = None,
                     bypass: Optional[bool] = None, open_editor: Optional[bool] = None) -> Dict[str, Any]:
    """Insert slot (0-15) of a track: activate/deactivate, bypass, open/close the plug-in window."""
    b = bridge()
    tgt = _target(b, track)
    pre = f"sel.ins{int(slot)}"
    for k, v in (("on", active), ("bypass", bypass), ("edit", open_editor)):
        if v is not None:
            b.set_and_wait(f"{pre}.{k}", 1.0 if v else 0.0)
    return {"track": tgt.get("name"), "slot": slot, **_plugin(b, pre)}


@tool
def set_instrument_parameter(index: int, value: float, track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Set parameter #index (0-15) of the current parameter bank of the track's instrument (normalized 0..1).
    Use instrument_parameter_bank to page; get_selected_track_info lists names/values."""
    b = bridge()
    tgt = _target(b, track)
    st = b.set_and_wait(f"sel.inst.p{int(index)}", value)
    return {"track": tgt.get("name"), "parameter": st["value_title"], "value": st["value"], "display": b.display(f"sel.inst.p{int(index)}")}


@tool
def instrument_parameter_bank(direction: str = "next") -> Dict[str, Any]:
    """Page the selected instrument's 16-parameter bank: next | prev | reset."""
    b = bridge()
    b.action(f"inst_params_{direction}")
    time.sleep(0.2)
    return {"parameters": [{"index": i, **_val(b, f"sel.inst.p{i}")} for i in range(16) if b.has(f"sel.inst.p{i}")]}


@tool
def set_quick_control(index: int, value: float, track: Optional[TrackRef] = None, focused: bool = False) -> Dict[str, Any]:
    """Set track quick control 0-7 (normalized 0..1). focused=True targets the focused quick controls instead."""
    b = bridge()
    if focused:
        key = f"fqc{int(index)}"
        tgt = {"name": "focused"}
    else:
        tgt = _target(b, track)
        key = f"sel.qc{int(index)}"
    st = b.set_and_wait(key, value)
    return {"track": tgt.get("name"), "quick_control": index, "name": st["value_title"], "display": b.display(key)}


# ============================================================ DirectAccess: plug-ins & host objects

@tool
def explore_host_objects(root: str = "selected", object_id: Optional[int] = None, depth: int = 2,
                         max_nodes: int = 200, track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Browse Cubase's internal object tree (DirectAccess, Cubase 13.0.50+). root: selected (selected track's
    channel: inserts, sends, instrument...), mixconsole, transport, bank0. Each node lists id, title, type,
    number of parameters and children. Use with get_object_parameters / set_object_parameter."""
    b = bridge()
    if track is not None:
        _select(b, track)
    return b.request("da_tree", root=root, object_id=object_id, depth=depth, max_nodes=max_nodes, timeout=10)


@tool
def find_host_objects(query: str = "", root: str = "selected", track: Optional[TrackRef] = None,
                      depth: int = 6, limit: int = 40) -> Dict[str, Any]:
    """Search the DirectAccess object tree by title/type (e.g. a plug-in name like 'Compressor', 'Insert', 'EQ')."""
    b = bridge()
    if track is not None:
        _select(b, track)
    return b.request("da_find", root=root, query=query, depth=depth, limit=limit, timeout=15)


@tool
def get_object_parameters(object_id: int, root: str = "selected", filter: Optional[str] = None,
                          offset: int = 0, limit: int = 64, full: bool = False) -> Dict[str, Any]:
    """List the parameters of a host object (e.g. a plug-in found with find_host_objects): tag, title,
    normalized value, display value + units. filter matches parameter titles (e.g. 'threshold')."""
    return bridge().request("da_params", root=root, object_id=object_id, filter=filter, offset=offset,
                            limit=limit, full=full, timeout=15)


@tool
def set_object_parameter(object_id: int, tag: int, value: Optional[float] = None, display: Optional[str] = None,
                         plain: bool = False, root: str = "selected") -> Dict[str, Any]:
    """Set a host-object/plug-in parameter: value = normalized 0..1 (or plain units when plain=True, API 1.3),
    or display = text as Cubase would display it (e.g. '-12 dB', '2.5 ms')."""
    if value is None and display is None:
        raise ValueError("give value or display")
    return bridge().request("da_set", root=root, object_id=object_id, tag=tag, value=value, display=display, plain=plain)


@tool
def set_plugin_parameter(plugin: str, parameter: str, value: Optional[float] = None, display: Optional[str] = None,
                         track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Convenience: find a plug-in on the (selected) track by name and set one parameter by (partial) title."""
    b = bridge()
    if track is not None:
        _select(b, track)
    found = b.request("da_find", root="selected", query=plugin, depth=6, limit=10, timeout=15)["found"]
    found = [f for f in found if f.get("params")]
    if not found:
        raise CubaseError(f"plug-in '{plugin}' not found on the selected track")
    obj = found[0]
    params = b.request("da_params", root="selected", object_id=obj["id"], filter=parameter, limit=5)["params"]
    if not params:
        raise CubaseError(f"parameter '{parameter}' not found in '{obj.get('title')}'")
    p = params[0]
    res = b.request("da_set", root="selected", object_id=obj["id"], tag=p["tag"], value=value, display=display)
    return {"plugin": obj.get("title"), "object_id": obj["id"], "parameter": p.get("title"), "result": res}


@tool
def list_available_plugins(slot_object_id: int, filter: Optional[str] = None, collection: Optional[int] = None,
                           limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """List plug-ins that can be loaded into a plug-in slot object (DirectAccess plug-in manager, Cubase 15+).
    Find slot ids with explore_host_objects/find_host_objects (insert slots, instrument slot)."""
    return bridge().request("da_plugins", root="selected", object_id=slot_object_id, filter=filter,
                            collection=collection, limit=limit, offset=offset, timeout=15)


@tool
def load_plugin(slot_object_id: int, plugin_uid: Optional[str] = None, plugin_name: Optional[str] = None) -> Dict[str, Any]:
    """Load a plug-in into a slot (Cubase 15+). Give plugin_uid from list_available_plugins, or plugin_name
    to pick the first match."""
    b = bridge()
    if not plugin_uid:
        if not plugin_name:
            raise ValueError("give plugin_uid or plugin_name")
        items = b.request("da_plugins", root="selected", object_id=slot_object_id, filter=plugin_name, limit=5,
                          timeout=15).get("items", [])
        if not items:
            raise CubaseError(f"no plug-in matching '{plugin_name}'")
        plugin_uid = items[0].get("pluginuid") or items[0].get("uid")
        if not plugin_uid:
            raise CubaseError(f"could not read UID from entry {items[0]}")
    res = b.request("da_load_plugin", root="selected", object_id=slot_object_id, uid=plugin_uid)
    return {"loaded": plugin_uid, **res}


@tool
def remove_plugin(slot_object_id: int) -> Dict[str, Any]:
    """Empty a plug-in slot (Cubase 15+)."""
    return bridge().request("da_reset_plugin", root="selected", object_id=slot_object_id)


# ============================================================ control room

@tool
def control_room(level: Optional[float] = None, mute: Optional[bool] = None, dim: Optional[bool] = None,
                 click: Optional[bool] = None, click_level: Optional[float] = None,
                 reference_level: Optional[bool] = None, talkback: Optional[bool] = None) -> Dict[str, Any]:
    """Control Room main monitor (Cubase Pro / Nuendo with Control Room enabled). Levels normalized 0..1."""
    b = bridge()
    if not b.has("cr.level"):
        raise CubaseError("Control Room is not available")
    for key, v in (("cr.level", level), ("cr.clicklevel", click_level)):
        if v is not None:
            b.set_and_wait(key, v)
    for key, v in (("cr.mute", mute), ("cr.dim", dim), ("cr.click", click), ("cr.reflevel", reference_level),
                   ("cr.talkback", talkback)):
        if v is not None:
            b.set_and_wait(key, 1.0 if v else 0.0)
    return {k: _val(b, f"cr.{k}") for k in ("level", "mute", "dim", "click", "clicklevel", "reflevel", "talkback")}


# ============================================================ raw controls

@tool
def list_controls(prefix: str = "") -> Dict[str, Any]:
    """Advanced: list the bridge's raw control keys (e.g. prefix 'sel.' or 'ch0.') with current values."""
    b = bridge()
    out = {}
    for i, k in enumerate(b.keys):
        if b.kinds[i] == "v" and k.startswith(prefix):
            out[k] = _val(b, k)
        if len(out) >= 300:
            break
    return out


@tool
def set_control(key: str, value: float) -> Dict[str, Any]:
    """Advanced: set any raw control (see list_controls) to a normalized 0..1 value."""
    b = bridge()
    st = b.set_and_wait(key, value)
    return {"key": key, **st}


# ============================================================ music generation

def _notes_result(notes: List[Dict], **meta) -> Dict[str, Any]:
    end = max((n["start"] + n["duration"] for n in notes), default=0)
    return {**meta, "length_beats": round(end, 3), "count": len(notes), "notes": notes}


@tool
def music_theory(key: str = "C major", chord: Optional[str] = None, octave: int = 4) -> Dict[str, Any]:
    """Scale notes of a key (e.g. 'A minor', 'D dorian', 'F# harmonic_minor') and diatonic triads/7ths;
    optionally decode a chord symbol."""
    root, scale = M.parse_key(key)
    notes = M.scale_notes(key, octave)
    out: Dict[str, Any] = {"key": key, "scale": scale, "notes": [M.midi_to_note(n) for n in notes], "midi": notes,
                           "available_scales": sorted(M.SCALES)}
    if len(M.SCALES[scale]) == 7:
        nums = ["I", "II", "III", "IV", "V", "VI", "VII"]
        triads = []
        for i in range(7):
            sc = M.SCALES[scale]
            t = [sc[i], sc[(i + 2) % 7] + (12 if (i + 2) >= 7 else 0), sc[(i + 4) % 7] + (12 if (i + 4) >= 7 else 0)]
            third, fifth = t[1] - t[0], t[2] - t[0]
            q = "" if (third, fifth) == (4, 7) else "m" if (third, fifth) == (3, 7) else "dim" if (third, fifth) == (3, 6) else "aug"
            name = M.NOTE_NAMES[(root + sc[i]) % 12] + q
            rn = nums[i] if q in ("", "aug") else nums[i].lower()
            triads.append({"degree": rn + ("o" if q == "dim" else "+" if q == "aug" else ""), "chord": name})
        out["diatonic_chords"] = triads
    if chord:
        ch = M.parse_chord(chord, octave)
        out["chord"] = {"symbol": chord, "notes": [M.midi_to_note(n) for n in ch], "midi": ch}
    return out


@tool
def generate_chord_progression(chords: List[str], key: Optional[str] = None, beats_per_chord: float = 4.0,
                               octave: int = 4, rhythm: str = "sustain", velocity: int = 90,
                               add_bass: bool = False, voice_leading: bool = True) -> Dict[str, Any]:
    """Notes for a chord progression. chords: symbols ['Am','F','C','G'] or roman numerals ['vi','IV','I','V']
    with key. rhythm: sustain | quarters | eighths | offbeat | charleston | pop."""
    notes = M.chord_progression(chords, key, beats_per_chord, octave, velocity,
                                "voice_lead" if voice_leading else "root", rhythm, add_bass)
    return _notes_result(notes, chords=chords)


def generate_drum_pattern(style: str = "rock", bars: int = 2, swing: float = 0.0, humanize: float = 0.0,
                          fill: bool = False, velocity: int = 100, custom_grid: Optional[Dict[str, str]] = None,
                          seed: Optional[int] = None) -> Dict[str, Any]:
    notes = M.drum_pattern(style, bars, swing, velocity, humanize, fill, custom_grid, seed)
    return _notes_result(notes, style=style, drum_map={k: v for k, v in M.DRUMS.items()})


generate_drum_pattern.__doc__ = (
    "GM drum pattern (16 steps/bar). style: " + ", ".join(sorted(M.DRUM_STYLES)) +
    '. custom_grid example: {"kick":"X...X...X...X...","snare":"....X.......X...","closed_hat":"x.x.x.x.x.x.x.x."}'
    " (X accent, x hit, o ghost). Instruments: " + ", ".join(M.DRUMS))
generate_drum_pattern = tool(generate_drum_pattern)


@tool
def generate_bassline(chords: List[str], key: Optional[str] = None, style: str = "root_fifth",
                      beats_per_chord: float = 4.0, octave: int = 2, velocity: int = 100,
                      seed: Optional[int] = None) -> Dict[str, Any]:
    """Bass line following chords. style: root | root_fifth | octave | eighths | syncopated | walking | arp."""
    return _notes_result(M.bassline(chords, key, beats_per_chord, style, octave, velocity, seed), style=style)


@tool
def generate_arpeggio(chords: List[str], key: Optional[str] = None, pattern: str = "up", rate: float = 0.25,
                      beats_per_chord: float = 4.0, octave: int = 4, octaves: int = 2, gate: float = 0.8,
                      velocity: int = 90, seed: Optional[int] = None) -> Dict[str, Any]:
    """Arpeggiate chords. pattern: up | down | updown | random | converge. rate in beats (0.25 = 1/16)."""
    return _notes_result(M.arpeggio(chords, key, beats_per_chord, pattern, rate, octave, octaves, gate, velocity, seed))


@tool
def generate_melody(key: str = "C major", bars: int = 4, chords: Optional[List[str]] = None,
                    beats_per_chord: float = 4.0, octave: int = 5, density: float = 0.65,
                    contour: str = "arch", velocity: int = 95, seed: Optional[int] = None) -> Dict[str, Any]:
    """Scale-aware melody; strong beats land on chord tones when chords are given. contour: arch | rising |
    falling | wave | random. Change seed for variations."""
    return _notes_result(M.melody(key, bars, octave, density, chords, beats_per_chord, contour, velocity, seed))


@tool
def transform_notes(notes: List[Dict[str, Any]], transpose: int = 0, shift_beats: float = 0.0,
                    quantize_grid: Optional[float] = None, quantize_strength: float = 1.0,
                    humanize_timing: float = 0.0, humanize_velocity: int = 0,
                    velocity_scale: float = 1.0, seed: Optional[int] = None, strum_beats: float = 0.0,
                    strum_direction: str = "down", stretch_factor: float = 1.0) -> Dict[str, Any]:
    """Transform a note list: transpose, shift, quantize (grid in beats), humanize, scale velocities,
    strum chords (strum_beats per note, direction down|up|alternate – like Cubase 16 Strum) and stretch
    timing (stretch_factor 2 = half time, 0.5 = double time – like Cubase 16 Stretch Selection)."""
    n = M.normalize_notes(notes)
    if stretch_factor and stretch_factor != 1.0:
        n = M.stretch(n, stretch_factor)
    if strum_beats:
        n = M.strum(n, strum_beats, strum_direction)
    if transpose:
        n = M.transpose(n, transpose)
    if shift_beats:
        n = M.shift(n, shift_beats)
    if quantize_grid:
        n = M.quantize(n, quantize_grid, quantize_strength)
    if humanize_timing or humanize_velocity:
        n = M.humanize(n, humanize_timing, humanize_velocity, seed)
    if velocity_scale != 1.0:
        n = [{**x, "velocity": int(max(1, min(127, x["velocity"] * velocity_scale)))} for x in n]
    return _notes_result(n)


def _export_dir() -> Path:
    d = Path(os.environ.get("NEOCP_EXPORT_DIR", Path.home() / "Documents" / "NeoCP"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@tool
def export_midi_file(tracks: Dict[str, List[Dict[str, Any]]], filename: str = "neocp.mid",
                     tempo: Optional[float] = None, time_signature: str = "4/4") -> Dict[str, Any]:
    """Write a multi-track MIDI file, e.g. {"Drums": [...], "Bass": [...]} (tracks starting with 'drum' use
    channel 10). Saved to ~/Documents/NeoCP (or NEOCP_EXPORT_DIR). Drag it into Cubase or use
    project_action('import_midi')."""
    if tempo is None:
        try:
            tempo = float(bridge().transport.get("tempo") or 120)
        except CubaseError:
            tempo = 120.0
    num, den = (int(x) for x in time_signature.split("/"))
    path = _export_dir() / Path(filename).name
    if path.suffix.lower() not in (".mid", ".midi"):
        path = path.with_suffix(".mid")
    M.write_midi_file(str(path), tracks, tempo, (num, den))
    return {"path": str(path), "tracks": {k: len(v) for k, v in tracks.items()}, "tempo": tempo}


# ============================================================ notes into Cubase

_play_stop = threading.Event()


@tool
def play_notes(notes: List[Dict[str, Any]], track: Optional[TrackRef] = None, bpm: Optional[float] = None,
               channel: int = 0) -> Dict[str, Any]:
    """Audition notes live through the 'NeoCP Notes' port into the selected (monitored/armed) track
    without recording. notes: [{"pitch": 60 | "C4", "start": beats, "duration": beats, "velocity": 1-127}]."""
    from .performer import play_notes as _play
    b = bridge()
    if track is not None:
        _select(b, track)
    tempo = float(bpm or b.transport.get("tempo") or 120)
    _play_stop.clear()
    return _play(b.link, M.normalize_notes(notes), tempo, channel, stop=_play_stop)


@tool
def stop_notes() -> Dict[str, Any]:
    """Stop a running play_notes / record_notes_to_track playback and send all-notes-off."""
    from .performer import all_notes_off
    _play_stop.set()
    if _bridge:
        all_notes_off(_bridge.link)
    return {"stopped": True}


@tool
def record_notes_to_track(track: TrackRef, notes: List[Dict[str, Any]], start_bar: float = 1.0,
                          speed: float = 1.0, quantize_after: bool = False, preroll_beats: float = 4.0,
                          channel: int = 0, beats_per_bar: int = 4) -> Dict[str, Any]:
    """Write notes into a MIDI/instrument track by recording them (the API cannot insert notes directly).
    Steps: select + record-arm the track, put the cursor before start_bar, start recording, play the notes on
    the 'NeoCP Notes' port in sync, stop, restore tempo/arm. speed > 1 temporarily raises the tempo
    (max 300 BPM) to record faster — note positions stay musically identical. quantize_after runs Cubase's
    Quantize on the new part. Requirements: precount off, track input = 'All MIDI Inputs' or 'NeoCP Notes'.
    notes: [{"pitch": 60 | "C4", "start": beats from start_bar, "duration": beats, "velocity": 1-127}]."""
    from .performer import play_notes as _play, all_notes_off
    b = bridge()
    canon = M.normalize_notes(notes)
    if not canon:
        raise ValueError("no notes")
    idx = _resolve_track(b, track)
    pre = b.track_prefix(idx)
    name = b.select_track(idx)
    was_armed = _flag(b, f"{pre}.rec")
    if _flag(b, "transport.play"):
        transport.__wrapped__("stop")
    original_tempo = float(b.transport.get("tempo") or 120)
    rec_tempo = min(300.0, original_tempo * max(0.25, float(speed)))
    pre_beats = preroll_beats if start_bar > 1 or preroll_beats == 0 else 0.0
    start_pos = start_bar - pre_beats / beats_per_bar
    if start_pos < 1:
        pre_beats = (start_bar - 1) * beats_per_bar
        start_pos = 1
    try:
        b.set_and_wait(f"{pre}.rec", 1.0)
        if rec_tempo != original_tempo:
            b.request("tempo", bpm=rec_tempo)
        _locate(b, "cursor", start_pos, beats_per_bar=beats_per_bar)
        time.sleep(0.2)
        latency = float(os.environ.get("NEOCP_RECORD_LATENCY_MS", "0")) / 1000.0
        t_start = time.perf_counter() + latency  # timeline beat 0 = when record is sent (+ latency, may be < 0)
        b.set("transport.record", 1.0)
        b.wait_for(lambda: bool(_flag(b, "transport.record")), 2.0)
        _play_stop.clear()
        stats = _play(b.link, M.shift(canon, pre_beats), rec_tempo, channel, stop=_play_stop, t0=t_start)
        time.sleep(60.0 / rec_tempo * 0.5)
    finally:
        all_notes_off(b.link)
        b.set("transport.stop", 1.0)
        time.sleep(0.1)
        b.set("transport.stop", 0.0)
        b.set("transport.record", 0.0)
        time.sleep(0.2)
        if rec_tempo != original_tempo:
            b.request("tempo", bpm=original_tempo)
        if not was_armed:
            b.set(f"{pre}.rec", 0.0)
    if quantize_after:
        time.sleep(0.2)
        b.command("Quantize Category", "Quantize")
    return {"track": name, "recorded_notes": len(canon), "start_bar": start_bar, "record_tempo": rec_tempo,
            "restored_tempo": original_tempo, "timing": stats, "quantized": quantize_after}


@tool
def create_song_sketch(key: str = "A minor", progression: Optional[List[str]] = None, bars: int = 8,
                       drum_style: str = "pop", bass_style: str = "root_fifth", chord_rhythm: str = "pop",
                       include_melody: bool = True, tempo: Optional[float] = None, seed: Optional[int] = None,
                       record_to_tracks: Optional[Dict[str, TrackRef]] = None, start_bar: float = 1.0,
                       speed: float = 1.0) -> Dict[str, Any]:
    """One-shot arrangement: drums, bass, chords (and melody) in a key, exported as a multi-track MIDI file.
    record_to_tracks optionally maps parts to existing Cubase tracks to record them directly, e.g.
    {"drums": "Drums", "bass": "Bass", "chords": "Keys", "melody": "Lead"}."""
    prog = progression or (["i", "VI", "III", "VII"] if "min" in key.lower() or key.strip().endswith("m") else ["I", "V", "vi", "IV"])
    per = 4.0
    reps = max(1, int(bars * 4 / (per * len(prog))))
    full = list(prog) * reps
    parts = {
        "drums": M.drum_pattern(drum_style, bars, fill=True, seed=seed),
        "bass": M.bassline(full, key, per, bass_style, 2, 100, seed),
        "chords": M.chord_progression(full, key, per, 4, 85, "voice_lead", chord_rhythm),
    }
    if include_melody:
        parts["melody"] = M.melody(key, bars, 5, 0.6, full, per, "arch", 95, seed)
    if tempo:
        try:
            set_tempo.__wrapped__(tempo)
        except Exception:
            pass
    exp = export_midi_file.__wrapped__({k.capitalize(): v for k, v in parts.items()}, "song_sketch.mid", tempo)
    if isinstance(exp, str):
        exp = json.loads(exp)
    result: Dict[str, Any] = {"key": key, "progression": full, "midi_file": exp,
                              "parts": {k: len(v) for k, v in parts.items()}}
    if record_to_tracks:
        rec = {}
        for part, trk in record_to_tracks.items():
            if part in parts:
                r = record_notes_to_track.__wrapped__(trk, parts[part], start_bar, speed)
                rec[part] = json.loads(r) if isinstance(r, str) else r
        result["recorded"] = rec
    return result


# ============================================================ listening ("ears")

_last_listen: Dict[str, Any] = {}
DEFAULT_ANALYSES = ["loudness", "dynamics", "stereo", "spectrum", "tempo", "key", "chords"]


def _bars_from_seconds(sec: float, bpm: float, start_bar: float = 1.0, beats_per_bar: int = 4) -> float:
    return round(start_bar + sec / (60.0 / bpm * beats_per_bar), 2)


def _finish_analysis(audio, sr, source: str, analyses: Optional[List[str]], target_lufs: float,
                     bpm: Optional[float] = None, start_bar: Optional[float] = None, save_as: Optional[str] = None) -> Dict[str, Any]:
    from . import audio as A
    if audio.shape[0] < sr * 0.2 or float(abs(audio).max(initial=0)) < 1e-5:
        return {"source": source, "error": "silence captured: check that Cubase plays and that its output is routed "
                                           "to the listening device (see list_audio_inputs / README)."}
    rep = A.analyze(audio, sr, analyses or DEFAULT_ANALYSES, bpm_hint=bpm, target_lufs=target_lufs)
    rep["source"] = source
    if bpm and start_bar is not None:
        for c in rep.get("chords", []):
            c["bar"] = _bars_from_seconds(c["start_s"], bpm, start_bar)
    path = _export_dir() / (save_as or f"listen_{time.strftime('%Y%m%d_%H%M%S')}.wav")
    try:
        A.save_wav(str(path), audio, sr)
        rep["saved_wav"] = str(path)
    except Exception as e:  # soundfile missing
        rep["saved_wav"] = f"not saved: {e}"
    _last_listen.clear()
    _last_listen.update({"report": rep, "path": rep.get("saved_wav"), "audio": audio, "sr": sr, "bpm": bpm,
                         "start_bar": start_bar if start_bar is not None else 1.0})
    return rep


@tool
def list_audio_inputs() -> Dict[str, Any]:
    """Audio input devices the server can listen to. Devices flagged looks_like_loopback (BlackHole, VB-Cable,
    Stereo Mix...) can carry Cubase's output; microphones are used for transcribe_audio."""
    from . import capture
    devs = capture.list_input_devices()
    try:
        default = capture.resolve_device()[1]
    except Exception as e:
        default = str(e)
    return {"inputs": devs, "default_listen_device": default,
            "hint": "Route a Cubase output bus to a loopback device (macOS: BlackHole; Windows: VB-Cable) "
                    "and set NEOCP_LISTEN_DEVICE to its name."}


@tool
def listen(seconds: float = 8.0, device: Optional[str] = None, analyses: Optional[List[str]] = None,
           target_lufs: float = -14.0) -> Dict[str, Any]:
    """Listen to whatever is coming in right now (does not touch the transport) and analyse it.
    analyses ⊆ loudness, dynamics, stereo, spectrum, tempo, key, chords, melody."""
    from . import capture
    audio, sr, name = capture.record(min(seconds, 120.0), device)
    bpm = None
    if _bridge and _bridge.connected:
        bpm = _bridge.transport.get("tempo")
    return _finish_analysis(audio, sr, f"input '{name}'", analyses, target_lufs, bpm)


@tool
def listen_to_cubase(start_bar: float = 1.0, bars: float = 8.0, device: Optional[str] = None,
                     analyses: Optional[List[str]] = None, target_lufs: float = -14.0,
                     solo_track: Optional[TrackRef] = None, beats_per_bar: int = 4) -> Dict[str, Any]:
    """Play a section of the project and LISTEN to it: moves the cursor to start_bar, plays `bars` bars while
    recording Cubase's output from the loopback device, stops and returns an objective report: LUFS / true
    peak / LRA, dynamics, stereo image, spectral balance and resonances, tempo, key, chords (with bar numbers)
    and mixing suggestions. solo_track listens to a single track (solo is restored afterwards)."""
    from . import capture
    b = bridge()
    bpm = float(b.transport.get("tempo") or 120.0)
    seconds = bars * beats_per_bar * 60.0 / bpm
    if seconds > 300:
        raise ValueError("section too long (max 300 s)")
    solo_pre = None
    if solo_track is not None:
        solo_pre = b.track_prefix(_resolve_track(b, solo_track))
        was_solo = _flag(b, f"{solo_pre}.solo")
        b.set_and_wait(f"{solo_pre}.solo", 1.0)
    if _flag(b, "transport.play"):
        transport.__wrapped__("stop")
    _locate(b, "cursor", start_bar, beats_per_bar=beats_per_bar)
    time.sleep(0.15)
    rec = capture.Recorder(device).start()
    t_rec = time.time()
    t_play = t_rec
    try:
        b.set("transport.play", 1.0)
        b.wait_for(lambda: bool(_flag(b, "transport.play")), 1.5)
        t_play = time.time()
        time.sleep(seconds + 0.25)
    finally:
        b.set("transport.stop", 1.0)
        time.sleep(0.05)
        b.set("transport.stop", 0.0)
        audio, sr = rec.stop()
        if solo_pre is not None and not was_solo:
            b.set(f"{solo_pre}.solo", 0.0)
    # trim the part captured before playback started (keep 50 ms of margin)
    lead = max(0.0, t_play - t_rec - 0.05)
    audio = audio[int(lead * sr):]
    rep = _finish_analysis(audio, sr, f"Cubase bars {start_bar}-{start_bar + bars} via '{rec.device_name}'",
                           analyses, target_lufs, bpm, start_bar)
    rep["project_tempo"] = bpm
    if solo_track is not None:
        rep["soloed_track"] = (b.objects.get(solo_pre) or {}).get("title")
    return rep


@tool
def listen_to_meters(seconds: float = 8.0, start_bar: Optional[float] = None, interval: float = 0.05) -> Dict[str, Any]:
    """Zero-setup listening: plays (from start_bar if given) and samples every track's VU meter, reporting per
    track average/peak level (Cubase normalized meter scale 0..1), clip flags and which tracks dominate the mix.
    Needs no audio routing."""
    b = bridge()
    started = False
    if start_bar is not None:
        _locate(b, "cursor", start_bar)
    if not _flag(b, "transport.play"):
        b.set("transport.play", 1.0)
        started = True
    samples: Dict[str, List[float]] = {}
    clips: Dict[str, bool] = {}
    end = time.time() + min(seconds, 120.0)
    try:
        time.sleep(0.2)
        while time.time() < end:
            for sid, v in b.request("meters")["slots"]:
                key = b.key_of(sid)
                if not key:
                    continue
                pre, _, kind = key.partition(".")
                name = (b.objects.get(pre) or {}).get("title")
                if not name or v is None:
                    continue
                if kind == "meter":
                    samples.setdefault(f"{pre}|{name}", []).append(float(v))
                elif kind == "meterclip" and v > 0.5:
                    clips[name] = True
            time.sleep(interval)
    finally:
        if started:
            b.set("transport.stop", 1.0)
            time.sleep(0.05)
            b.set("transport.stop", 0.0)
    rows = []
    for k, vals in samples.items():
        pre, name = k.split("|", 1)
        arr = sorted(vals)
        rows.append({"track": name, "bus": pre.startswith("io"), "avg": round(sum(arr) / len(arr), 3),
                     "p95": round(arr[int(len(arr) * 0.95) - 1 if len(arr) > 1 else 0], 3), "max": round(arr[-1], 3),
                     "silent": arr[-1] < 0.01, "clipped": clips.get(name, False)})
    rows.sort(key=lambda r: -r["avg"])
    tracks = [r for r in rows if not r["bus"]]
    notes = []
    if tracks:
        top = tracks[0]
        if len(tracks) > 1 and top["avg"] > 1.5 * max(tracks[1]["avg"], 1e-6):
            notes.append(f"'{top['track']}' dominates the mix (avg {top['avg']} vs next {tracks[1]['avg']}).")
        notes += [f"'{r['track']}' clipped." for r in rows if r["clipped"]]
        notes += [f"'{r['track']}' was silent." for r in tracks if r["silent"]]
    return {"samples_per_track": len(next(iter(samples.values()), [])), "levels": rows, "notes": notes}


@tool
def analyze_audio_file(path: str, analyses: Optional[List[str]] = None, target_lufs: float = -14.0,
                       max_seconds: float = 600.0) -> Dict[str, Any]:
    """Analyse an audio file (exported mixdown, stem, reference track, bounce): loudness, true peak, dynamics,
    stereo, spectrum, tempo, key, chords, optional melody transcription and suggestions."""
    from . import audio as A
    p = os.path.expanduser(path)
    data, sr = A.load_file(p, max_seconds)
    rep = A.analyze(data, sr, analyses or DEFAULT_ANALYSES, target_lufs=target_lufs)
    rep["source"] = p
    return rep


@tool
def compare_to_reference(reference_path: str, mix_path: Optional[str] = None) -> Dict[str, Any]:
    """Compare your mix with a reference track (loudness, dynamics, width, band balance) and get EQ/level
    suggestions. mix_path defaults to the last thing heard with listen / listen_to_cubase."""
    from . import audio as A
    if mix_path:
        d, sr = A.load_file(os.path.expanduser(mix_path), 600)
        mine = A.analyze(d, sr, ["loudness", "dynamics", "stereo", "spectrum"])
    elif _last_listen.get("report"):
        mine = _last_listen["report"]
    else:
        raise ValueError("give mix_path or listen to Cubase first")
    d, sr = A.load_file(os.path.expanduser(reference_path), 600)
    ref = A.analyze(d, sr, ["loudness", "dynamics", "stereo", "spectrum"])
    return {"mix_minus_reference": A.compare(mine, ref),
            "mix": {k: mine.get(k) for k in ("loudness", "stereo")},
            "reference": {k: ref.get(k) for k in ("loudness", "stereo")}}


@tool
def transcribe_audio(path: Optional[str] = None, seconds: float = 8.0, device: Optional[str] = None,
                     bpm: Optional[float] = None, quantize: float = 0.25,
                     record_to_track: Optional[TrackRef] = None, start_bar: float = 1.0) -> Dict[str, Any]:
    """Turn a monophonic melody into notes: from an audio file, or by LISTENING to a microphone/input for
    `seconds` (hum, sing, whistle or play). Notes are in beats at the project tempo; optionally record them
    straight into a MIDI/instrument track (record_to_track)."""
    from . import audio as A
    if bpm is None and _bridge and _bridge.connected:
        bpm = _bridge.transport.get("tempo")
    bpm = float(bpm or 120.0)
    if path:
        data, sr = A.load_file(os.path.expanduser(path), 300)
        src = path
    else:
        from . import capture
        data, sr, name = capture.record(min(seconds, 60.0), device, prefer_loopback=False)
        src = f"input '{name}'"
    mono = A.to_float(data).mean(axis=1)
    notes = A.transcribe_melody(mono, sr, bpm, quantize=quantize or None)
    if notes:
        first = notes[0]["start"]
        for n in notes:
            n["start"] = round(n["start"] - first, 4)
    res: Dict[str, Any] = {"source": src, "bpm": bpm, "count": len(notes), "notes": notes}
    if notes:
        res["key_guess"] = A.detect_key(A.chromagram(mono, sr))["key"]
    if record_to_track is not None and notes:
        r = record_notes_to_track.__wrapped__(record_to_track, notes, start_bar)
        res["recorded"] = json.loads(r) if isinstance(r, str) else r
    return res


# ============================================================ polyphonic transcription

@tool
def transcribe_polyphonic(path: Optional[str] = None, start_bar: float = 1.0, bars: Optional[float] = None,
                          bpm: Optional[float] = None, quantize: Optional[float] = 0.25, threshold: float = 0.12,
                          max_polyphony: int = 6, bass_max_note: str = "E3",
                          record_to_tracks: Optional[Dict[str, TrackRef]] = None, record_start_bar: Optional[float] = None,
                          export_midi: bool = True, include_notes: bool = True) -> Dict[str, Any]:
    """Transcribe POLYPHONIC audio (chords, bass, melody together) into MIDI notes, split into parts
    bass / chords / melody, with chord names per half bar.
    Source: an audio file (path), or LISTEN to `bars` bars of the Cubase project from start_bar, or the last
    capture. Then optionally export a MIDI file (one track per part) and/or record parts straight into
    Cubase tracks: record_to_tracks={"bass": "Bass", "chords": "Keys", "melody": "Lead"} (or "all": track).
    threshold: lower = more notes (0.06-0.25). Works best on clean material (piano, guitar, pads, synths);
    loud drums reduce accuracy. Notes are in beats at bpm (project tempo by default)."""
    from . import music as Mu
    from . import transcribe as T
    offset_bar = start_bar
    if path:
        from . import audio as A
        data, sr = A.load_file(os.path.expanduser(path), 600)
        src = path
        offset_bar = 1.0
    else:
        if bars:
            listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness"])
        data, sr, bpm_l, offset_bar, src = _source_audio(None)
        bpm = bpm or bpm_l
    if bpm is None and _bridge and _bridge.connected:
        bpm = _bridge.transport.get("tempo")
    if bpm is None:
        from . import audio as A
        bpm = A.detect_tempo(A.to_float(data).mean(axis=1), sr).get("bpm") or 120.0
    res = T.transcribe(data, sr, float(bpm), quantize, threshold, max_polyphony,
                       bass_max=Mu.note_to_midi(bass_max_note))
    res["source"] = src
    res["source_start_bar"] = offset_bar
    for c in res["chords"]:
        c["bar"] = round(offset_bar + c["beat"] / 4.0, 2)
    parts = res.get("parts", {})
    if export_midi and res["notes"]:
        tracks = {k.capitalize(): v for k, v in parts.items() if v} or {"Transcription": res["notes"]}
        exp = export_midi_file.__wrapped__(tracks, "transcription.mid", float(bpm))
        res["midi_file"] = exp.get("path") if isinstance(exp, dict) else exp
    if record_to_tracks:
        rec = {}
        at = record_start_bar or offset_bar
        for part, trk in record_to_tracks.items():
            notes = res["notes"] if part == "all" else parts.get(part, [])
            if notes:
                r = record_notes_to_track.__wrapped__(trk, notes, at)
                rec[part] = r if isinstance(r, dict) else json.loads(r)
        res["recorded"] = rec
    if not include_notes:
        res.pop("notes", None)
        res.pop("parts", None)
    return res


# ============================================================ drums & genre references

@tool
def transcribe_drums(path: Optional[str] = None, start_bar: float = 1.0, bars: Optional[float] = None,
                     bpm: Optional[float] = None, grid: Optional[float] = 0.25, sensitivity: float = 1.0,
                     record_to_track: Optional[TrackRef] = None, record_start_bar: Optional[float] = None,
                     export_midi: bool = True, include_notes: bool = True) -> Dict[str, Any]:
    """Drum audio -> MIDI (GM map: kick 36, snare 38, closed hat 42, open hat 46). Source: an audio file,
    LISTEN to `bars` bars of the project, or the last capture. Returns a step grid per instrument, swing
    estimate, a MIDI file, and can record the pattern into a drum track (Groove Agent, a GM kit...).
    sensitivity > 1 detects quieter hits. Best on drum loops / drum stems (use Cubase stem separation first)."""
    from . import drums as DR
    if path:
        from . import audio as A
        data, sr = A.load_file(os.path.expanduser(path), 600)
        src, offset_bar = path, 1.0
    else:
        if bars:
            listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness"])
        data, sr, bpm_l, offset_bar, src = _source_audio(None)
        bpm = bpm or bpm_l
    if bpm is None and _bridge and _bridge.connected:
        bpm = _bridge.transport.get("tempo")
    if bpm is None:
        from . import audio as A
        bpm = A.detect_tempo(A.to_float(data).mean(axis=1), sr).get("bpm") or 120.0
    res = DR.transcribe_drums(data, sr, float(bpm), grid, sensitivity)
    res["source"] = src
    if export_midi and res["notes"]:
        exp = export_midi_file.__wrapped__({"Drums": res["notes"]}, "drums_transcription.mid", float(bpm))
        res["midi_file"] = exp.get("path") if isinstance(exp, dict) else exp
    if record_to_track is not None and res["notes"]:
        r = record_notes_to_track.__wrapped__(record_to_track, res["notes"], record_start_bar or offset_bar)
        res["recorded"] = r if isinstance(r, dict) else json.loads(r)
    if not include_notes:
        res.pop("notes", None)
    return res


@tool
def check_mix_against_genre(genre: str, path: Optional[str] = None, start_bar: float = 1.0,
                            bars: Optional[float] = None) -> Dict[str, Any]:
    """Compare the mix with typical ranges of a genre (loudness, true peak, crest factor, spectral tilt,
    sub and air share) and get concrete suggestions. genre: edm, house, techno, trap, hiphop, pop, rock,
    metal, reggaeton, lofi, jazz, acoustic, classical, podcast, streaming. Source: file, LISTEN to `bars`
    bars, or the last capture."""
    from . import audio as A
    from . import genres as G
    if path:
        d, sr = A.load_file(os.path.expanduser(path), 600)
        rep = A.analyze(d, sr, ["loudness", "dynamics", "spectrum"])
    else:
        if bars:
            listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness", "dynamics", "spectrum"])
        if not _last_listen.get("report"):
            raise ValueError("give path or bars (listen first)")
        rep = _last_listen["report"]
        if "spectrum" not in rep or "dynamics" not in rep:
            rep = A.analyze(_last_listen["audio"], _last_listen["sr"], ["loudness", "dynamics", "spectrum"])
    out = G.check(rep, genre)
    out["genre_loudness_target"] = G.PROFILES[out["genre"]]["lufs"]
    return out


@tool
def list_genre_profiles() -> Dict[str, Any]:
    """Approximate mix/master reference ranges per genre used by check_mix_against_genre."""
    from . import genres as G
    return G.PROFILES


# ============================================================ seeing, structure, auto-mix

def _source_audio(path: Optional[str]):
    """Audio of a file, or of the last listen."""
    from . import audio as A
    if path:
        d, sr = A.load_file(os.path.expanduser(path), 900)
        return d, sr, None, 1.0, path
    if "audio" not in _last_listen:
        raise ValueError("nothing heard yet: give a path or call listen_to_cubase first")
    L = _last_listen
    return L["audio"], L["sr"], L.get("bpm"), L.get("start_bar", 1.0), L["report"].get("source")


@tool
def show_spectrogram(path: Optional[str] = None, fmin: float = 30.0, fmax: float = 16000.0,
                     dynamic_range_db: float = 80.0) -> list:
    """SEE the sound: returns a spectrogram image (log frequency, bar grid) of an audio file or of the last
    listen_to_cubase / listen capture, plus a legend. Great for spotting masking, resonances, muddy lows,
    missing highs, clipping bursts, arrangement density and section changes."""
    from . import audio as A
    audio, sr, bpm, start_bar, src = _source_audio(path)
    png, legend = A.spectrogram_png(audio, sr, fmin=fmin, fmax=fmax, dyn_db=dynamic_range_db, bpm=bpm,
                                    start_bar=start_bar)
    legend["source"] = src
    return [Image(data=png, format="png"), json.dumps(legend, ensure_ascii=False)]


@tool
def analyze_song_structure(path: Optional[str] = None, start_bar: float = 1.0, bars: Optional[float] = None,
                           bpm: Optional[float] = None, mark_in_project: bool = False,
                           beats_per_bar: int = 4) -> Dict[str, Any]:
    """Detect the song form (intro / verse / chorus / bridge / outro, repeats labelled A, B, C...) with bar
    numbers. Source: an audio file, the last capture, or (bars given) LISTEN to that many bars of the project.
    mark_in_project inserts a Cubase marker at the start of every section."""
    from . import audio as A
    if bars:
        listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness"])
        audio, sr, bpm_l, start_bar, src = _source_audio(None)
    else:
        audio, sr, bpm_l, sb, src = _source_audio(path)
        if not path:
            start_bar = sb
    bpm = bpm or bpm_l
    if bpm is None and _bridge and _bridge.connected and not path:
        bpm = _bridge.transport.get("tempo")
    res = A.song_structure(audio, sr, bpm, beats_per_bar, start_bar)
    res["source"] = src
    res.pop("novelty", None)
    if mark_in_project:
        b = bridge()
        for sec in res["sections"]:
            _locate(b, "cursor", sec["start_bar"], beats_per_bar=beats_per_bar)
            time.sleep(0.08)
            b.command("Marker", "Insert Marker")
            time.sleep(0.08)
        res["markers_inserted"] = len(res["sections"])
        res["note"] = "The MIDI Remote API cannot name markers; names are listed here (rename them in the Marker window)."
    return res


def _fader_db(b: CubaseBridge, key: str) -> Optional[float]:
    return _parse_number(b.display(key))


def _nudge_fader(b: CubaseBridge, key: str, delta_db: float, max_db: float = 6.0) -> Dict[str, Any]:
    cur = _fader_db(b, key)
    if cur is None or cur == float("-inf"):
        return {"skipped": "fader at -inf or unreadable"}
    target = max(-60.0, min(max_db, cur + delta_db))
    r = _set_level(b, key, target, None)
    return {"from_db": cur, "to_db": _fader_db(b, key), "requested_db": round(target, 2), "display": r.get("display")}


@tool
def gain_stage_tracks(target_lufs: float = -20.0, tracks: Optional[List[TrackRef]] = None, start_bar: float = 1.0,
                      bars: float = 4.0, tolerance_lu: float = 1.0, apply: bool = True) -> Dict[str, Any]:
    """Listen to each track SOLO over a section, measure its loudness (LUFS) and move its fader so every track
    sits near target_lufs (classic gain staging; then balance by ear). apply=False only reports."""
    b = bridge()
    idxs = [_resolve_track(b, t) for t in tracks] if tracks else [c["index"] for c in b.bank_channels()]
    out = []
    for idx in idxs:
        pre = b.track_prefix(idx)
        name = b.channel_title(pre)
        rep = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness"], solo_track=idx)
        lufs = (rep.get("loudness") or {}).get("integrated_lufs")
        row: Dict[str, Any] = {"track": name, "measured_lufs": lufs}
        if lufs is None or lufs <= -69:
            row["skipped"] = "silent in this section"
        else:
            delta = target_lufs - lufs
            row["delta_db"] = round(delta, 2)
            if apply and abs(delta) > tolerance_lu:
                row["fader"] = _nudge_fader(b, f"{pre}.volume", delta)
        out.append(row)
    return {"target_lufs": target_lufs, "section": [start_bar, start_bar + bars], "tracks": out}


@tool
def fix_resonances(track: TrackRef, start_bar: float = 1.0, bars: float = 4.0, max_cuts: int = 2,
                   threshold_db: float = 8.0, max_cut_db: float = 6.0, q: float = 6.0, apply: bool = True,
                   verify: bool = True) -> Dict[str, Any]:
    """Listen to a track solo, find narrow resonances, cut them with the channel EQ (bands 2 and 3, peak
    filters), then listen again to verify. Cut depth = min(max_cut_db, excess/2)."""
    b = bridge()
    idx = _resolve_track(b, track)
    before = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["spectrum", "loudness"], solo_track=idx)
    if "error" in before:
        return before
    res = [r for r in before["spectrum"]["resonances"] if r["excess_db"] >= threshold_db][:max(1, min(2, max_cuts))]
    applied = []
    if apply:
        for band, r in zip((2, 3), res):
            cut = -min(max_cut_db, r["excess_db"] / 2)
            eq = set_eq_band.__wrapped__(band, track=idx, on=True, gain_db=round(cut, 1), freq_hz=r["freq_hz"], q=q)
            applied.append({"band": band, "freq_hz": r["freq_hz"], "note": r["note"], "gain_db": round(cut, 1), "eq": eq})
    result: Dict[str, Any] = {"track": b.channel_title(b.track_prefix(idx)), "resonances_found": res, "eq_cuts": applied}
    if verify and applied:
        after = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["spectrum", "loudness"], solo_track=idx)
        result["resonances_after"] = (after.get("spectrum") or {}).get("resonances")
        result["loudness_before_after"] = [(before.get("loudness") or {}).get("integrated_lufs"),
                                           (after.get("loudness") or {}).get("integrated_lufs")]
    if not res:
        result["note"] = f"no resonance above {threshold_db} dB"
    return result


def _output_bus_prefix(b: CubaseBridge, bus: Optional[str]) -> str:
    cands = [(f"io{i}", (b.objects.get(f"io{i}") or {}).get("title")) for i in range(8)]
    cands = [(p, n) for p, n in cands if n]
    if bus:
        for p, n in cands:
            if bus.lower() in n.lower():
                return p
        raise CubaseError(f"no bus named '{bus}' (busses: {[n for _, n in cands]})")
    for p, n in cands:
        if "stereo out" in n.lower() or "main" in n.lower() or "out" in n.lower():
            return p
    if cands:
        return cands[-1][0]
    raise CubaseError("no output bus visible")


@tool
def match_loudness(target_lufs: float = -14.0, start_bar: float = 1.0, bars: float = 8.0, bus: Optional[str] = None,
                   max_passes: int = 3, tolerance_lu: float = 0.5, max_gain_db: float = 6.0) -> Dict[str, Any]:
    """Listen to the mix and move the output bus fader until the integrated loudness reaches target_lufs
    (e.g. -14 streaming, -16 podcasts/Apple, -9 club). Warns when true peak exceeds -1 dBTP (add a limiter)."""
    b = bridge()
    pre = _output_bus_prefix(b, bus)
    passes = []
    for _ in range(max(1, max_passes)):
        rep = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars, analyses=["loudness"])
        L = rep.get("loudness") or {}
        lufs = L.get("integrated_lufs")
        row = {"lufs": lufs, "true_peak_dbtp": L.get("true_peak_dbtp"), "fader_db": _fader_db(b, f"{pre}.volume")}
        passes.append(row)
        if lufs is None or lufs <= -69 or abs(target_lufs - lufs) <= tolerance_lu:
            break
        row["adjust"] = _nudge_fader(b, f"{pre}.volume", target_lufs - lufs, max_db=max_gain_db)
    last = passes[-1]
    out = {"bus": (b.objects.get(pre) or {}).get("title"), "target_lufs": target_lufs, "passes": passes}
    if last.get("true_peak_dbtp") is not None and last["true_peak_dbtp"] > -1:
        out["warning"] = "true peak above -1 dBTP: insert a limiter (ceiling -1 dB) on the output bus before raising loudness"
    return out


@tool
def auto_mix_pass(start_bar: float = 1.0, bars: float = 8.0, track_target_lufs: float = -20.0,
                  master_target_lufs: float = -14.0, fix_resonances_on: Optional[List[TrackRef]] = None,
                  do_gain_staging: bool = True, do_master: bool = True) -> Dict[str, Any]:
    """Closed-loop mix assistant: (1) listen to the mix; (2) gain-stage every track solo; (3) notch resonances
    on chosen tracks; (4) bring the master to target loudness; (5) listen again and report before/after with
    remaining suggestions. Every change is a normal Cubase edit (undo with project_action('undo') per step)."""
    steps: Dict[str, Any] = {}
    before = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars)
    steps["before"] = {k: before.get(k) for k in ("loudness", "stereo", "suggestions")}
    if do_gain_staging:
        steps["gain_staging"] = gain_stage_tracks.__wrapped__(track_target_lufs, None, start_bar, min(bars, 4.0))
    if fix_resonances_on:
        steps["resonances"] = [fix_resonances.__wrapped__(t, start_bar, min(bars, 4.0), verify=False)
                               for t in fix_resonances_on]
    if do_master:
        steps["master"] = match_loudness.__wrapped__(master_target_lufs, start_bar, bars)
    after = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=bars)
    steps["after"] = {k: after.get(k) for k in ("loudness", "stereo", "spectrum", "suggestions")}
    return steps


# ============================================================ instruments & effects by name

def _rank_plugins(items: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = query.lower().strip()
    words = [w for w in re.split(r"\W+", q) if w]

    def score(it: Dict[str, Any]) -> float:
        name = str(it.get("name", "")).lower()
        sc = 0.0
        if name == q:
            sc += 100
        if name.startswith(q):
            sc += 50
        sc += 15 * sum(1 for w in words if w in name)
        sc += 5 * sum(1 for w in words if w in str(it.get("vendor", "")).lower())
        return sc - len(name) * 0.05
    return sorted(items, key=score, reverse=True)


def _plugin_slots(b: CubaseBridge, track: Optional[TrackRef]) -> List[Dict[str, Any]]:
    if track is not None:
        _select(b, track)
    slots = b.request("da_plugin_slots", root="selected", depth=5, timeout=15)["slots"]
    for sl in slots:
        kinds = [k.lower() for k in sl.get("entry_kinds", [])]
        sl["kind"] = "instrument" if "instrument" in kinds and "fx" not in kinds else "effect"
    return slots


@tool
def list_plugin_slots(track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Plug-in slots of a track (Cubase 15+): instrument slot and insert slots with their object ids and the
    plug-in currently loaded (title)."""
    b = bridge()
    slots = _plugin_slots(b, track)
    return {"track": b.channel_title("sel"),
            "slots": [{"object_id": s["id"], "kind": s["kind"], "title": s.get("title") or None,
                       "type": s.get("type")} for s in slots]}


@tool
def search_plugins(query: str = "", kind: Optional[str] = None, track: Optional[TrackRef] = None,
                   limit: int = 30) -> Dict[str, Any]:
    """Search installed plug-ins by name / vendor / category (Cubase 15+). kind: instrument | effect.
    Examples: 'piano', 'halion', 'reverb', 'compressor', 'Arturia'."""
    b = bridge()
    slots = _plugin_slots(b, track)
    if not slots:
        raise CubaseError("no plug-in slot found on the selected track (select an audio/instrument track)")
    want = (kind or "").lower()
    slot = next((s for s in slots if want and s["kind"] == want), slots[0])
    sub = "instrument" if want == "instrument" else ("fx" if want == "effect" else None)
    items = b.request("da_search_plugins", root="selected", object_id=slot["id"], query=query, kind=sub,
                      limit=200, timeout=15)["items"]
    if not items and query:  # query may be a category word ('piano', 'reverb')
        items = b.request("da_search_plugins", root="selected", object_id=slot["id"], query="", kind=sub,
                          limit=1000, timeout=20)["items"]
        items = [i for i in items if query.lower() in json.dumps(i).lower()]
    return {"results": _rank_plugins(items, query)[:limit] if query else items[:limit]}


def _load_into(b: CubaseBridge, slot: Dict[str, Any], name: str, kind_filter: Optional[str]) -> Dict[str, Any]:
    items = b.request("da_search_plugins", root="selected", object_id=slot["id"], query=name, kind=kind_filter,
                      limit=100, timeout=15)["items"]
    if not items:
        words = [w for w in re.split(r"\W+", name.lower()) if w]
        allv = b.request("da_search_plugins", root="selected", object_id=slot["id"], query="", kind=kind_filter,
                         limit=2000, timeout=20)["items"]
        items = [i for i in allv if any(w in json.dumps(i).lower() for w in words)]
    if not items:
        raise CubaseError(f"no plug-in matching '{name}' (try search_plugins)")
    best = _rank_plugins(items, name)[0]
    res = b.request("da_load_plugin", root="selected", object_id=slot["id"], uid=best["uid"], dont_ask=True)
    return {"loaded": best["name"], "vendor": best.get("vendor"), "uid": best["uid"], "ok": res.get("ok"),
            "slot_object_id": slot["id"], "alternatives": [i["name"] for i in _rank_plugins(items, name)[1:6]]}


@tool
def load_instrument(name: str, track: Optional[TrackRef] = None) -> Dict[str, Any]:
    """Load a VST instrument by name into an instrument track (Cubase 15+), e.g. 'HALion Sonic', 'Retrologue',
    'Groove Agent', 'Padshop', or a category word like 'piano' / 'drum'. Replaces the current instrument."""
    b = bridge()
    slots = [s for s in _plugin_slots(b, track) if s["kind"] == "instrument"]
    if not slots:
        raise CubaseError("this track has no instrument slot: use an instrument track (create_instrument_track)")
    r = _load_into(b, slots[0], name, "instrument")
    b.wait_for(lambda: ((b.objects.get("sel.inst") or {}).get("plugin") or {}).get("name") == r["loaded"], 3.0)
    r["track"] = b.channel_title("sel")
    r["instrument_now"] = ((b.objects.get("sel.inst") or {}).get("plugin") or {}).get("name")
    return r


@tool
def load_effect(name: str, track: Optional[TrackRef] = None, slot: Optional[int] = None) -> Dict[str, Any]:
    """Insert an effect by name (Cubase 15+), e.g. 'Compressor', 'REVerence', 'FabFilter Pro-Q', 'delay'.
    slot = insert position (0-based); default = first empty insert slot."""
    b = bridge()
    fx = [s for s in _plugin_slots(b, track) if s["kind"] == "effect"]
    if not fx:
        raise CubaseError("no insert slots found on this track")
    if slot is not None:
        if not 0 <= slot < len(fx):
            raise ValueError(f"slot must be 0..{len(fx) - 1}")
        target = fx[slot]
    else:
        empty = [s for s in fx if not s.get("title") or str(s.get("title")).lower() in ("no effect", "empty", "none")]
        target = (empty or fx)[0]
    r = _load_into(b, target, name, "fx")
    r["track"] = b.channel_title("sel")
    r["slot"] = fx.index(target)
    return r


@tool
def create_instrument_track(instrument: str, wait_seconds: float = 60.0) -> Dict[str, Any]:
    """Add an instrument track and load an instrument by name into it (Cubase 15+). Cubase may show its
    'Add Track' dialog: confirm it (any or no instrument) and NeoCP loads the requested one automatically."""
    b = bridge()
    before = {c["name"] for c in b.bank_channels()}
    count = len(before)
    b.command("AddTrack", "Instrument")
    ok = b.wait_for(lambda: len(b.bank_channels()) > count, wait_seconds)
    if not ok:
        raise CubaseError("no new track appeared (was the Add Track dialog cancelled?)")
    time.sleep(0.6)
    new = [c for c in b.bank_channels() if c["name"] not in before]
    if new:
        b.select_track(new[-1]["index"])
    r = load_instrument.__wrapped__(instrument)
    r["new_track"] = new[-1]["name"] if new else b.channel_title("sel")
    return r


# ============================================================ automation

_calib: Dict[str, List[tuple]] = {}


def _calibrate(b: CubaseBridge, key: str, kind: str, points: int = 36) -> List[tuple]:
    """Sweep a fader once to learn Cubase's normalized->dB curve (cached per kind)."""
    if kind in _calib:
        return _calib[kind]
    orig = b.value(key)
    table = []
    for i in range(points + 1):
        x = i / points
        st = b.set_and_wait(key, x, timeout=0.4)
        v = _parse_number(b.display(key))
        if v is not None and v != float("-inf"):
            table.append((v, x))
    if orig is not None:
        b.set_and_wait(key, orig)
    table.sort()
    clean = []
    for v, x in table:  # keep strictly increasing
        if not clean or (v > clean[-1][0] and x > clean[-1][1]):
            clean.append((v, x))
    if len(clean) < 3:
        raise CubaseError(f"could not calibrate {key}: display values not readable")
    _calib[kind] = clean
    return clean


def _db_to_norm(table: List[tuple], db: float) -> float:
    if db <= table[0][0]:
        return 0.0 if db < table[0][0] - 1 else table[0][1]
    for (d0, x0), (d1, x1) in zip(table, table[1:]):
        if d0 <= db <= d1:
            return x0 + (x1 - x0) * (db - d0) / (d1 - d0)
    return table[-1][1]


def _automation_target(b: CubaseBridge, idx: int, target: str) -> Dict[str, Any]:
    t = target.strip().lower()
    pre = b.track_prefix(idx)
    m = re.fullmatch(r"send\s*(\d+)", t)
    if t == "volume":
        return {"kind": "slot", "key": f"{pre}.volume", "unit": "db", "cal": "volume"}
    if t == "pan":
        return {"kind": "slot", "key": f"{pre}.pan", "unit": "pan"}
    if t == "mute":
        return {"kind": "slot", "key": f"{pre}.mute", "unit": "toggle"}
    if m:  # 0-based like set_send / get_track_info
        key = f"{pre}.send{int(m.group(1))}.level"
        if not b.has(key):
            raise ValueError(f"send slot {m.group(1)} does not exist (0..{int(b.info.get('send_slots', 8)) - 1})")
        return {"kind": "slot", "key": key, "unit": "db", "cal": "send"}
    m = re.fullmatch(r"(qc|quick_control)\s*(\d+)", t)
    if m:
        b.select_track(idx)
        key = f"sel.qc{int(m.group(2))}"
        if not b.has(key):
            raise ValueError("quick control index must be 0..7")
        return {"kind": "slot", "key": key, "unit": "normalized"}
    m = re.fullmatch(r"instrument[:\s]*(\d+)", t)
    if m:
        b.select_track(idx)
        key = f"sel.inst.p{int(m.group(1))}"
        if not b.has(key):
            raise ValueError("instrument parameter index must be 0..15")
        return {"kind": "slot", "key": key, "unit": "normalized"}
    if t.startswith("plugin:"):
        plug, _, param = target.split(":", 1)[1].partition("/")
        b.select_track(idx)
        found = [f for f in b.request("da_find", root="selected", query=plug.strip(), depth=6, limit=10,
                                      timeout=15)["found"] if f.get("params")]
        if not found:
            raise CubaseError(f"plug-in '{plug}' not found on the track")
        prm = b.request("da_params", root="selected", object_id=found[0]["id"], filter=param.strip(), limit=3)["params"]
        if not prm:
            raise CubaseError(f"parameter '{param}' not found in '{found[0].get('title')}'")
        return {"kind": "da", "object_id": found[0]["id"], "tag": prm[0]["tag"], "unit": "normalized",
                "label": f"{found[0].get('title')} / {prm[0].get('title')}"}
    if b.has(target):
        return {"kind": "slot", "key": target, "unit": "normalized"}
    raise ValueError("target must be volume | pan | mute | sendN | qcN | instrument:N | plugin:<name>/<param> | raw control key")


@tool
def write_automation(track: TrackRef, target: str = "volume", start_bar: float = 1.0, bars: float = 4.0,
                     shape: Optional[str] = "ramp", points: Optional[List[Dict[str, Any]]] = None,
                     start: Optional[float] = None, end: Optional[float] = None, center: Optional[float] = None,
                     depth: Optional[float] = None, rate_beats: float = 1.0, waveform: str = "sine",
                     curve: str = "linear", steps: Optional[List[float]] = None, attack: float = 0.6,
                     resolution_hz: float = 40.0, preroll_beats: float = 4.0, dry_run: bool = False,
                     restore_touch_mode: bool = True, beats_per_bar: int = 4) -> Dict[str, Any]:
    """Write automation by performing it in real time (Auto-Latch), like moving a fader while Cubase plays.

    target: volume (dB) | pan (-1..1) | mute (0/1) | sendN (dB, N = 0-based send slot like set_send) |
            qcN (0..1, N = 0-7) | instrument:N (0..1, N = 0-15) |
            plugin:<plug-in>/<parameter> (0..1, e.g. "plugin:Compressor/Threshold") | raw control key.
    Either points=[{"bar": 9, "value": -20}, {"bar": 13, "value": 0}] (or "beat" relative to start) or a shape:
      ramp (start->end, curve linear|exp|log|s), fade_in, fade_out, lfo (center, depth, rate_beats, waveform
      sine|triangle|square|saw), duck (sidechain pump: depth, rate_beats, attack=recovery fraction),
      swell (per rate_beats), steps (trance gate: steps=[...] per rate_beats), hold.
    dry_run=True only returns a preview. The track is left in Read mode so you hear the result."""
    from . import automation as AU
    if points:
        crv = AU.points_curve(points, beats_per_bar, start_bar, curve)
    else:
        unit_hint = "db" if target.lower() in ("volume",) or target.lower().startswith("send") else (
            "pan" if target.lower() == "pan" else "normalized")
        crv = AU.shape_curve(shape or "ramp", bars * beats_per_bar, start, end, center, depth, rate_beats,
                             waveform, curve, steps, attack, unit_hint)
    length = bars * beats_per_bar
    preview = AU.sample(crv, length, max(0.25, length / 32))
    if dry_run:
        return {"target": target, "start_bar": start_bar, "bars": bars, "preview": preview}
    b = bridge()
    idx = _resolve_track(b, track)
    tgt = _automation_target(b, idx, target)
    if tgt["unit"] == "db":
        table = _calibrate(b, tgt["key"], tgt["cal"])
        to_norm = lambda v: _db_to_norm(table, v)  # noqa: E731
    elif tgt["unit"] == "pan":
        to_norm = lambda v: (max(-1.0, min(1.0, v)) + 1) / 2  # noqa: E731
    elif tgt["unit"] == "toggle":
        to_norm = lambda v: 1.0 if v >= 0.5 else 0.0  # noqa: E731
    else:
        to_norm = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    pre = b.track_prefix(idx)
    name = b.channel_title(pre)
    if _flag(b, "transport.play"):
        transport.__wrapped__("stop")
    bpm = float(b.transport.get("tempo") or 120.0)
    pre_beats = min(preroll_beats, (start_bar - 1) * beats_per_bar)
    sent = 0
    max_late = 0.0
    b.command("Automation", "Automation Mode - Auto-Latch")
    first = to_norm(crv(0.0))
    try:
        b.set_and_wait(f"{pre}.awrite", 1.0)
        _locate(b, "cursor", start_bar - pre_beats / beats_per_bar, beats_per_bar=beats_per_bar)
        time.sleep(0.15)
        latency = float(os.environ.get("NEOCP_RECORD_LATENCY_MS", "0")) / 1000.0
        t0 = time.perf_counter() + latency  # beat 0 of the pre-roll = when play is sent (+ latency)
        b.set("transport.play", 1.0)
        b.wait_for(lambda: bool(_flag(b, "transport.play")), 1.5)
        frame = 1.0 / max(5.0, min(100.0, resolution_hz if tgt["kind"] == "slot" else min(resolution_hz, 25.0)))
        last = None
        next_t = t0
        while True:
            now = time.perf_counter()
            beat = (now - t0) * bpm / 60.0 - pre_beats
            if beat > length:
                break
            if beat >= 0:
                v = first if beat == 0 else to_norm(crv(beat))
                if last is None or abs(v - last) >= 1.0 / 4000:
                    if tgt["kind"] == "slot":
                        b.set(tgt["key"], v)
                    else:
                        b.send_nowait("da_set_fast", root="selected", object_id=tgt["object_id"], tag=tgt["tag"], value=v)
                    last = v
                    sent += 1
                max_late = max(max_late, now - next_t)
            next_t += frame
            sl = next_t - time.perf_counter()
            if sl > 0:
                time.sleep(sl)
        # land exactly on the final value, then stop writing
        endv = to_norm(crv(length))
        if tgt["kind"] == "slot":
            b.set(tgt["key"], endv)
        else:
            b.send_nowait("da_set_fast", root="selected", object_id=tgt["object_id"], tag=tgt["tag"], value=endv)
        b.command("Automation", "Punch Out of Latch Automation")
        time.sleep(0.05)
    finally:
        b.set("transport.stop", 1.0)
        time.sleep(0.05)
        b.set("transport.stop", 0.0)
        b.set(f"{pre}.awrite", 0.0)
        b.set(f"{pre}.aread", 1.0)
        if restore_touch_mode:
            b.command("Automation", "Automation Mode - Touch")
    return {"track": name, "target": tgt.get("label", target), "start_bar": start_bar, "bars": bars,
            "values_sent": sent, "max_late_ms": round(max_late * 1000, 1), "tempo": bpm,
            "preview": preview[:: max(1, len(preview) // 12)],
            "note": "Track left in Read mode. Undo with project_action('undo') or clear_automation."}


@tool
def clear_automation(track: TrackRef, confirm: bool = False) -> Dict[str, Any]:
    """Delete ALL automation of a track (destructive: needs confirm=True)."""
    if not confirm:
        return {"error": "destructive: call again with confirm=True"}
    b = bridge()
    sel = _select(b, track)
    b.command("Automation", "Delete Automation of Selected Tracks")
    return {"cleared": sel["name"]}


@tool
def set_automation_mode(mode: str = "touch") -> Dict[str, Any]:
    """Global automation mode: touch | latch (Auto-Latch) | cross_over | trim."""
    cmds = {"touch": "Automation Mode - Touch", "latch": "Automation Mode - Auto-Latch",
            "auto_latch": "Automation Mode - Auto-Latch", "cross_over": "Automation Mode - Cross-Over",
            "trim": "Automation Mode - Trim"}
    if mode not in cmds:
        raise ValueError(f"mode must be one of {', '.join(cmds)}")
    bridge().command("Automation", cmds[mode])
    return {"mode": mode}


# ============================================================ end-to-end producer

GENRE_PRESETS: Dict[str, Dict[str, Any]] = {
    "pop":       {"tempo": 112, "drums": "pop", "bass": "root_fifth", "chords": "pop", "prog_major": ["I", "V", "vi", "IV"], "prog_minor": ["i", "VI", "III", "VII"]},
    "house":     {"tempo": 124, "drums": "house", "bass": "octave", "chords": "offbeat", "prog_major": ["vi", "IV", "I", "V"], "prog_minor": ["i", "VII", "VI", "VII"]},
    "techno":    {"tempo": 130, "drums": "techno", "bass": "eighths", "chords": "offbeat", "prog_major": ["I", "bVII", "I", "bVII"], "prog_minor": ["i", "i", "VI", "VII"]},
    "trap":      {"tempo": 140, "drums": "trap", "bass": "root", "chords": "sustain", "prog_major": ["vi", "IV", "I", "V"], "prog_minor": ["i", "VI", "VII", "v"]},
    "hiphop":    {"tempo": 90, "drums": "boom_bap", "bass": "syncopated", "chords": "sustain", "prog_major": ["ii7", "V7", "Imaj7", "vi7"], "prog_minor": ["i7", "iv7", "VI", "v7"]},
    "lofi":      {"tempo": 82, "drums": "boom_bap", "bass": "walking", "chords": "charleston", "prog_major": ["ii7", "V7", "Imaj7", "vi7"], "prog_minor": ["i7", "iv7", "VII", "III"]},
    "rock":      {"tempo": 120, "drums": "rock", "bass": "eighths", "chords": "eighths", "prog_major": ["I", "IV", "V", "IV"], "prog_minor": ["i", "VII", "VI", "VII"]},
    "reggaeton": {"tempo": 95, "drums": "reggaeton", "bass": "syncopated", "chords": "offbeat", "prog_major": ["vi", "IV", "I", "V"], "prog_minor": ["i", "VI", "III", "VII"]},
    "funk":      {"tempo": 105, "drums": "funk", "bass": "syncopated", "chords": "offbeat", "prog_major": ["I7", "IV7", "I7", "V7"], "prog_minor": ["i7", "iv7", "i7", "v7"]},
    "dnb":       {"tempo": 174, "drums": "dnb", "bass": "root", "chords": "sustain", "prog_major": ["vi", "IV", "I", "V"], "prog_minor": ["i", "VI", "III", "VII"]},
    "disco":     {"tempo": 118, "drums": "disco", "bass": "octave", "chords": "offbeat", "prog_major": ["ii7", "V7", "Imaj7", "vi7"], "prog_minor": ["i7", "iv7", "VII", "III"]},
    "afrobeat":  {"tempo": 108, "drums": "afrobeat", "bass": "syncopated", "chords": "offbeat", "prog_major": ["I", "IV", "vi", "V"], "prog_minor": ["i", "iv", "VII", "III"]},
}
DEFAULT_INSTRUMENTS = {"drums": "Groove Agent", "bass": "Retrologue", "chords": "HALion Sonic", "melody": "Retrologue"}


def produce_song(genre: str = "pop", key: str = "A minor", bars: int = 8, tempo: Optional[float] = None,
                 progression: Optional[List[str]] = None, parts: Optional[List[str]] = None,
                 tracks: Optional[Dict[str, TrackRef]] = None, instruments: Optional[Dict[str, str]] = None,
                 create_tracks: bool = True, start_bar: float = 1.0, speed: float = 2.0, seed: Optional[int] = None,
                 listen_after: bool = True) -> Dict[str, Any]:
    g = genre.lower()
    if g not in GENRE_PRESETS:
        raise ValueError(f"genre must be one of {', '.join(GENRE_PRESETS)}")
    P = GENRE_PRESETS[g]
    b = bridge()
    parts = parts or ["drums", "bass", "chords", "melody"]
    minor = "min" in key.lower() or key.strip().endswith("m")
    prog = progression or (P["prog_minor"] if minor else P["prog_major"])
    bpm = float(tempo or P["tempo"])
    log: Dict[str, Any] = {"genre": g, "key": key, "tempo": bpm, "progression": prog, "steps": []}
    set_tempo.__wrapped__(bpm)
    reps = max(1, int(bars * 4 / (4 * len(prog))))
    full = list(prog) * reps
    notes = {
        "drums": M.drum_pattern(P["drums"], bars, fill=True, seed=seed),
        "bass": M.bassline(full, key, 4.0, P["bass"], 2, 100, seed),
        "chords": M.chord_progression(full, key, 4.0, 4, 85, "voice_lead", P["chords"]),
        "melody": M.melody(key, bars, 5, 0.6, full, 4.0, "arch", 95, seed),
    }
    if g in ("lofi", "hiphop"):
        notes["drums"] = M.drum_pattern(P["drums"], bars, swing=0.25, humanize=0.3, fill=True, seed=seed)
        notes["chords"] = M.strum(notes["chords"], 0.03, "down")
    target_tracks: Dict[str, Any] = dict(tracks or {})
    inst = {**DEFAULT_INSTRUMENTS, **(instruments or {})}
    for part in parts:
        if part in target_tracks:
            continue
        if not create_tracks:
            raise ValueError(f"no track given for part '{part}' (set tracks or create_tracks=True)")
        try:
            r = create_instrument_track.__wrapped__(inst.get(part, "HALion Sonic"))
            target_tracks[part] = r.get("new_track")
            log["steps"].append({"created": part, "track": r.get("new_track"), "instrument": r.get("loaded")})
        except Exception as e:  # pre-Cubase-15 or dialog cancelled
            log["steps"].append({"created": part, "error": str(e)})
    exp = export_midi_file.__wrapped__({k.capitalize(): v for k, v in notes.items() if k in parts},
                                       f"{g}_{key.replace(' ', '_')}.mid", bpm)
    log["midi_file"] = exp.get("path") if isinstance(exp, dict) else exp
    for part in parts:
        trk = target_tracks.get(part)
        if not trk:
            continue
        r = record_notes_to_track.__wrapped__(trk, notes[part], start_bar, speed)
        log["steps"].append({"recorded": part, "track": trk, "notes": r.get("recorded_notes") if isinstance(r, dict) else r})
    set_loop_range.__wrapped__(start_bar, start_bar + bars)
    if listen_after:
        try:
            rep = listen_to_cubase.__wrapped__(start_bar=start_bar, bars=min(bars, 8))
            from . import genres as G
            gp = g if g in G.PROFILES else "streaming"
            log["listen"] = {k: rep.get(k) for k in ("loudness", "key", "suggestions")}
            if "loudness" in rep:
                full_rep = rep if "spectrum" in rep else {**rep}
                log["genre_check"] = G.check(full_rep, gp)
        except Exception as e:
            log["listen"] = {"skipped": str(e)}
    log["next"] = ("Balance with set_track_volume / auto_mix_pass, add FX with load_effect, automation with "
                   "write_automation, then project_action('save').")
    return log


produce_song.__doc__ = (
    "Produce a song idea end-to-end in Cubase: set tempo for the genre, (optionally) create instrument tracks and "
    "load instruments by name (Cubase 15+), generate drums / bass / chords / melody for the key, record each part "
    "into its track (faster than real time), set the loop and finally LISTEN and check the mix against the genre "
    "profile. genre: " + ", ".join(GENRE_PRESETS) + ". Use `tracks` to record into existing tracks instead, e.g. "
    '{"drums": "Drums", "bass": "Bass"}. `instruments` overrides plug-ins per part '
    "(default Groove Agent / Retrologue / HALion Sonic).")
produce_song = tool(produce_song)


# ============================================================ resources & prompts

@mcp.resource("neocp://commands")
def resource_commands() -> str:
    """Full catalog of Cubase key commands (category/command/label)."""
    return json.dumps(commands(), ensure_ascii=False)


@mcp.resource("neocp://drum-map")
def resource_drum_map() -> str:
    """General MIDI drum map used by the generators."""
    return json.dumps(M.DRUMS)


@mcp.prompt()
def produce_track(idea: str = "a melancholic synthwave groove at 100 BPM") -> str:
    """Guide Claude through producing an idea in Cubase."""
    return (f"Produce this in Cubase: {idea}.\n"
            "1. get_session_info to inspect the project.\n"
            "2. set_tempo, then create_track (midi/instrument) for drums, bass, chords, lead; load instruments "
            "with load_plugin (Cubase 15) or ask me to pick them.\n"
            "3. generate_* tools for parts, then record_notes_to_track for each (speed=2 is fine).\n"
            "4. Balance with set_track_volume / set_track_pan, add EQ with set_eq_band, sends with set_send.\n"
            "5. listen_to_cubase (or listen_to_meters if no loopback device) to HEAR the result: fix level, EQ and "
            "balance problems it reports, listen again, then project_action('save').")


@mcp.prompt()
def mix_check(genre: str = "pop") -> str:
    """Listen to the project and fix the mix for a genre."""
    return (f"Check and improve my mix for {genre}:\n"
            "1. run_selftest if anything fails; get_session_info.\n"
            "2. listen_to_cubase over the chorus (ask me the bars if unknown, or analyze_song_structure first).\n"
            f"3. check_mix_against_genre('{genre}') and show_spectrogram; explain the 3 biggest problems.\n"
            "4. Fix them step by step (gain_stage_tracks, fix_resonances, set_eq_band, set_send, match_loudness), "
            "listening again after each change, and report before/after numbers.")


@mcp.prompt()
def transcribe_reference(path: str = "~/Music/reference.wav") -> str:
    """Turn a reference song into MIDI parts in Cubase."""
    return (f"Transcribe {path}: analyze_audio_file for key/tempo, analyze_song_structure, then transcribe_drums "
            "(drums) and transcribe_polyphonic (bass/chords/melody). Ask me which tracks to record into, set the "
            "project tempo to the detected one and record the parts with record_notes_to_track.")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
