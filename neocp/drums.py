"""Drum transcription: audio loop -> kick / snare / hi-hat (closed, open) MIDI on the GM map.

Each attack (high-resolution spectral-flux onsets) is described by band energies right after the hit
(sub/low, body, crack, sizzle), spectral flatness (noise vs tone) and the decay of the top band.
Labels are assigned per band relative to the loudest hit of that kind in the file, so one hit can be
several instruments at once (kick + hat on the downbeat).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .transcribe import SR, onsets, resample

GM = {"kick": 36, "snare": 38, "closed_hat": 42, "open_hat": 46, "clap": 39, "tom": 45, "crash": 49}


def _band(spec: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> float:
    m = (freqs >= lo) & (freqs < hi)
    return float((spec[m] ** 2).sum())


def hit_features(x: np.ndarray, t: float) -> Optional[Dict[str, float]]:
    s = int(max(0, t - 0.005) * SR)
    seg = x[s:s + int(0.06 * SR)]
    tail = x[s + int(0.12 * SR):s + int(0.22 * SR)]
    if len(seg) < 256:
        return None
    n = 2048
    win = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * win, n))
    f = np.fft.rfftfreq(n, 1 / SR)
    feats = {
        "low": _band(S, f, 30, 140), "body": _band(S, f, 140, 400), "crack": _band(S, f, 1500, 5000),
        "sizzle": _band(S, f, 6000, 11000), "total": float((S ** 2).sum()) + 1e-12,
    }
    sel = (f > 150) & (f < 10000)
    p = S[sel] ** 2 + 1e-12
    feats["flatness"] = float(np.exp(np.mean(np.log(p))) / np.mean(p))
    if len(tail) > 256:
        T = np.abs(np.fft.rfft(tail * np.hanning(len(tail)), n))
        feats["sizzle_decay"] = _band(T, f, 6000, 11000) / (feats["sizzle"] + 1e-12)
    else:
        feats["sizzle_decay"] = 0.0
    feats["peak"] = float(np.abs(seg).max())
    return feats


def transcribe_drums(audio: np.ndarray, sr: int, bpm: float = 120.0, grid: Optional[float] = 0.25,
                     sensitivity: float = 1.0) -> Dict[str, Any]:
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    x = resample(x, sr)
    pad = int(0.1 * SR)
    x = np.concatenate([np.zeros(pad, dtype=np.float32), x])  # so a hit at t=0 has an onset

    def band(sig, lo, hi):
        X = np.fft.rfft(sig)
        f = np.fft.rfftfreq(len(sig), 1 / SR)
        X[(f < lo) | (f >= hi)] = 0
        return np.fft.irfft(X, len(sig)).astype(np.float32)
    # per-band onsets: quiet hats are not masked by loud kicks
    low_ons = onsets(band(x, 20, 200), n_fft=1024, hop=64, min_gap_s=0.05)
    high_ons = onsets(band(x, 5000, SR / 2), n_fft=512, hop=64, min_gap_s=0.03)
    allo = np.concatenate([onsets(x, n_fft=512, hop=64, min_gap_s=0.03), low_ons, high_ons])

    def near(arr, t, tol):
        return arr.size > 0 and float(np.min(np.abs(arr - t))) <= tol
    allo.sort()
    ons = []
    for t in allo:
        if not ons or t - ons[-1] > 0.025:
            ons.append(float(t))
    hits = []
    for t in ons:
        ft = hit_features(x, float(t))
        if ft and t >= pad / SR - 0.02:
            ft["low_onset"] = near(low_ons, t, 0.035)
        ft["high_onset"] = near(high_ons, t, 0.02)
        hits.append((float(t) - pad / SR, ft))
    if not hits:
        return {"bpm": bpm, "count": 0, "notes": [], "hits": 0}
    mx = {k: max(h[1][k] for h in hits) + 1e-12 for k in ("low", "body", "crack", "sizzle")}
    # hats are judged against hat-like hits (top band dominant), not against noisy snares
    hatlike = [h[1]["sizzle"] for h in hits if h[1]["sizzle"] / (h[1]["sizzle"] + h[1]["crack"] + h[1]["body"] + 1e-12) > 0.6]
    if hatlike:
        mx["sizzle"] = max(hatlike) + 1e-12
    thr = 0.12 / max(0.2, sensitivity)
    notes = []
    spb = 60.0 / bpm
    stats = {k: 0 for k in GM}
    last_kick = (-1.0, 0.0)
    for t, ft in hits:
        tot = ft["total"]
        low_r = ft["low"] / tot
        labels = []
        # amplitude ratios (sqrt of energy) relative to the loudest hit of each kind
        kick_e = float(np.sqrt(ft["low"] / mx["low"]))
        if kick_e > thr * 2.5 and low_r > 0.25 and ft["low_onset"]:
            # the pitch sweep of a kick can re-trigger: ignore a weaker "kick" right after a kick
            if not (t - last_kick[0] < 0.2 and kick_e < 1.2 * last_kick[1]):
                labels.append(("kick", kick_e))
                last_kick = (t, kick_e)
        snare_e = float(np.sqrt((ft["body"] + ft["crack"]) / (mx["body"] + mx["crack"])))
        if snare_e > thr * 2 and ft["flatness"] > 0.05 and (ft["crack"] + ft["body"]) / tot > 0.25 and low_r < 0.6:
            labels.append(("snare", snare_e))
        hat_e = min(1.0, float(np.sqrt(ft["sizzle"] / mx["sizzle"])))
        top_r = ft["sizzle"] / (ft["sizzle"] + ft["crack"] + 1e-12)
        snare_hit = bool(labels) and labels[-1][0] == "snare"
        if hat_e > thr * 2 and top_r > 0.5 and ft["high_onset"] and (not snare_hit or ft["sizzle"] > 2.0 * ft["crack"]):
            labels.append(("open_hat" if ft["sizzle_decay"] > 0.25 else "closed_hat", hat_e))
        beat = t / spb
        if grid:
            beat = round(beat / grid) * grid
        for name, strength in labels:
            vel = int(max(30, min(127, 30 + 97 * np.sqrt(min(1.0, strength)))))
            notes.append({"pitch": GM[name], "start": round(beat, 4), "duration": 0.25 if grid else 0.1,
                          "velocity": vel, "drum": name})
            stats[name] += 1
    uniq = {}
    for n in notes:  # one note per instrument per grid slot, keep the loudest
        k = (n["pitch"], n["start"])
        if k not in uniq or uniq[k]["velocity"] < n["velocity"]:
            uniq[k] = n
    notes = sorted(uniq.values(), key=lambda n: (n["start"], n["pitch"]))
    swing = None
    if grid:
        offs = [((t / spb) / 0.25) % 2 for t, _ in hits]  # position inside a pair of 16ths
        odd = [o - 1 for o in offs if 0.5 < o < 1.75]
        if len(odd) >= 4:
            swing = round(float(np.median(odd)) * 100, 1)  # % of a 16th the off-beats are late
    grid_txt = {}
    if notes:
        bars = int(max(n["start"] for n in notes) // 4) + 1
        for name in ("kick", "snare", "closed_hat", "open_hat"):
            row = ["."] * (16 * min(bars, 4))
            for n in notes:
                if n["drum"] == name and n["start"] < 4 * min(bars, 4):
                    i = int(round(n["start"] / 0.25))
                    if i < len(row):
                        row[i] = "X" if n["velocity"] > 100 else "x"
            grid_txt[name] = " | ".join("".join(row[i:i + 16]) for i in range(0, len(row), 16))
    return {"bpm": bpm, "hits": len(hits), "count": len(notes), "per_instrument": stats, "swing_percent": swing,
            "grid": grid_txt, "notes": notes}
