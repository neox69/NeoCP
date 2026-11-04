"""Real-time note playback on the "CubaseMCP Notes" port.

Cubase's MIDI Remote API cannot write MIDI events into parts, so notes reach the
project the same way a keyboard player's would: they are played on a virtual
MIDI port into a record-armed track while Cubase records. Timing is scheduled
with ``perf_counter`` (sub-millisecond on desktop OSes) and can be tightened
afterwards with Cubase's Quantize.
"""
from __future__ import annotations

import heapq
import threading
import time
from typing import Dict, List, Optional

from .link import Link


def play_notes(link: Link, notes: List[Dict], bpm: float, channel: int = 0,
               start_delay: float = 0.0, stop: Optional[threading.Event] = None) -> Dict:
    """Play canonical notes (beats) at ``bpm``. Blocks until done. Returns stats."""
    spb = 60.0 / float(bpm)
    events = []
    for i, n in enumerate(notes):
        ch = int(n.get("channel", channel))
        on = n["start"] * spb
        off = (n["start"] + n["duration"]) * spb
        heapq.heappush(events, (on, 1, i, "on", ch, n["pitch"], n["velocity"]))
        heapq.heappush(events, (off, 0, i, "off", ch, n["pitch"], 0))
    t0 = time.perf_counter() + start_delay
    max_late = 0.0
    sounding = set()
    try:
        while events:
            if stop is not None and stop.is_set():
                break
            when, _, _, kind, ch, pitch, vel = heapq.heappop(events)
            target = t0 + when
            while True:
                now = time.perf_counter()
                left = target - now
                if left <= 0:
                    break
                time.sleep(left - 0.002 if left > 0.004 else 0)
            max_late = max(max_late, time.perf_counter() - target)
            link.send_note(kind, ch, pitch, vel)
            if kind == "on":
                sounding.add((ch, pitch))
            else:
                sounding.discard((ch, pitch))
    finally:
        for ch, pitch in list(sounding):
            link.send_note("off", ch, pitch, 0)
    return {"notes": len(notes), "duration_s": round(time.perf_counter() - t0, 3),
            "max_late_ms": round(max_late * 1000, 2)}


def all_notes_off(link: Link) -> None:
    for ch in range(16):
        try:
            link.send_notes_raw("cc", ch, 123, 0)
            link.send_notes_raw("cc", ch, 64, 0)
        except Exception:
            return
