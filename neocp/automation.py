"""Automation curves: shapes and breakpoints -> value(beat) functions.

Values are in the target's user unit (dB for volume/sends, -1..1 for pan, 0..1 otherwise);
the server converts them to Cubase's normalized values.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence

Curve = Callable[[float], float]

SHAPES = ("ramp", "fade_in", "fade_out", "lfo", "duck", "swell", "steps", "points", "hold")


def _ease(t: float, curve: str) -> float:
    t = max(0.0, min(1.0, t))
    if curve == "exp":
        return t * t
    if curve == "log":
        return 1 - (1 - t) ** 2
    if curve in ("s", "smooth"):
        return t * t * (3 - 2 * t)
    return t


def points_curve(points: Sequence[Dict[str, Any]], beats_per_bar: int = 4, start_bar: float = 1.0,
                 curve: str = "linear") -> Curve:
    """Breakpoints: [{"beat": 0, "value": -12}, {"bar": 5, "value": 0}, ...].

    "beat" is relative to the start of the automation; "bar" is an absolute project bar."""
    pts = []
    for p in points:
        if "beat" in p:
            b = float(p["beat"])
        elif "bar" in p:
            b = (float(p["bar"]) - start_bar) * beats_per_bar
        else:
            raise ValueError("each point needs 'beat' or 'bar'")
        pts.append((b, float(p["value"])))
    if not pts:
        raise ValueError("no points")
    pts.sort()

    def f(beat: float) -> float:
        if beat <= pts[0][0]:
            return pts[0][1]
        for (b0, v0), (b1, v1) in zip(pts, pts[1:]):
            if b0 <= beat <= b1:
                if b1 == b0:
                    return v1
                return v0 + (v1 - v0) * _ease((beat - b0) / (b1 - b0), curve)
        return pts[-1][1]
    return f


def shape_curve(shape: str, length_beats: float, start: Optional[float] = None, end: Optional[float] = None,
                center: Optional[float] = None, depth: Optional[float] = None, rate_beats: float = 1.0,
                waveform: str = "sine", curve: str = "linear", steps: Optional[Sequence[float]] = None,
                attack: float = 0.1, unit: str = "normalized") -> Curve:
    """Generate common automation gestures.

    * ramp:      start -> end over the whole length (curve: linear | exp | log | s)
    * fade_in:   silence -> end (default 0 dB)          * fade_out: start (default 0 dB) -> silence
    * lfo:       center +/- depth, period rate_beats, waveform sine | triangle | square | saw
    * duck:      sidechain-style pump: drops by depth on every rate_beats and recovers over attack
                 (fraction of the period, default 0.6)
    * swell:     rises from start to end inside every rate_beats (e.g. 4 = per bar), then resets
    * steps:     list of values, one per rate_beats (trance gate / stepped filter)
    * hold:      constant value (start)
    """
    is_db = unit == "db"
    floor = -60.0 if is_db else 0.0
    top = 0.0 if is_db else 1.0
    L = max(1e-6, float(length_beats))
    if shape == "ramp":
        a = floor if start is None else start
        b = top if end is None else end
        return lambda x: a + (b - a) * _ease(x / L, curve)
    if shape == "fade_in":
        a = floor if start is None else start
        b = top if end is None else end
        return lambda x: a + (b - a) * _ease(x / L, curve if curve != "linear" else ("log" if is_db else "linear"))
    if shape == "fade_out":
        a = top if start is None else start
        b = floor if end is None else end
        return lambda x: a + (b - a) * _ease(x / L, curve if curve != "linear" else ("exp" if is_db else "linear"))
    if shape == "hold":
        v = top if start is None else start
        return lambda x: v
    if shape == "lfo":
        c = (0.0 if unit == "pan" else (-6.0 if is_db else 0.5)) if center is None else center
        d = (1.0 if unit == "pan" else (6.0 if is_db else 0.5)) if depth is None else depth
        r = max(1e-3, rate_beats)

        def lfo(x: float) -> float:
            ph = (x / r) % 1.0
            if waveform == "triangle":
                w = 1 - 4 * abs(ph - 0.5)
            elif waveform == "square":
                w = 1.0 if ph < 0.5 else -1.0
            elif waveform == "saw":
                w = 2 * ph - 1
            else:
                w = math.sin(2 * math.pi * ph)
            return c + d * w
        return lfo
    if shape == "duck":
        base = (top if is_db else 1.0) if start is None else start
        d = (12.0 if is_db else 0.6) if depth is None else depth
        r = max(1e-3, rate_beats)
        rel = attack if attack and attack > 0.1 else 0.6  # fraction of the period used to recover

        def duck(x: float) -> float:
            ph = (x / r) % 1.0
            return base - d * (1 - min(1.0, ph / rel)) ** 2
        return duck
    if shape == "swell":
        a = floor if start is None else start
        b = top if end is None else end
        r = max(1e-3, rate_beats)
        return lambda x: a + (b - a) * _ease(((x / r) % 1.0), curve if curve != "linear" else "exp")
    if shape == "steps":
        if not steps:
            raise ValueError("steps shape needs 'steps' values")
        vals = [float(v) for v in steps]
        r = max(1e-3, rate_beats)
        return lambda x: vals[int(x // r) % len(vals)]
    raise ValueError(f"shape must be one of {', '.join(SHAPES)}")


def sample(curve: Curve, length_beats: float, step_beats: float = 0.25) -> List[Dict[str, float]]:
    """Preview of a curve (for the user / tests)."""
    out = []
    b = 0.0
    while b <= length_beats + 1e-9:
        out.append({"beat": round(b, 4), "value": round(float(curve(b)), 3)})
        b += step_beats
    return out
