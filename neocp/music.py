"""Music theory helpers and pattern generators.

All generators return note dicts: ``{"pitch", "start", "duration", "velocity"}`` with
``start``/``duration`` in quarter-note beats (same convention as ableton-mcp).
"""
from __future__ import annotations

import random
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Note = Dict[str, float]

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

SCALES: Dict[str, List[int]] = {
    "major": [0, 2, 4, 5, 7, 9, 11], "ionian": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10], "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "natural_minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11], "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11], "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "major_pentatonic": [0, 2, 4, 7, 9], "minor_pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10], "major_blues": [0, 2, 3, 4, 7, 9],
    "phrygian_dominant": [0, 1, 4, 5, 7, 8, 10], "hungarian_minor": [0, 2, 3, 6, 7, 8, 11],
    "whole_tone": [0, 2, 4, 6, 8, 10], "chromatic": list(range(12)),
    "diminished": [0, 2, 3, 5, 6, 8, 9, 11], "japanese": [0, 1, 5, 7, 8],
    "arabic": [0, 1, 4, 5, 7, 8, 11], "spanish": [0, 1, 3, 4, 5, 7, 8, 10],
}

CHORD_QUALITIES: Dict[str, List[int]] = {
    "": [0, 4, 7], "maj": [0, 4, 7], "M": [0, 4, 7], "m": [0, 3, 7], "min": [0, 3, 7], "-": [0, 3, 7],
    "dim": [0, 3, 6], "o": [0, 3, 6], "aug": [0, 4, 8], "+": [0, 4, 8],
    "sus2": [0, 2, 7], "sus4": [0, 5, 7], "sus": [0, 5, 7], "5": [0, 7],
    "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "M7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10], "min7": [0, 3, 7, 10], "-7": [0, 3, 7, 10], "mmaj7": [0, 3, 7, 11],
    "dim7": [0, 3, 6, 9], "o7": [0, 3, 6, 9], "m7b5": [0, 3, 6, 10], "ø": [0, 3, 6, 10],
    "7sus4": [0, 5, 7, 10], "aug7": [0, 4, 8, 10], "7#5": [0, 4, 8, 10],
    "add9": [0, 4, 7, 14], "madd9": [0, 3, 7, 14], "9": [0, 4, 7, 10, 14], "maj9": [0, 4, 7, 11, 14],
    "m9": [0, 3, 7, 10, 14], "7b9": [0, 4, 7, 10, 13], "7#9": [0, 4, 7, 10, 15],
    "11": [0, 4, 7, 10, 14, 17], "m11": [0, 3, 7, 10, 14, 17], "13": [0, 4, 7, 10, 14, 21],
    "maj13": [0, 4, 7, 11, 14, 21], "m13": [0, 3, 7, 10, 14, 21], "6/9": [0, 4, 7, 9, 14],
}

# General MIDI drum map
DRUMS = {
    "kick": 36, "kick2": 35, "snare": 38, "snare2": 40, "rim": 37, "clap": 39,
    "closed_hat": 42, "hat": 42, "pedal_hat": 44, "open_hat": 46,
    "low_tom": 45, "mid_tom": 47, "high_tom": 50, "floor_tom": 41,
    "crash": 49, "ride": 51, "ride_bell": 53, "china": 52, "splash": 55,
    "tambourine": 54, "cowbell": 56, "shaker": 70, "conga_high": 62, "conga_low": 64,
    "bongo_high": 60, "bongo_low": 61, "clave": 75, "woodblock": 76,
}


def note_to_midi(name) -> int:
    """'C4' -> 60, 'F#3' -> 54, 'Bb2' -> 46. Integers pass through."""
    if isinstance(name, (int, float)):
        return int(name)
    s = str(name).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]*)(-?\d+)?", s)
    if not m:
        raise ValueError(f"bad note name '{name}'")
    pc = _PC[m.group(1).upper()] + m.group(2).count("#") + m.group(2).count("♯") \
        - m.group(2).count("b") - m.group(2).count("♭")
    octave = int(m.group(3)) if m.group(3) is not None else 4
    return (octave + 1) * 12 + pc


