"""MIDI transport between the MCP server and Cubase.

Three ports are used:

* ``NeoCP to Cubase``   – server -> Cubase (SysEx requests + NRPN value writes)
* ``NeoCP from Cubase`` – Cubase -> server (SysEx responses + state events)
* ``NeoCP Notes``       – server -> Cubase tracks (notes to play / record)

On macOS and Linux the ports are created as virtual ports automatically if they
do not exist. On Windows create them once with loopMIDI (same names).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable, List, Optional

logger = logging.getLogger("neocp.link")

PORT_TO_CUBASE = os.environ.get("NEOCP_PORT_TO_CUBASE", "NeoCP to Cubase")
PORT_FROM_CUBASE = os.environ.get("NEOCP_PORT_FROM_CUBASE", "NeoCP from Cubase")
PORT_NOTES = os.environ.get("NEOCP_PORT_NOTES", "NeoCP Notes")


class Link:
    """Abstract MIDI link. Subclasses deliver raw SysEx (list of ints incl. F0/F7)."""

    def __init__(self) -> None:
        self.on_sysex: Optional[Callable[[List[int]], None]] = None
        self._lock = threading.Lock()

    # --- to override -------------------------------------------------------
    def send_sysex(self, data: List[int]) -> None:  # data includes F0 .. F7
        raise NotImplementedError

    def send_cc(self, channel: int, cc: int, value: int) -> None:
        raise NotImplementedError

    def send_note(self, kind: str, channel: int, note: int, velocity: int) -> None:
        """kind: 'on' | 'off' – sent on the notes port."""
        raise NotImplementedError

    def send_notes_raw(self, kind: str, channel: int, a: int, b: int = 0) -> None:
        """kind: 'cc' | 'pitchwheel' | 'program' on the notes port."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def describe(self) -> dict:
        return {}

    def _deliver(self, data: List[int]) -> None:
        cb = self.on_sysex
        if cb:
            try:
                cb(data)
            except Exception:  # pragma: no cover - defensive
                logger.exception("sysex handler failed")


def _find(names: List[str], wanted: str) -> Optional[str]:
    w = wanted.lower()
    for n in names:
        if n.lower() == w:
            return n
    for n in names:
        if w in n.lower() or w.replace(" ", "_") in n.lower():
            return n
    return None


class MidoLink(Link):
    """Link implemented with mido + python-rtmidi."""

    def __init__(self) -> None:
        super().__init__()
        import mido  # imported lazily so tests run without rtmidi

        self._mido = mido
        virtual_ok = sys.platform != "win32"
        outs = mido.get_output_names()
        ins = mido.get_input_names()
        self.created_virtual = []

        def open_out(name: str):
            found = _find(outs, name)
            if found:
                return mido.open_output(found), found
            if virtual_ok:
                self.created_virtual.append(name)
                return mido.open_output(name, virtual=True), name
            raise RuntimeError(
                f"MIDI port '{name}' not found. On Windows create it with loopMIDI "
                f"(https://www.tobias-erichsen.de/software/loopmidi.html). Available outputs: {outs}")

        def open_in(name: str):
            found = _find(ins, name)
            if found:
                return mido.open_input(found, callback=self._on_msg), found
            if virtual_ok:
                self.created_virtual.append(name)
                return mido.open_input(name, virtual=True, callback=self._on_msg), name
            raise RuntimeError(
                f"MIDI port '{name}' not found. On Windows create it with loopMIDI. Available inputs: {ins}")

        self.out, self.out_name = open_out(PORT_TO_CUBASE)
        self.inp, self.in_name = open_in(PORT_FROM_CUBASE)
        try:
            self.notes, self.notes_name = open_out(PORT_NOTES)
        except Exception as e:  # notes port is optional
            logger.warning("notes port unavailable: %s", e)
            self.notes, self.notes_name = None, None

    def _on_msg(self, msg) -> None:
        if msg.type == "sysex":
            self._deliver([0xF0, *msg.data, 0xF7])

    def send_sysex(self, data: List[int]) -> None:
        body = data[1:-1] if data and data[0] == 0xF0 else data
        with self._lock:
            self.out.send(self._mido.Message("sysex", data=body))

    def send_cc(self, channel: int, cc: int, value: int) -> None:
        with self._lock:
            self.out.send(self._mido.Message("control_change", channel=channel, control=cc, value=value))

    def send_note(self, kind: str, channel: int, note: int, velocity: int) -> None:
        if not self.notes:
            raise RuntimeError(f"notes port '{PORT_NOTES}' is not available")
        t = "note_on" if kind == "on" else "note_off"
        self.notes.send(self._mido.Message(t, channel=channel, note=note, velocity=velocity))

    def send_notes_raw(self, kind: str, channel: int, a: int, b: int = 0) -> None:
        if not self.notes:
            raise RuntimeError(f"notes port '{PORT_NOTES}' is not available")
        M = self._mido.Message
        if kind == "cc":
            self.notes.send(M("control_change", channel=channel, control=a, value=b))
        elif kind == "pitchwheel":
            self.notes.send(M("pitchwheel", channel=channel, pitch=a))
        elif kind == "program":
            self.notes.send(M("program_change", channel=channel, program=a))

    def describe(self) -> dict:
        return {"to_cubase": self.out_name, "from_cubase": self.in_name, "notes": self.notes_name,
                "virtual_ports_created": self.created_virtual}

    def close(self) -> None:
        for p in (self.out, self.inp, self.notes):
            try:
                if p:
                    p.close()
            except Exception:
                pass
