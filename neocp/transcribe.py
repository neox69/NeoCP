"""Polyphonic transcription (audio -> MIDI notes), numpy only.

Method
1. Resample to 22.05 kHz, STFT (8192 / hop 512).
2. Map the magnitude spectrum to a log-frequency axis (3 bins per semitone).
3. Non-negative matrix factorisation with a FIXED dictionary of harmonic templates (one per piano key,
   built in the linear domain with the same window blur, then mapped with the same matrix) – only the
   activations are learned (KL divergence, multiplicative updates, sparsity penalty).
4. Note tracking with hysteresis, minimum length, gap merging, onset re-triggering, and removal of
   harmonic "ghost" notes (octave / twelfth above a much stronger simultaneous note).
5. Optional split into bass / chords / melody parts, chord labelling and grid quantisation.

Works best on clean polyphonic material (piano, guitar, pads, synth chords, a mix without loud drums).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

SR = 22050
N_FFT = 8192
HOP = 512
BINS_PER_SEMI = 3
LOW_MIDI, HIGH_MIDI = 28, 96  # E1 .. C7 (fundamentals)
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def note_name(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def resample(x: np.ndarray, sr: int, target: int = SR) -> np.ndarray:
    if sr == target:
        return x.astype(np.float32)
    n = len(x)
    m = int(round(n * target / sr))
    X = np.fft.rfft(x)
    keep = min(len(X), m // 2 + 1)
    Y = np.zeros(m // 2 + 1, dtype=complex)
    Y[:keep] = X[:keep]
    return (np.fft.irfft(Y, n=m) * (m / n)).astype(np.float32)


def _stft(x: np.ndarray) -> np.ndarray:
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    x = np.pad(x, (N_FFT // 2, N_FFT // 2))
    n = 1 + (len(x) - N_FFT) // HOP
    win = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, N_FFT // 2 + 1), dtype=np.float32)
    for s in range(0, n, 256):  # chunked to bound memory
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(s, min(n, s + 256))[:, None]
        out[s:s + idx.shape[0]] = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    return out


_CACHE: Dict[str, Any] = {}


def _logmap() -> Tuple[np.ndarray, np.ndarray]:
    """Linear-bin -> log-bin weight matrix (triangular), and log-bin centre frequencies."""
    if "map" in _CACHE:
        return _CACHE["map"]
    lin = np.fft.rfftfreq(N_FFT, 1 / SR)
    lo = midi_hz(LOW_MIDI - 1)
    hi = min(SR / 2 * 0.95, midi_hz(HIGH_MIDI) * 6)
    nb = int(math.floor(12 * BINS_PER_SEMI * math.log2(hi / lo)))
    centres = lo * 2 ** (np.arange(nb) / (12 * BINS_PER_SEMI))
    M = np.zeros((len(lin), nb), dtype=np.float32)
    ratio = 2 ** (1 / (12 * BINS_PER_SEMI))
    for k, fc in enumerate(centres):
        fl, fh = fc / ratio, fc * ratio
        w = np.where(lin < fc, (lin - fl) / (fc - fl), (fh - lin) / (fh - fc))
        w = np.clip(w, 0, None)
        if w.sum() == 0:  # bin narrower than linear resolution: nearest bin
            w[np.argmin(np.abs(lin - fc))] = 1.0
        M[:, k] = w / (w.sum() + 1e-12)
    _CACHE["map"] = (M, centres)
    return M, centres


def _dictionary(n_harm: int = 10, decay: float = 0.9) -> np.ndarray:
    """Harmonic templates (notes x log bins), built with the Hann main-lobe blur, L1-normalised."""
    if "W" in _CACHE:
        return _CACHE["W"]
    M, _ = _logmap()
    lin = np.fft.rfftfreq(N_FFT, 1 / SR)
    df = lin[1]
    sigma = 1.2 * df  # Hann main lobe ~ +-2 bins
    notes = np.arange(LOW_MIDI, HIGH_MIDI + 1)
    W = np.zeros((len(notes), M.shape[1]), dtype=np.float32)
    for i, m in enumerate(notes):
        f0 = midi_hz(m)
        t = np.zeros(len(lin), dtype=np.float64)
        for h in range(1, n_harm + 1):
            fh = f0 * h
            if fh >= SR / 2 * 0.95:
                break
            amp = 1.0 / h ** decay
            lo_i, hi_i = int((fh - 5 * sigma) / df), int((fh + 5 * sigma) / df) + 1
            sl = slice(max(0, lo_i), min(len(lin), hi_i))
            t[sl] += amp * np.exp(-0.5 * ((lin[sl] - fh) / sigma) ** 2)
        w = t.astype(np.float32) @ M
        W[i] = w / (w.sum() + 1e-12)
    _CACHE["W"] = W
    return W


def activations(audio: np.ndarray, sr: int, iterations: int = 60, sparsity: float = 0.08,
                return_signal: bool = False):
    """Return (H: notes x frames activations, frame duration in seconds[, 22.05 kHz mono signal])."""
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    x = resample(x, sr)
    S = _stft(x)
    M, _ = _logmap()
    V = (S ** 2) @ M           # power on log-frequency axis (frames x bins)
    V = np.sqrt(V).T + 1e-9    # magnitude, bins x frames
    W = _dictionary().T        # bins x notes
    H = np.full((W.shape[1], V.shape[1]), V.mean() / W.shape[1] + 1e-6, dtype=np.float32)
    Wsum = W.sum(axis=0)[:, None]
    for _ in range(iterations):  # KL-NMF with W fixed + L1 sparsity
        R = V / (W @ H + 1e-9)
        H *= (W.T @ R) / (Wsum + sparsity)
    if return_signal:
        return H, HOP / SR, x
    return H, HOP / SR


def onsets(x22: np.ndarray, n_fft: int = 1024, hop: int = 128, min_gap_s: float = 0.05) -> np.ndarray:
    """Onset times (s) from log spectral flux on a short window (high time resolution)."""
    if len(x22) < n_fft:
        return np.zeros(0)
    n = 1 + (len(x22) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    flux = np.zeros(n, dtype=np.float32)
    prev = None
    for s in range(0, n, 2048):
        idx = np.arange(n_fft)[None, :] + hop * np.arange(s, min(n, s + 2048))[:, None]
        L = np.log1p(100 * np.abs(np.fft.rfft(x22[idx] * win, axis=1)))
        if prev is not None:
            L = np.vstack([prev[None, :], L])
            d = np.maximum(0, np.diff(L, axis=0)).sum(axis=1)
            flux[s:s + len(d)] = d
        else:
            d = np.maximum(0, np.diff(L, axis=0)).sum(axis=1)
            flux[s + 1:s + 1 + len(d)] = d
        prev = L[-1]
    w = int(0.25 * SR / hop)
    pad = np.pad(flux, (w, w), mode="edge")
    loc = np.array([pad[i:i + 2 * w + 1].mean() for i in range(len(flux))]) if len(flux) < 20000 else \
        np.convolve(pad, np.ones(2 * w + 1) / (2 * w + 1), mode="valid")
    thr = loc + 1.5 * flux.std()
    gap = max(1, int(min_gap_s * SR / hop))
    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > thr[i] and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
            if peaks and i - peaks[-1] < gap:
                if flux[i] > flux[peaks[-1]]:
                    peaks[-1] = i
                continue
            peaks.append(i)
    # time of the window centre where the flux rises ~ onset
    return (np.array(peaks) * hop + n_fft / 2) / SR


def track_notes(H: np.ndarray, frame_s: float, threshold: float = 0.12, min_note_s: float = 0.07,
                max_gap_s: float = 0.05, max_polyphony: int = 6, ghost_ratio: float = 0.2,
                onset_times: Optional[np.ndarray] = None, compress: float = 1.0) -> List[Dict[str, Any]]:
    """Activations -> notes with hysteresis; drops harmonic ghosts and limits polyphony. With onset_times,
    starts snap to the real attacks and sustained notes are split where they are re-struck."""
    A = H / (H.max() + 1e-12)
    if compress != 1.0:
        A = A ** compress  # lift quiet voices (pads under a lead)
    # temporal smoothing (3 frames)
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    A = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, A)
    n_notes, n_frames = A.shape
    # ghost suppression: a note an octave/twelfth/two octaves above a much stronger one is a harmonic
    G = A.copy()
    for i in range(n_notes):
        for iv in (12, 19, 24, 28, 31):
            j = i - iv
            if j >= 0:
                ghost = A[i] < ghost_ratio * A[j]
                G[i, ghost] *= 0.25
    # semitone neighbours: spectral leakage (mainly in the bass) creates a weaker note next to a strong one
    for i in range(n_notes):
        for j in (i - 1, i + 1):
            if 0 <= j < n_notes:
                weak = A[i] < 0.6 * A[j]
                G[i, weak] *= 0.2
    # polyphony limit per frame
    if max_polyphony and n_notes > max_polyphony:
        order = np.argsort(-G, axis=0)
        mask = np.zeros_like(G, dtype=bool)
        np.put_along_axis(mask, order[:max_polyphony], True, axis=0)
        G = np.where(mask, G, 0)
    on_thr, off_thr = threshold, threshold * 0.5
    min_f = max(1, int(round(min_note_s / frame_s)))
    gap_f = int(round(max_gap_s / frame_s))
    notes: List[Dict[str, Any]] = []
    for i in range(n_notes):
        row = G[i]
        active = False
        start = 0
        segs = []
        for t in range(n_frames):
            v = row[t]
            if not active and v >= on_thr:
                active, start = True, t
            elif active and v < off_thr:
                segs.append((start, t))
                active = False
        if active:
            segs.append((start, n_frames))
        merged: List[List[int]] = []
        for s, e in segs:
            if merged and s - merged[-1][1] <= gap_f:
                merged[-1][1] = e
            else:
                merged.append([s, e])
        ons = onset_times if onset_times is not None else np.zeros(0)
        for s, e in merged:
            t_s, t_e = s * frame_s, e * frame_s
            # snap the start to the closest attack slightly after / before the activation rise
            cand = ons[(ons >= t_s - 0.12) & (ons <= t_s + 0.30)]
            if cand.size:
                t_s = float(cand[0])
            cuts = [t_s]
            # re-strike: an attack inside the note where this note's activation rises again
            for o in ons[(ons > t_s + min_note_s) & (ons < t_e - min_note_s)]:
                f = int(round(o / frame_s))
                before = row[max(0, f - 6):max(1, f - 2)].min() if f - 2 > 0 else row[0]
                after = row[f:min(n_frames, f + 8)].max()
                if after > 1.2 * before + 0.02:
                    cuts.append(float(o))
            cuts.append(t_e)
            for a, b in zip(cuts, cuts[1:]):
                if b - a < min_note_s:
                    continue
                fa, fb = int(a / frame_s), max(int(a / frame_s) + 1, int(b / frame_s))
                peak = float(row[fa:fb].max())
                notes.append({"pitch": LOW_MIDI + i, "start_s": round(a, 4), "end_s": round(b, 4),
                              "strength": round(peak, 3)})
    notes.sort(key=lambda n: (n["start_s"], n["pitch"]))
    return notes


def to_beats(notes: List[Dict[str, Any]], bpm: float, quantize: Optional[float] = 0.25,
             offset_s: float = 0.0) -> List[Dict[str, Any]]:
    spb = 60.0 / bpm
    out = []
    for n in notes:
        st = (n["start_s"] - offset_s) / spb
        du = (n["end_s"] - n["start_s"]) / spb
        if quantize:
            st = round(st / quantize) * quantize
            du = max(quantize, round(du / quantize) * quantize)
        vel = int(max(25, min(127, 35 + 92 * math.sqrt(n["strength"]))))
        out.append({"pitch": n["pitch"], "start": round(max(0.0, st), 4), "duration": round(du, 4),
                    "velocity": vel, "name": note_name(n["pitch"])})
    # remove duplicates created by quantisation
    seen = set()
    uniq = []
    for n in out:
        k = (n["pitch"], n["start"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    return uniq


def split_parts(notes: List[Dict[str, Any]], bass_max: int = 52) -> Dict[str, List[Dict[str, Any]]]:
    """bass = lowest note below bass_max at each time; melody = highest note when clearly above the chord;
    chords = the rest."""
    bass, chords, melody = [], [], []
    by_start: Dict[float, List[Dict[str, Any]]] = {}
    for n in notes:
        by_start.setdefault(n["start"], []).append(n)
    for st, group in sorted(by_start.items()):
        group = sorted(group, key=lambda n: n["pitch"])
        if group[0]["pitch"] <= bass_max:
            bass.append(group[0])
            group = group[1:]
        if len(group) >= 3 and group[-1]["pitch"] - group[-2]["pitch"] >= 3:
            melody.append(group[-1])
            group = group[:-1]
        elif len(group) == 1 and group[0]["pitch"] >= 72:
            melody.append(group[0])
            group = []
        chords.extend(group)
    return {"bass": bass, "chords": chords, "melody": melody}


_TEMPL = None


def chord_labels(notes: List[Dict[str, Any]], beats_per_segment: float = 2.0) -> List[Dict[str, Any]]:
    """Name the harmony per segment from the transcribed notes."""
    global _TEMPL
    if _TEMPL is None:
        _TEMPL = []
        for name, ivs in (("", [0, 4, 7]), ("m", [0, 3, 7]), ("7", [0, 4, 7, 10]), ("maj7", [0, 4, 7, 11]),
                          ("m7", [0, 3, 7, 10]), ("dim", [0, 3, 6]), ("sus4", [0, 5, 7]), ("sus2", [0, 2, 7])):
            for r in range(12):
                v = np.zeros(12)
                v[[(r + i) % 12 for i in ivs]] = 1
                _TEMPL.append((NOTE_NAMES[r] + name, r, v / np.linalg.norm(v)))
    if not notes:
        return []
    end = max(n["start"] + n["duration"] for n in notes)
    out: List[Dict[str, Any]] = []
    t = 0.0
    while t < end - 1e-9:
        pc = np.zeros(12)
        low = None
        for n in notes:
            ov = min(n["start"] + n["duration"], t + beats_per_segment) - max(n["start"], t)
            if ov > 0:
                pc[n["pitch"] % 12] += ov * n["velocity"]
                if ov >= 0.25 * beats_per_segment and (low is None or n["pitch"] < low):
                    low = n["pitch"]
        if pc.sum() == 0:
            name = "N.C."
        else:
            v = pc / np.linalg.norm(pc)
            best = max(((float(v @ tv) + (0.05 if low is not None and low % 12 == r else 0), nm, r)
                        for nm, r, tv in _TEMPL))
            name, root = best[1], best[2]
            if low is not None and low % 12 != root and pc[low % 12] > 0:
                name = f"{name}/{NOTE_NAMES[low % 12]}"
        if out and out[-1]["chord"] == name:
            out[-1]["beats"] += beats_per_segment
        else:
            out.append({"chord": name, "beat": round(t, 3), "beats": beats_per_segment})
        t += beats_per_segment
    return out


def transcribe(audio: np.ndarray, sr: int, bpm: float = 120.0, quantize: Optional[float] = 0.25,
               threshold: float = 0.12, max_polyphony: int = 6, min_note_ms: float = 70.0,
               split: bool = True, bass_max: int = 52, chord_segment_beats: float = 2.0) -> Dict[str, Any]:
    H, frame_s, x22 = activations(audio, sr, return_signal=True)
    ons = onsets(x22)
    raw = track_notes(H, frame_s, threshold=threshold, min_note_s=min_note_ms / 1000.0, max_polyphony=max_polyphony,
                      onset_times=ons)
    notes = to_beats(raw, bpm, quantize)
    res: Dict[str, Any] = {"bpm": bpm, "count": len(notes), "notes": notes,
                           "pitch_range": [note_name(min(n["pitch"] for n in notes)),
                                           note_name(max(n["pitch"] for n in notes))] if notes else None,
                           "chords": chord_labels(notes, chord_segment_beats)}
    if split:
        parts = split_parts(notes, bass_max)
        res["parts"] = {k: v for k, v in parts.items()}
        res["part_counts"] = {k: len(v) for k, v in parts.items()}
    return res