def midi_to_note(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def pitch_class(name: str) -> int:
    return note_to_midi(re.sub(r"-?\d+$", "", name.strip()) + "4") % 12


def parse_key(key: str) -> Tuple[int, str]:
    """'C', 'Am', 'F# minor', 'D dorian', 'Eb major' -> (root pitch class, scale name)."""
    k = key.strip()
    m = re.fullmatch(r"([A-Ga-g][#b]?)\s*(.*)", k)
    if not m:
        raise ValueError(f"bad key '{key}'")
    root = pitch_class(m.group(1))
    rest = m.group(2).strip().lower().replace(" ", "_")
    if rest in ("", "maj", "major", "M"):
        scale = "major"
    elif rest in ("m", "min", "minor", "-"):
        scale = "minor"
    else:
        scale = rest
    if scale not in SCALES:
        raise ValueError(f"unknown scale '{scale}'. Known: {', '.join(sorted(SCALES))}")
    return root, scale


def scale_notes(key: str, octave: int = 4, octaves: int = 1) -> List[int]:
    root, scale = parse_key(key)
    base = (octave + 1) * 12 + root
    out = [base + 12 * o + i for o in range(octaves) for i in SCALES[scale]]
    out.append(base + 12 * octaves)
    return out


def parse_chord(symbol: str, octave: int = 4) -> List[int]:
    """Chord symbol -> MIDI notes. Supports slash chords: 'Am7', 'F#m7b5', 'C/E', 'Gsus4'."""
    s = symbol.strip()
    bass = None
    if "/" in s and not s.endswith("6/9"):
        s, bass = s.split("/", 1)
    m = re.fullmatch(r"([A-Ga-g][#b]?)(.*)", s)
    if not m:
        raise ValueError(f"bad chord '{symbol}'")
    root = (octave + 1) * 12 + pitch_class(m.group(1))
    qual = m.group(2)
    if qual not in CHORD_QUALITIES:
        raise ValueError(f"unknown chord quality '{qual}' in '{symbol}'")
    notes = [root + i for i in CHORD_QUALITIES[qual]]
    if bass:
        b = (octave + 1) * 12 + pitch_class(bass) - 12
        while b >= notes[0]:
            b -= 12
        notes = [b] + notes
    return notes


_ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}


def roman_to_chord(numeral: str, key: str, octave: int = 4) -> List[int]:
    """Roman numeral in key -> notes. 'I', 'vi', 'V7', 'ii7', 'bVII', 'iv', 'viio'."""
    root_pc, scale = parse_key(key)
    sc = SCALES[scale] if len(SCALES[scale]) == 7 else SCALES["major"]
    m = re.fullmatch(r"([b#]?)([ivIV]+)(.*)", numeral.strip())
    if not m:
        raise ValueError(f"bad roman numeral '{numeral}'")
    acc, rn, ext = m.groups()
    deg = _ROMAN[rn.lower()]
    shift = -1 if acc == "b" else (1 if acc == "#" else 0)
    root = (octave + 1) * 12 + root_pc + sc[deg] + shift
    upper = rn.isupper()
    if ext in ("o", "dim", "°"):
        q = [0, 3, 6]
    elif ext in ("o7", "dim7"):
        q = [0, 3, 6, 9]
    elif ext in ("ø", "ø7", "m7b5"):
        q = [0, 3, 6, 10]
    elif ext == "+":
        q = [0, 4, 8]
    elif ext == "7":
        q = [0, 4, 7, 10] if upper else [0, 3, 7, 10]
        if upper and acc == "" and deg in (0, 3) and scale in ("major", "ionian", "lydian"):
            q = [0, 4, 7, 11]
    elif ext in ("maj7", "M7"):
        q = [0, 4, 7, 11] if upper else [0, 3, 7, 11]
    elif ext in CHORD_QUALITIES and ext:
        q = CHORD_QUALITIES[ext]
    else:
        q = [0, 4, 7] if upper else [0, 3, 7]
    return [root + i for i in q]


def voice_lead(chords: List[List[int]], center: int = 64) -> List[List[int]]:
    """Choose inversions so that consecutive chords move minimally."""
    out: List[List[int]] = []
    prev: Optional[List[int]] = None
    for ch in chords:
        cands = []
        base = sorted(ch)
        for inv in range(len(base)):
            v = base[inv:] + [n + 12 for n in base[:inv]]
            for sh in (-24, -12, 0, 12):
                cands.append([n + sh for n in v])
        if prev is None:
            best = min(cands, key=lambda v: abs(sum(v) / len(v) - center))
        else:
            pm = sum(prev) / len(prev)
            best = min(cands, key=lambda v: sum(min(abs(n - p) for p in prev) for n in v) + abs(sum(v) / len(v) - pm) * 0.5)
        out.append(best)
        prev = best
    return out


