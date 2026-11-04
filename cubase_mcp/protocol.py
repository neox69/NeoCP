"""SysEx / NRPN wire protocol shared with the MIDI Remote script.

SysEx frame (all bytes 7-bit except F0/F7)::

    F0 7D 43 4D <type> <id_hi> <id_lo> <seq_hi> <seq_lo> <tot_hi> <tot_lo> <ascii-json> F7

``type``: 0x01 request (server -> Cubase), 0x02 response, 0x03 event.
JSON is encoded with ``ensure_ascii`` so every payload byte is < 0x80.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

HEADER = (0x7D, 0x43, 0x4D)  # 0x7D = non-commercial manufacturer id, "CM"
TYPE_REQUEST = 0x01
TYPE_RESPONSE = 0x02
TYPE_EVENT = 0x03
CHUNK = 240
NRPN_MAX = 16383


def encode(msg_type: int, msg_id: int, obj: Any, chunk: int = CHUNK) -> List[List[int]]:
    """Encode an object into a list of SysEx messages (each including F0..F7)."""
    txt = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    data = txt.encode("ascii")
    total = max(1, (len(data) + chunk - 1) // chunk)
    if total > 0x3FFF:
        raise ValueError("message too large")
    msg_id &= 0x3FFF
    out = []
    for seq in range(total):
        part = data[seq * chunk:(seq + 1) * chunk]
        out.append([0xF0, *HEADER, msg_type, (msg_id >> 7) & 0x7F, msg_id & 0x7F,
                    (seq >> 7) & 0x7F, seq & 0x7F, (total >> 7) & 0x7F, total & 0x7F, *part, 0xF7])
    return out


@dataclass
class Frame:
    msg_type: int
    msg_id: int
    seq: int
    total: int
    payload: bytes


def parse_frame(msg: Iterable[int]) -> Optional[Frame]:
    """Parse one SysEx message. Accepts data with or without F0/F7."""
    b = list(msg)
    if b and b[0] == 0xF0:
        b = b[1:]
    if b and b[-1] == 0xF7:
        b = b[:-1]
    if len(b) < 10 or tuple(b[:3]) != HEADER:
        return None
    return Frame(b[3], (b[4] << 7) | b[5], (b[6] << 7) | b[7], (b[8] << 7) | b[9], bytes(b[10:]))


@dataclass
class Reassembler:
    """Collect chunked frames into complete JSON messages."""
    _parts: Dict[Tuple[int, int], Dict[int, bytes]] = field(default_factory=dict)

    def feed(self, frame: Frame) -> Optional[Tuple[int, int, Any]]:
        key = (frame.msg_type, frame.msg_id)
        if frame.seq == 0 or key not in self._parts:
            self._parts[key] = {}
        parts = self._parts[key]
        parts[frame.seq] = frame.payload
        if len(parts) < frame.total:
            return None
        del self._parts[key]
        data = b"".join(parts[i] for i in range(frame.total))
        return frame.msg_type, frame.msg_id, json.loads(data.decode("ascii"))


def slot_address(slot_id: int) -> Tuple[int, int]:
    """Slot index -> (midi channel 0-15, nrpn number 0-16383)."""
    return (slot_id >> 14) & 0x0F, slot_id & 0x3FFF


def nrpn_messages(channel: int, nrpn: int, value: float) -> List[Tuple[int, int, int]]:
    """Return (channel, cc, value) tuples for a 14-bit NRPN write of a 0..1 value."""
    v = int(round(max(0.0, min(1.0, value)) * NRPN_MAX))
    return [
        (channel, 99, (nrpn >> 7) & 0x7F),
        (channel, 98, nrpn & 0x7F),
        (channel, 6, (v >> 7) & 0x7F),
        (channel, 38, v & 0x7F),
    ]
