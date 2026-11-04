"""Audio capture ("listening") through any input device, using sounddevice (PortAudio).

To hear Cubase, route its output into a loopback device:
* macOS: BlackHole 2ch (free). In Cubase Studio > Audio Connections > Outputs add a bus on
  BlackHole, or make a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup.
* Windows: VB-Audio Virtual Cable, or "Stereo Mix" if your card has it; Cubase's ASIO driver
  usually owns the hardware, so a virtual cable bus in Cubase is the reliable route.
* Any OS: a microphone input works too (humming / playing an instrument).
Set CUBASE_MCP_LISTEN_DEVICE to a device name substring to choose the default.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULT_HINTS = ("blackhole", "loopback", "cable output", "vb-audio", "stereo mix", "soundflower", "monitor of")


def _sd():
    try:
        import sounddevice as sd
    except Exception as e:  # pragma: no cover - depends on system PortAudio
        raise RuntimeError(f"audio capture needs 'sounddevice' + PortAudio ({e}). pip install 'cubase-mcp[audio]'") from e
    return sd


def list_input_devices() -> List[Dict[str, Any]]:
    sd = _sd()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            out.append({"id": i, "name": d["name"], "channels": d["max_input_channels"], "api": api,
                        "default_samplerate": d["default_samplerate"],
                        "looks_like_loopback": any(h in d["name"].lower() for h in DEFAULT_HINTS)})
    return out


def resolve_device(device: Optional[str] = None, prefer_loopback: bool = True) -> Tuple[Optional[int], str]:
    devs = list_input_devices()
    want = device or os.environ.get("CUBASE_MCP_LISTEN_DEVICE")
    if want is not None and str(want).isdigit():
        d = next((x for x in devs if x["id"] == int(want)), None)
        if d:
            return d["id"], d["name"]
    if want:
        for d in devs:
            if str(want).lower() in d["name"].lower():
                return d["id"], d["name"]
        raise RuntimeError(f"no input device matching '{want}'. Inputs: {[d['name'] for d in devs]}")
    if prefer_loopback:
        for d in devs:
            if d["looks_like_loopback"]:
                return d["id"], d["name"]
    return None, "system default input"


class Recorder:
    """Non-blocking recorder: start(), then stop() returns (audio, samplerate)."""

    def __init__(self, device: Optional[str] = None, samplerate: Optional[int] = None, channels: int = 2,
                 prefer_loopback: bool = True):
        sd = _sd()
        self.device_id, self.device_name = resolve_device(device, prefer_loopback)
        info = sd.query_devices(self.device_id, "input")
        self.sr = int(samplerate or info["default_samplerate"])
        self.channels = max(1, min(channels, int(info["max_input_channels"])))
        self._chunks: List[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = sd.InputStream(device=self.device_id, samplerate=self.sr, channels=self.channels,
                                      dtype="float32", callback=self._cb)

    def _cb(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self) -> "Recorder":
        self._stream.start()
        return self

    def stop(self) -> Tuple[np.ndarray, int]:
        self._stream.stop()
        self._stream.close()
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros((0, self.channels), np.float32)
        return audio, self.sr


def record(seconds: float, device: Optional[str] = None, samplerate: Optional[int] = None,
           prefer_loopback: bool = True) -> Tuple[np.ndarray, int, str]:
    import time
    r = Recorder(device, samplerate, prefer_loopback=prefer_loopback).start()
    time.sleep(max(0.1, float(seconds)))
    audio, sr = r.stop()
    return audio, sr, r.device_name
