"""Cubase MCP server – exposes Steinberg Cubase / Nuendo to MCP clients (Claude etc.)."""
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

logging.basicConfig(level=os.environ.get("CUBASE_MCP_LOG", "INFO"),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("cubase_mcp")

INSTRUCTIONS = """Control Steinberg Cubase/Nuendo (13+) live through its MIDI Remote API.

How Cubase thinks (important):
* Tracks are addressed by index (0 = first channel in the MixConsole, excluding input/output busses).
* Deep editing (inserts, EQ, pre-filter, strip, instrument, quick controls, plug-in parameters)
  always acts on the SELECTED track: tools that take track_index select it first.
* Anything in Cubase's menus/key commands can be run with execute_command; use search_commands
  to find the exact category/command pair (1400+ commands: add tracks, quantize, bounce, export...).
* Notes cannot be written directly into parts by the API. Use record_notes_to_track: it arms the
  track, records the notes in real time from the 'CubaseMCP Notes' MIDI port and stops (optionally
  faster than real time and quantized). Or export_midi_file for drag & drop.
* Plug-in parameters / loading plug-ins use DirectAccess (Cubase 13.0.50+, plug-in loading 15+).
* You can LISTEN: listen_to_cubase plays a section and analyses the audio (LUFS, spectrum, key, chords,
  tempo, mix suggestions); listen_to_meters works without any audio routing; transcribe_audio turns a hummed
  melody into notes; compare_to_reference compares with a reference track; show_spectrogram lets you SEE it;
  analyze_song_structure finds intro/verse/chorus; gain_stage_tracks, fix_resonances, match_loudness and
  auto_mix_pass close the loop (listen -> adjust -> listen again).
* Values are reported with Cubase's own display strings (e.g. "-6.02 dB"), use them to verify.
Always call get_session_info first to see tracks and transport state.
"""

mcp = _Server("Cubase", instructions=INSTRUCTIONS)

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

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            res = fn(*a, **kw)
            if isinstance(res, (str, list)) or type(res).__name__ == "Image":
                return res
            return _ok(res)
        except CubaseError as e:
            return _ok({"error": str(e)})
        except (ValueError, KeyError, TypeError) as e:
            return _ok({"error": f"{type(e).__name__}: {e}"})
    return mcp.tool()(wrapper)


def commands() -> List[Dict[str, Any]]:
    global _COMMANDS
    if _COMMANDS is None:
        _COMMANDS = json.loads((DATA / "commands.json").read_text(encoding="utf-8"))
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
    d: Dict[str, Any] = {"name": o.get("title")}
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
        "selected_track": (b.objects.get("sel") or {}).get("title"),
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


def _position_string(b: CubaseBridge, bar: Optional[float], beat: float = 1, position: Optional[str] = None,
                     beats_per_bar: int = 4) -> str:
    if position:
        return position
    if bar is None:
        raise ValueError("give bar or position")
    fmt = str(b.transport.get("cursor_format") or "").lower()
    whole_bar = int(bar)
    extra_beats = (bar - whole_bar) * beats_per_bar + (beat - 1)
    if not fmt or "bar" in fmt or "beat" in fmt or "musical" in fmt:
        bt = int(extra_beats)
        sixteenth = int(round((extra_beats - bt) * 4))
        return f"{whole_bar}.{bt + 1}.{sixteenth + 1}.0"
    tempo = float(b.transport.get("tempo") or 120.0)
    secs = ((whole_bar - 1) * beats_per_bar + extra_beats) * 60.0 / tempo
    if "sample" in fmt:
        return str(int(secs * 48000))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}"


@tool
def set_position(bar: Optional[float] = None, beat: float = 1, position: Optional[str] = None,
                 locator: str = "cursor") -> Dict[str, Any]:
    """Move the project cursor (or the left/right locator). Use bar (1-based, may be fractional) and beat,
    or a raw position string in Cubase's primary time format (e.g. "17.1.1.0" or "0:01:30.000").
    locator: cursor | left | right."""
    b = bridge()
    pos = _position_string(b, bar, beat, position)
    b.request("time", locator=locator, value=pos)
    time.sleep(0.1)
    return {"set": pos, **get_transport_state.__wrapped__()}


