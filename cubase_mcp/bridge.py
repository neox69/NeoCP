"""High level, thread-safe connection to the Cubase MIDI Remote bridge script."""
from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import protocol as P
from .link import Link

logger = logging.getLogger("cubase_mcp.bridge")

SETUP_HINT = (
    "Cubase is not answering. Checklist: (1) Cubase/Nuendo 13+ is running with a project open; "
    "(2) the MIDI Remote script is installed (run `cubase-mcp-install`) and Cubase was restarted "
    "or 'Reload Scripts' was pressed in the MIDI Remote tab; (3) in Studio > MIDI Remote Manager "
    "the 'CubaseMCP - Bridge' controller is enabled with input 'CubaseMCP to Cubase' and output "
    "'CubaseMCP from Cubase' (on Windows create these ports with loopMIDI)."
)


class CubaseError(RuntimeError):
    pass


class _Pending:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: Any = None


class CubaseBridge:
    def __init__(self, link: Link, write_mode: Optional[str] = None) -> None:
        self.link = link
        self.link.on_sysex = self._on_sysex
        self.write_mode = (write_mode or os.environ.get("CUBASE_MCP_WRITE_MODE", "nrpn")).lower()
        self._reasm = P.Reassembler()
        self._ids = itertools.count(1)
        self._pending: Dict[int, _Pending] = {}
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.connected = False
        self.info: Dict[str, Any] = {}
        self.keys: List[str] = []
        self.kinds: List[str] = []
        self.key_to_id: Dict[str, int] = {}
        self.command_ids: Dict[Tuple[str, str], int] = {}
        self.state: Dict[int, List[Any]] = {}
        self.objects: Dict[str, Dict[str, Any]] = {}
        self.transport: Dict[str, Any] = {}
        self.bank_offset = 0
        self.last_event_time = 0.0
        self.script_restarted = False
        self.listeners: List[Callable[[dict], None]] = []

    # ------------------------------------------------------------------ wire
    def _on_sysex(self, data: List[int]) -> None:
        frame = P.parse_frame(data)
        if not frame:
            return
        done = self._reasm.feed(frame)
        if not done:
            return
        mtype, mid, obj = done
        if mtype == P.TYPE_RESPONSE:
            with self._lock:
                pend = self._pending.get(mid)
            if pend:
                pend.response = obj
                pend.event.set()
        elif mtype == P.TYPE_EVENT:
            self._apply_event(obj)

    def _apply_event(self, ev: dict) -> None:
        with self._cond:
            self.last_event_time = time.time()
            if ev.get("ev") == "hello":
                # script (re)loaded inside Cubase: slot table may have changed
                if self.connected and ev.get("slots") != len(self.keys):
                    self.connected = False
                self.script_restarted = True
            if ev.get("ev") == "state":
                for row in ev.get("slots") or []:
                    self.state[row[0]] = row[1:]
            if ev.get("transport"):
                self.transport.update(ev["transport"])
            if ev.get("objects"):
                self.objects.update(ev["objects"])
            self._cond.notify_all()
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                logger.exception("listener failed")

    def request(self, op: str, timeout: float = 5.0, **params: Any) -> Any:
        mid = next(self._ids) & 0x3FFF or 1
        pend = _Pending()
        with self._lock:
            self._pending[mid] = pend
        try:
            payload = {"op": op, **{k: v for k, v in params.items() if v is not None}}
            for msg in P.encode(P.TYPE_REQUEST, mid, payload):
                self.link.send_sysex(msg)
            if not pend.event.wait(timeout):
                raise CubaseError(f"timeout waiting for Cubase ({op}). " + SETUP_HINT)
        finally:
            with self._lock:
                self._pending.pop(mid, None)
        resp = pend.response
        if not resp.get("ok"):
            raise CubaseError(resp.get("error", "unknown error"))
        return resp.get("result")

    # ------------------------------------------------------------ handshake
    def connect(self, timeout: float = 4.0) -> Dict[str, Any]:
        info = self.request("hello", timeout=timeout)
        keys: List[str] = []
        kinds: List[str] = []
        off = 0
        while off < info["slots"]:
            page = self.request("slots", offset=off, limit=400, timeout=10)
            for k, kind in page["slots"]:
                keys.append(k)
                kinds.append(kind)
            off += len(page["slots"])
            if not page["slots"]:
                break
        with self._lock:
            self.info = info
            self.keys, self.kinds = keys, kinds
            self.key_to_id = {k: i for i, k in enumerate(keys)}
            self.command_ids = {}
            for i, k in enumerate(keys):
                if kinds[i] == "c" and k.startswith("cmd:"):
                    cat, _, cmd = k[4:].partition("/")
                    self.command_ids[(cat, cmd)] = i
            self.bank_offset = info.get("bank_offset", 0)
        self.refresh()
        self.connected = True
        self.script_restarted = False
        return info

    def ensure(self) -> None:
        if not self.connected:
            self.connect()

    def refresh(self) -> None:
        d = self.request("dump", timeout=10)
        with self._cond:
            for row in d.get("slots", []):
                self.state[row[0]] = row[1:]
            self.objects.update(d.get("objects") or {})
            self.transport.update(d.get("transport") or {})
            self.bank_offset = d.get("bank_offset", self.bank_offset)
            self._cond.notify_all()

    # ---------------------------------------------------------------- values
    def slot_id(self, key: str) -> int:
        try:
            return self.key_to_id[key]
        except KeyError:
            raise CubaseError(f"unknown control '{key}'") from None

    def has(self, key: str) -> bool:
        return key in self.key_to_id

    def get(self, key: str) -> Dict[str, Any]:
        sid = self.slot_id(key)
        row = self.state.get(sid) or [None, None, None, None, None]
        return {"value": row[0], "display": row[1], "units": row[2], "title": row[3], "value_title": row[4]}

    def value(self, key: str) -> Optional[float]:
        if key not in self.key_to_id:
            return None
        row = self.state.get(self.key_to_id[key])
        return row[0] if row else None

    def display(self, key: str) -> Optional[str]:
        if key not in self.key_to_id:
            return None
        row = self.state.get(self.key_to_id[key])
        if not row or row[1] is None:
            return None
        return f"{row[1]}{(' ' + row[2]) if row[2] else ''}".strip()

    def set(self, key: str, value: float) -> None:
        sid = self.slot_id(key)
        value = max(0.0, min(1.0, float(value)))
        if self.write_mode == "sysex":
            self.request("set", id=sid, value=value)
        else:
            ch, nrpn = P.slot_address(sid)
            for c, cc, v in P.nrpn_messages(ch, nrpn, value):
                self.link.send_cc(c, cc, v)

    def wait_for(self, pred: Callable[[], bool], timeout: float = 1.0) -> bool:
        end = time.time() + timeout
        with self._cond:
            while not pred():
                left = end - time.time()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def set_and_wait(self, key: str, value: float, timeout: float = 0.6, tol: float = 0.002) -> Dict[str, Any]:
        self.set(key, value)
        self.wait_for(lambda: (self.value(key) is not None and abs(self.value(key) - value) <= tol), timeout)
        time.sleep(0.03)  # let the display string follow
        return self.get(key)

    def set_display_target(self, key: str, target: float, lo: float = 0.0, hi: float = 1.0,
                           parse: Callable[[Optional[str]], Optional[float]] = None, iterations: int = 12,
                           tolerance: float = 0.05) -> Dict[str, Any]:
        """Bisect the process value until the displayed number reaches ``target``.

        Works for any monotonically increasing parameter (volume dB, frequency, ...)."""
        parse = parse or _parse_number
        best = None
        for _ in range(iterations):
            mid = (lo + hi) / 2
            st = self.set_and_wait(key, mid)
            got = parse(st.get("display"))
            if got is None:
                return st
            best = st
            if abs(got - target) <= tolerance:
                break
            if got < target:
                lo = mid
            else:
                hi = mid
        return best or self.get(key)

    def command(self, category: str, command: str) -> None:
        sid = self.command_ids.get((category, command))
        if sid is None:
            raise CubaseError(f"command '{category}/{command}' is not bound by the bridge script "
                              f"(use search_commands to find the exact category/command)")
        if self.write_mode == "sysex":
            self.request("cmd", category=category, command=command)
        else:
            ch, nrpn = P.slot_address(sid)
            for c, cc, v in P.nrpn_messages(ch, nrpn, 1.0):
                self.link.send_cc(c, cc, v)
            time.sleep(0.02)
            for c, cc, v in P.nrpn_messages(ch, nrpn, 0.0):
                self.link.send_cc(c, cc, v)

    def action(self, name: str, times: int = 1) -> Dict[str, Any]:
        r = self.request("action", name=name, times=times)
        self.bank_offset = r.get("bank_offset", self.bank_offset)
        return r

    # ---------------------------------------------------------------- tracks
    @property
    def bank_size(self) -> int:
        return int(self.info.get("bank_size", 32))

    def bank_channels(self) -> List[Dict[str, Any]]:
        out = []
        for i in range(self.bank_size):
            o = self.objects.get(f"ch{i}") or {}
            title = o.get("title")
            if not title:
                continue
            out.append({"bank_slot": i, "index": self.bank_offset + i, "name": title, "color": o.get("color")})
        return out

    def ensure_track_in_bank(self, index: int) -> int:
        """Make sure absolute track ``index`` is mirrored; return its bank slot."""
        if index < 0:
            raise CubaseError("track index must be >= 0")
        if self.bank_offset <= index < self.bank_offset + self.bank_size:
            return index - self.bank_offset
        target = (index // self.bank_size) * self.bank_size
        self.action("bank_reset")
        if target:
            self.action("bank_next", times=target // self.bank_size)
        time.sleep(0.25)
        return index - self.bank_offset

    def track_prefix(self, index: int) -> str:
        slot = self.ensure_track_in_bank(index)
        if not (self.objects.get(f"ch{slot}") or {}).get("title"):
            raise CubaseError(f"there is no track with index {index}")
        return f"ch{slot}"

    def select_track(self, index: int, wait: float = 1.0) -> str:
        pre = self.track_prefix(index)
        name = self.objects[pre]["title"]
        self.set(f"{pre}.selected", 1.0)
        self.wait_for(lambda: (self.objects.get("sel") or {}).get("title") == name, wait)
        time.sleep(0.1)
        return name


def _parse_number(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    import re
    t = str(s).strip().replace(",", ".")
    if t.lower() in ("-oo", "-inf", "-∞", "off"):
        return float("-inf")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group(0))
    if re.search(r"\d\s*k(hz)?\b", t.lower()):
        v *= 1000
    return v