def chord_progression(chords: Sequence[str], key: Optional[str] = None, beats_per_chord: float = 4.0,
                      octave: int = 4, velocity: int = 90, voicing: str = "voice_lead",
                      rhythm: str = "sustain", bass: bool = False) -> List[Note]:
    """Chords as symbols ('Am', 'F', 'C/E') or roman numerals (with key)."""
    parsed = []
    for c in chords:
        if key and re.fullmatch(r"[b#]?[ivIV]+.*", c.strip()):
            parsed.append(roman_to_chord(c, key, octave))
        else:
            parsed.append(parse_chord(c, octave))
    if voicing == "voice_lead":
        parsed = voice_lead(parsed, center=(octave + 1) * 12 + 4)
    notes: List[Note] = []
    t = 0.0
    for ch in parsed:
        hits = _rhythm_hits(rhythm, beats_per_chord)
        for (start, dur, accent) in hits:
            for n in ch:
                notes.append(_n(n, t + start, dur, int(velocity * accent)))
        if bass:
            notes.append(_n(min(ch) - 12 if min(ch) - 12 >= 28 else min(ch), t, beats_per_chord, velocity))
        t += beats_per_chord
    return notes


def _rhythm_hits(rhythm: str, length: float) -> List[Tuple[float, float, float]]:
    if rhythm == "sustain":
        return [(0.0, length * 0.98, 1.0)]
    if rhythm == "quarters":
        return [(b, 0.9, 1.0 if b % 2 == 0 else 0.85) for b in _frange(0, length, 1.0)]
    if rhythm == "eighths":
        return [(b, 0.45, 1.0 if b % 1 == 0 else 0.8) for b in _frange(0, length, 0.5)]
    if rhythm == "offbeat":
        return [(b + 0.5, 0.4, 0.95) for b in _frange(0, length, 1.0)]
    if rhythm == "charleston":
        return [(b, 0.9, 1.0) for b in _frange(0, length, 4.0)] + [(b + 1.5, 0.5, 0.9) for b in _frange(0, length, 4.0)]
    if rhythm == "pop":
        hits = []
        for b in _frange(0, length, 4.0):
            hits += [(b, 1.4, 1.0), (b + 1.5, 1.0, 0.85), (b + 2.5, 1.4, 0.9)]
        return [h for h in hits if h[0] < length]
    raise ValueError("rhythm must be sustain|quarters|eighths|offbeat|charleston|pop")


def _frange(a: float, b: float, step: float) -> Iterable[float]:
    x = a
    while x < b - 1e-9:
        yield x
        x += step


def _n(pitch: int, start: float, dur: float, vel: int) -> Note:
    return {"pitch": int(pitch), "start": round(start, 4), "duration": round(max(0.01, dur), 4),
            "velocity": int(max(1, min(127, vel)))}