@tool
def set_loop_range(start_bar: float, end_bar: float, enable_cycle: bool = True) -> Dict[str, Any]:
    """Set left/right locators to a bar range (end exclusive) and optionally enable cycle."""
    b = bridge()
    b.request("time", locator="left", value=_position_string(b, start_bar))
    b.request("time", locator="right", value=_position_string(b, end_bar))
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
            meters[b.keys[sid]] = v
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
        key = b.keys[sid]
        pre, _, kind = key.partition(".")
        name = (b.objects.get(pre) or {}).get("title")
        if not name:
            continue
        out.setdefault(name, {})[kind] = v
    return out


@tool
def bank(action: str = "reset", times: int = 1) -> Dict[str, Any]:
    """Move the mirrored mixer bank (for projects with many tracks): next | prev | reset | shift_left | shift_right."""
    b = bridge()
    r = b.action(f"bank_{action}", times=times)
    time.sleep(0.25)
    return {**r, "tracks": b.bank_channels()}


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
    b.wait_for(lambda: len(b.bank_channels()) >= before + count, 2.0)
    return {"created": track_type, "count": count, "selected_track": (b.objects.get("sel") or {}).get("title"),
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
    return {"done": operation, "selected_track": (b.objects.get("sel") or {}).get("title"), "tracks": b.bank_channels()}


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
    for _ in range(max(1, int(times))):
        b.command(category, command)
        time.sleep(0.08)
    return {"executed": f"{category}/{command}", "times": times}


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
    return {"name": (b.objects.get("sel") or {}).get("title")}


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
                    velocity_scale: float = 1.0, seed: Optional[int] = None) -> Dict[str, Any]:
    """Transform a note list: transpose, shift, quantize (grid in beats), humanize, scale velocities."""
    n = M.normalize_notes(notes)
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
    d = Path(os.environ.get("CUBASE_MCP_EXPORT_DIR", Path.home() / "Documents" / "CubaseMCP"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@tool
def export_midi_file(tracks: Dict[str, List[Dict[str, Any]]], filename: str = "cubase_mcp.mid",
                     tempo: Optional[float] = None, time_signature: str = "4/4") -> Dict[str, Any]:
    """Write a multi-track MIDI file, e.g. {"Drums": [...], "Bass": [...]} (tracks starting with 'drum' use
    channel 10). Saved to ~/Documents/CubaseMCP (or CUBASE_MCP_EXPORT_DIR). Drag it into Cubase or use
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
    """Audition notes live through the 'CubaseMCP Notes' port into the selected (monitored/armed) track
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
    the 'CubaseMCP Notes' port in sync, stop, restore tempo/arm. speed > 1 temporarily raises the tempo
    (max 300 BPM) to record faster — note positions stay musically identical. quantize_after runs Cubase's
    Quantize on the new part. Requirements: precount off, track input = 'All MIDI Inputs' or 'CubaseMCP Notes'.
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
        b.request("time", locator="cursor", value=_position_string(b, start_pos, beats_per_bar=beats_per_bar))
        time.sleep(0.2)
        b.set("transport.record", 1.0)
        b.set("transport.play", 1.0)
        b.wait_for(lambda: bool(_flag(b, "transport.record")), 2.0)
        latency = float(os.environ.get("CUBASE_MCP_RECORD_LATENCY_MS", "0")) / 1000.0
        _play_stop.clear()
        stats = _play(b.link, M.shift(canon, pre_beats), rec_tempo, channel, start_delay=latency, stop=_play_stop)
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
                    "and set CUBASE_MCP_LISTEN_DEVICE to its name."}


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
    b.request("time", locator="cursor", value=_position_string(b, start_bar, beats_per_bar=beats_per_bar))
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
        b.request("time", locator="cursor", value=_position_string(b, start_bar))
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
                key = b.keys[sid]
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
            b.request("time", locator="cursor", value=_position_string(b, sec["start_bar"], beats_per_bar=beats_per_bar))
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
        name = b.objects[pre]["title"]
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
    result: Dict[str, Any] = {"track": b.objects[b.track_prefix(idx)]["title"], "resonances_found": res, "eq_cuts": applied}
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


# ============================================================ resources & prompts

@mcp.resource("cubase://commands")
def resource_commands() -> str:
    """Full catalog of Cubase key commands (category/command/label)."""
    return json.dumps(commands(), ensure_ascii=False)


@mcp.resource("cubase://drum-map")
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