DRUM_STYLES: Dict[str, Dict[str, str]] = {
    # 16 steps per bar, x = hit, X = accent, o = ghost
    "rock": {"kick": "X.......X.x.....", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "pop": {"kick": "X.....x.X.......", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x.", "clap": "....x.......x..."},
    "house": {"kick": "X...X...X...X...", "clap": "....X.......X...", "closed_hat": "..x...x...x...x."},
    "techno": {"kick": "X...X...X...X...", "closed_hat": "..x...x...x...x.", "clap": "....x.......x...", "ride": "x.x.x.x.x.x.x.x."},
    "hiphop": {"kick": "X......x..X.....", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "boom_bap": {"kick": "X.....x...X..x..", "snare": "....X.......X..o", "closed_hat": "x.x.x.xxx.x.x.x."},
    "trap": {"kick": "X......X..X.....", "snare": "........X.......", "closed_hat": "xxxxxxxxxxxx.xxx", "open_hat": "..............x."},
    "dnb": {"kick": "X.........X.....", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "reggaeton": {"kick": "X...X...X...X...", "snare": "...x..x....x..x.", "closed_hat": "x.x.x.x.x.x.x.x."},
    "funk": {"kick": "X..x..x...X..x..", "snare": "....X..o.o..X..o", "closed_hat": "xxxxxxxxxxxxxxxx"},
    "disco": {"kick": "X...X...X...X...", "snare": "....X.......X...", "open_hat": "..x...x...x...x.", "closed_hat": "x...x...x...x..."},
    "afrobeat": {"kick": "X..x....X..x....", "snare": "....x..x....x...", "closed_hat": "x.xxx.xxx.xxx.xx", "shaker": "xxxxxxxxxxxxxxxx"},
    "bossa": {"kick": "X..xX..xX..xX..x", "rim": "x..x..x...x..x..", "closed_hat": "xxxxxxxxxxxxxxxx"},
    "halftime": {"kick": "X.........x.....", "snare": "........X.......", "closed_hat": "x.x.x.x.x.x.x.x."},
    "breakbeat": {"kick": "X.........X..x..", "snare": "....X..o.o..X...", "closed_hat": "x.x.x.x.x.x.x.x."},
}


def drum_pattern(style: str = "rock", bars: int = 1, swing: float = 0.0, velocity: int = 100,
                 humanize: float = 0.0, fill: bool = False, custom: Optional[Dict[str, str]] = None,
                 seed: Optional[int] = None) -> List[Note]:
    """Step-sequenced drum pattern on the GM drum map (16 steps per bar).

    ``custom`` lets you pass your own grid, e.g. {"kick": "X...X...", "snare": "....X..."}.
    """
    rng = random.Random(seed)
    grid = custom or DRUM_STYLES.get(style)
    if grid is None:
        raise ValueError(f"unknown style '{style}'. Known: {', '.join(sorted(DRUM_STYLES))}")
    notes: List[Note] = []
    for bar in range(bars):
        is_fill = fill and bar == bars - 1
        if bar == 0 and fill and bars > 1:
            notes.append(_n(DRUMS["crash"], 0.0, 1.0, velocity))
        for inst, pat in grid.items():
            pitch = DRUMS.get(inst, None) if not str(inst).isdigit() else int(inst)
            if pitch is None:
                raise ValueError(f"unknown drum '{inst}'. Known: {', '.join(DRUMS)}")
            steps = len(pat)
            step_len = 4.0 / steps
            for i, ch in enumerate(pat):
                if ch not in "xXo":
                    continue
                if is_fill and i >= steps * 3 // 4 and inst not in ("kick", "crash"):
                    continue
                vel = velocity if ch == "x" else (min(127, velocity + 20) if ch == "X" else int(velocity * 0.45))
                start = bar * 4 + i * step_len
                if swing and i % 2 == 1:
                    start += step_len * swing * 0.5
                if humanize:
                    start += rng.uniform(-humanize, humanize) * step_len * 0.25
                    vel += int(rng.uniform(-humanize, humanize) * 15)
                notes.append(_n(pitch, max(0.0, start), step_len * 0.9, vel))
        if is_fill:
            toms = [DRUMS["high_tom"], DRUMS["mid_tom"], DRUMS["low_tom"], DRUMS["floor_tom"]]
            for i in range(4):
                notes.append(_n(toms[i], bar * 4 + 3 + i * 0.25, 0.22, velocity))
    return sorted(notes, key=lambda n: (n["start"], n["pitch"]))


def bassline(chords: Sequence[str], key: Optional[str] = None, beats_per_chord: float = 4.0,
             style: str = "root", octave: int = 2, velocity: int = 100, seed: Optional[int] = None) -> List[Note]:
    """Bass line following chords. styles: root, root_fifth, octave, walking, eighths, syncopated, arp."""
    rng = random.Random(seed)
    roots = []
    for c in chords:
        if key and re.fullmatch(r"[b#]?[ivIV]+.*", c.strip()):
            ch = roman_to_chord(c, key, octave)
        else:
            ch = parse_chord(c, octave)
        roots.append(ch)
    notes: List[Note] = []
    t = 0.0
    for idx, ch in enumerate(roots):
        r = ch[0]
        third = ch[1] if len(ch) > 1 else r + 4
        fifth = ch[2] if len(ch) > 2 else r + 7
        nxt = roots[(idx + 1) % len(roots)][0]
        L = beats_per_chord
        if style == "root":
            notes.append(_n(r, t, L * 0.95, velocity))
        elif style == "root_fifth":
            for b in _frange(0, L, 2.0):
                notes.append(_n(r if (b / 2) % 2 == 0 else fifth - 12 if fifth - 12 > 24 else fifth, t + b, 1.9, velocity))
        elif style == "octave":
            for b in _frange(0, L, 0.5):
                notes.append(_n(r + (12 if (b * 2) % 2 else 0), t + b, 0.45, velocity if (b * 2) % 2 == 0 else velocity - 15))
        elif style == "eighths":
            for b in _frange(0, L, 0.5):
                notes.append(_n(r, t + b, 0.45, velocity if b % 1 == 0 else velocity - 20))
        elif style == "syncopated":
            for b, d in ((0, 0.75), (0.75, 0.25), (1.5, 0.5), (2.5, 0.5), (3.0, 0.75), (3.75, 0.25)):
                if b < L:
                    notes.append(_n(r if b not in (2.5,) else fifth, t + b, d * 0.95, velocity))
        elif style == "walking":
            beats = int(L)
            line = [r, third, fifth]
            while len(line) < beats - 1:
                line.append(fifth + rng.choice([2, -1, 1]))
            approach = nxt + rng.choice([-1, 1, -2])
            for b in range(beats):
                p = line[b] if b < beats - 1 else approach
                notes.append(_n(p, t + b, 0.9, velocity - (10 if b % 2 else 0)))
        elif style == "arp":
            seq = [r, fifth, r + 12, fifth]
            for i, b in enumerate(_frange(0, L, 0.5)):
                notes.append(_n(seq[i % 4], t + b, 0.45, velocity))
        else:
            raise ValueError("style must be root|root_fifth|octave|eighths|syncopated|walking|arp")
        t += L
    return notes


def arpeggio(chords: Sequence[str], key: Optional[str] = None, beats_per_chord: float = 4.0,
             pattern: str = "up", rate: float = 0.25, octave: int = 4, octaves: int = 1,
             gate: float = 0.9, velocity: int = 90, seed: Optional[int] = None) -> List[Note]:
    """Arpeggiate chords. pattern: up, down, updown, random, converge; rate in beats (0.25 = 16ths)."""
    rng = random.Random(seed)
    notes: List[Note] = []
    t = 0.0
    for c in chords:
        ch = roman_to_chord(c, key, octave) if key and re.fullmatch(r"[b#]?[ivIV]+.*", c.strip()) else parse_chord(c, octave)
        pool = sorted(n + 12 * o for o in range(octaves) for n in ch)
        if pattern == "down":
            seq = pool[::-1]
        elif pattern == "updown":
            seq = pool + pool[-2:0:-1]
        elif pattern == "converge":
            seq = []
            a, b = 0, len(pool) - 1
            while a <= b:
                seq.append(pool[a])
                if a != b:
                    seq.append(pool[b])
                a += 1
                b -= 1
        else:
            seq = pool
        for i, b in enumerate(_frange(0, beats_per_chord, rate)):
            p = rng.choice(pool) if pattern == "random" else seq[i % len(seq)]
            notes.append(_n(p, t + b, rate * gate, velocity - (12 if i % 2 else 0)))
        t += beats_per_chord
    return notes


def melody(key: str = "C major", bars: int = 4, octave: int = 5, density: float = 0.6,
           chords: Optional[Sequence[str]] = None, beats_per_chord: float = 4.0, contour: str = "arch",
           velocity: int = 95, seed: Optional[int] = None) -> List[Note]:
    """Generate a scale-aware melody. Strong beats prefer chord tones when ``chords`` are given.

    contour: arch | rising | falling | wave | random
    """
    rng = random.Random(seed)
    sc = scale_notes(key, octave - 1, 3)
    chord_sets = []
    if chords:
        for c in chords:
            ch = roman_to_chord(c, key, octave) if re.fullmatch(r"[b#]?[ivIV]+.*", c.strip()) else parse_chord(c, octave)
            chord_sets.append({n % 12 for n in ch})
    total = bars * 4.0
    rhythm_cells = [[1.0], [0.5, 0.5], [0.75, 0.25], [0.25, 0.25, 0.5], [1.5, 0.5], [2.0], [0.5, 0.25, 0.25]]
    t = 0.0
    idx = len(sc) // 2
    notes: List[Note] = []
    while t < total - 1e-6:
        cell = rng.choice(rhythm_cells if density > 0.4 else rhythm_cells[:2] + [[2.0], [1.5, 0.5]])
        for d in cell:
            if t >= total - 1e-6:
                break
            prog = t / total
            if contour == "arch":
                target = int(len(sc) * (0.35 + 0.4 * (1 - abs(prog - 0.5) * 2)))
            elif contour == "rising":
                target = int(len(sc) * (0.25 + 0.5 * prog))
            elif contour == "falling":
                target = int(len(sc) * (0.75 - 0.5 * prog))
            elif contour == "wave":
                import math
                target = int(len(sc) * (0.5 + 0.25 * math.sin(prog * 6.283 * 2)))
            else:
                target = rng.randrange(len(sc))
            step = rng.choice([-2, -1, -1, 0, 1, 1, 2]) + (1 if target > idx else -1 if target < idx else 0)
            idx = max(0, min(len(sc) - 1, idx + step))
            pitch = sc[idx]
            if chord_sets and abs(t % 1.0) < 1e-6:
                cs = chord_sets[int(t // beats_per_chord) % len(chord_sets)]
                if pitch % 12 not in cs:
                    for delta in (1, -1, 2, -2):
                        j = max(0, min(len(sc) - 1, idx + delta))
                        if sc[j] % 12 in cs:
                            idx = j
                            pitch = sc[j]
                            break
            if rng.random() < density or abs(t % 4.0) < 1e-6:
                accent = 10 if abs(t % 1.0) < 1e-6 else 0
                notes.append(_n(pitch, t, d * 0.92, velocity - 10 + accent + rng.randint(-5, 5)))
            t += d
    return notes


def transpose(notes: List[Note], semitones: int) -> List[Note]:
    return [{**n, "pitch": int(max(0, min(127, n["pitch"] + semitones)))} for n in notes]


def shift(notes: List[Note], beats: float) -> List[Note]:
    return [{**n, "start": round(n["start"] + beats, 4)} for n in notes]


def quantize(notes: List[Note], grid: float = 0.25, strength: float = 1.0) -> List[Note]:
    out = []
    for n in notes:
        q = round(n["start"] / grid) * grid
        out.append({**n, "start": round(n["start"] + (q - n["start"]) * strength, 4)})
    return out


def humanize(notes: List[Note], timing: float = 0.01, velocity: int = 8, seed: Optional[int] = None) -> List[Note]:
    rng = random.Random(seed)
    return [{**n, "start": round(max(0.0, n["start"] + rng.uniform(-timing, timing)), 4),
             "velocity": int(max(1, min(127, n["velocity"] + rng.randint(-velocity, velocity))))} for n in notes]


def normalize_notes(notes: Iterable[dict]) -> List[Note]:
    """Accept flexible note dicts (pitch may be 'C4'; start_time/time aliases) -> canonical."""
    out = []
    for n in notes:
        pitch = note_to_midi(n.get("pitch", n.get("note")))
        start = float(n.get("start", n.get("start_time", n.get("time", 0.0))))
        dur = float(n.get("duration", n.get("length", 0.5)))
        vel = int(n.get("velocity", 100))
        o = _n(pitch, start, dur, vel)
        if "channel" in n:
            o["channel"] = int(n["channel"])
        if n.get("mute"):
            continue
        out.append(o)
    return sorted(out, key=lambda x: (x["start"], x["pitch"]))


def write_midi_file(path: str, tracks: Dict[str, List[Note]], tempo: float = 120.0,
                    time_signature: Tuple[int, int] = (4, 4), ppq: int = 480) -> str:
    """Write a multi-track Standard MIDI File (type 1). Drum tracks named 'drums*' use channel 10."""
    import mido

    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=time_signature[0], denominator=time_signature[1], time=0))
    mid.tracks.append(meta)
    for name, notes in tracks.items():
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name, time=0))
        default_ch = 9 if name.lower().startswith("drum") else 0
        events = []
        for n in normalize_notes(notes):
            ch = n.get("channel", default_ch)
            on = int(round(n["start"] * ppq))
            off = int(round((n["start"] + n["duration"]) * ppq))
            events.append((on, 1, mido.Message("note_on", note=n["pitch"], velocity=n["velocity"], channel=ch)))
            events.append((off, 0, mido.Message("note_off", note=n["pitch"], velocity=0, channel=ch)))
        events.sort(key=lambda e: (e[0], e[1]))
        last = 0
        for tick, _, msg in events:
            tr.append(msg.copy(time=tick - last))
            last = tick
        mid.tracks.append(tr)
    mid.save(path)
    return path
