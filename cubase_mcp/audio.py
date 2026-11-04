"""Audio analysis ("ears") – pure numpy, no scipy needed.

Gives the model an objective description of what Cubase sounds like: loudness (LUFS / true peak /
LRA), dynamics, stereo image, spectral balance and resonances, tempo, key, chords and monophonic
melody transcription, plus mixing suggestions.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

BANDS = [("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 500), ("mid", 500, 2000),
         ("high_mid", 2000, 4000), ("presence", 4000, 6000), ("air", 6000, 20000)]


# ----------------------------------------------------------------------------- utils

def db(x: float, floor: float = -150.0) -> float:
    return float(max(floor, 20 * math.log10(max(float(x), 1e-12))))


def to_float(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio)
    if a.dtype.kind in "iu":
        a = a.astype(np.float32) / float(np.iinfo(a.dtype).max)
    a = a.astype(np.float32, copy=False)
    if a.ndim == 1:
        a = a[:, None]
    return a


def load_file(path: str, max_seconds: Optional[float] = None) -> Tuple[np.ndarray, int]:
    import soundfile as sf
    info = sf.info(path)
    frames = -1 if not max_seconds else int(max_seconds * info.samplerate)
    data, sr = sf.read(path, frames=frames, dtype="float32", always_2d=True)
    return data, sr


def save_wav(path: str, audio: np.ndarray, sr: int) -> str:
    import soundfile as sf
    sf.write(path, audio, sr, subtype="PCM_24")
    return path


def stft_mag(x: np.ndarray, n_fft: int = 4096, hop: int = 1024) -> np.ndarray:
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(x[idx] * win, axis=1)).astype(np.float32)


# ----------------------------------------------------------------------------- loudness

def _biquad_response(b, a, freqs, sr):
    z = np.exp(-1j * 2 * np.pi * freqs / sr)
    return (b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2)


def _k_weighting(freqs: np.ndarray, sr: int) -> np.ndarray:
    # BS.1770 pre-filter (high shelf) and RLB high-pass, derived for any sample rate
    G, Q, fc = 3.99984385397, 0.7071752369554193, 1681.9744509555319
    K = math.tan(math.pi * fc / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    h1 = _biquad_response(b, a, freqs, sr)
    Q2, fc2 = 0.5003270373238773, 38.13547087602444
    K = math.tan(math.pi * fc2 / sr)
    a0 = 1 + K / Q2 + K * K
    a = [1, 2 * (K * K - 1) / a0, (1 - K / Q2 + K * K) / a0]
    b = [1, -2, 1]
    h2 = _biquad_response(b, a, freqs, sr)
    return np.abs(h1 * h2)


def k_weight(audio: np.ndarray, sr: int) -> np.ndarray:
    n = audio.shape[0]
    spec = np.fft.rfft(audio, axis=0)
    w = _k_weighting(np.fft.rfftfreq(n, 1 / sr), sr)[:, None]
    return np.fft.irfft(spec * w, n=n, axis=0).astype(np.float32)


def loudness(audio: np.ndarray, sr: int) -> Dict[str, Any]:
    """Integrated loudness (LUFS, BS.1770-4 gating), short-term max, loudness range, true peak."""
    a = to_float(audio)
    kw = k_weight(a, sr)
    ch_w = np.ones(a.shape[1])
    if a.shape[1] >= 5:
        ch_w[3:5] = 1.41

    def block_loudness(block_s: float, overlap: float) -> np.ndarray:
        bl, hop = int(block_s * sr), int(block_s * sr * (1 - overlap))
        if a.shape[0] < bl:
            ms = (kw ** 2).mean(axis=0)
            return np.array([-0.691 + 10 * np.log10(max((ms * ch_w).sum(), 1e-12))])
        sq = np.concatenate([np.zeros((1, kw.shape[1])), np.cumsum(kw.astype(np.float64) ** 2, axis=0)])
        starts = np.arange(0, a.shape[0] - bl + 1, hop)
        ms = (sq[starts + bl] - sq[starts]) / bl
        return -0.691 + 10 * np.log10(np.maximum((ms * ch_w).sum(axis=1), 1e-12))

    m = block_loudness(0.4, 0.75)
    g = m[m > -70]
    if g.size:
        rel = -0.691 + 10 * np.log10(np.mean(10 ** ((g + 0.691) / 10))) - 10
        g2 = g[g > rel]
        integ = -0.691 + 10 * np.log10(np.mean(10 ** ((g2 + 0.691) / 10))) if g2.size else -70.0
    else:
        integ = -70.0
    st = block_loudness(3.0, 0.9)
    stg = st[st > -70]
    lra = None
    if stg.size > 2:
        rel = -0.691 + 10 * np.log10(np.mean(10 ** ((stg + 0.691) / 10))) - 20
        s2 = np.sort(stg[stg > rel])
        if s2.size > 2:
            lra = float(np.percentile(s2, 95) - np.percentile(s2, 10))
    sample_peak = float(np.max(np.abs(a))) if a.size else 0.0
    return {"integrated_lufs": round(float(integ), 2), "short_term_max_lufs": round(float(st.max()), 2),
            "momentary_max_lufs": round(float(m.max()), 2),
            "loudness_range_lu": None if lra is None else round(lra, 2),
            "sample_peak_dbfs": round(db(sample_peak), 2), "true_peak_dbtp": round(db(true_peak(a)), 2)}


def true_peak(a: np.ndarray, factor: int = 4, taps_per_phase: int = 12, max_samples: int = 48000 * 300) -> float:
    """BS.1770-style true peak: 4x polyphase windowed-sinc interpolation."""
    a = to_float(a)[:max_samples]
    if a.size == 0:
        return 0.0
    peak = float(np.max(np.abs(a)))
    for ph in range(1, factor):
        t = (np.arange(-taps_per_phase // 2 + 1, taps_per_phase // 2 + 1) - ph / factor)
        h = np.sinc(t) * np.hanning(len(t) + 2)[1:-1]
        h /= h.sum()
        for c in range(a.shape[1]):
            y = np.convolve(a[:, c], h, mode="same")
            peak = max(peak, float(np.max(np.abs(y))))
    return peak


# ----------------------------------------------------------------------------- dynamics / stereo

def dynamics(audio: np.ndarray, sr: int) -> Dict[str, Any]:
    a = to_float(audio)
    mono = a.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) if mono.size else 0.0
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    clip = int(np.sum(np.abs(a) >= 0.999))
    win = int(0.05 * sr) or 1
    frames = mono[: len(mono) // win * win].reshape(-1, win)
    frms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) if frames.size else np.zeros(1)
    silent = float(np.mean(frms < 10 ** (-60 / 20))) if frms.size else 1.0
    loud = frms[frms > 10 ** (-60 / 20)]
    return {"rms_dbfs": round(db(rms), 2), "peak_dbfs": round(db(peak), 2),
            "crest_factor_db": round(db(peak) - db(rms), 2) if rms > 0 else None,
            "dynamic_spread_db": round(db(np.percentile(loud, 95)) - db(np.percentile(loud, 10)), 2) if loud.size > 10 else None,
            "clipped_samples": clip, "dc_offset": [round(float(x), 5) for x in a.mean(axis=0)],
            "silence_ratio": round(silent, 3), "duration_s": round(a.shape[0] / sr, 3)}


def stereo(audio: np.ndarray) -> Dict[str, Any]:
    a = to_float(audio)
    if a.shape[1] < 2:
        return {"channels": 1}
    l, r = a[:, 0].astype(np.float64), a[:, 1].astype(np.float64)
    denom = math.sqrt((l * l).sum() * (r * r).sum())
    corr = float((l * r).sum() / denom) if denom > 0 else 1.0
    mid, side = (l + r) / 2, (l - r) / 2
    m_e, s_e = float((mid * mid).mean()), float((side * side).mean())
    l_e, r_e = float((l * l).mean()), float((r * r).mean())
    return {"channels": a.shape[1], "correlation": round(corr, 3),
            "side_to_mid_db": round(10 * math.log10(max(s_e, 1e-20) / max(m_e, 1e-20)), 2),
            "balance_db_left_minus_right": round(10 * math.log10(max(l_e, 1e-20) / max(r_e, 1e-20)), 2),
            "mono_compatibility": "poor" if corr < 0 else "fair" if corr < 0.3 else "good"}


# ----------------------------------------------------------------------------- spectrum

def spectrum(audio: np.ndarray, sr: int, n_fft: int = 8192) -> Dict[str, Any]:
    mono = to_float(audio).mean(axis=1)
    S = stft_mag(mono, n_fft, n_fft // 2)
    power = (S.astype(np.float64) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    total = power[(freqs >= 20) & (freqs <= 20000)].sum() + 1e-20
    bands = {}
    for name, lo, hi in BANDS:
        e = power[(freqs >= lo) & (freqs < min(hi, sr / 2))].sum()
        bands[name] = round(10 * math.log10(max(e, 1e-20) / total), 2)
    valid = (freqs >= 20) & (freqs <= min(20000, sr / 2))
    centroid = float((freqs[valid] * power[valid]).sum() / (power[valid].sum() + 1e-20))
    # spectral tilt: slope of dB vs log2(f) over 100 Hz - 10 kHz (pink noise = -3 dB/oct)
    sel = (freqs >= 100) & (freqs <= 10000)
    lf = np.log2(freqs[sel])
    ldb = 10 * np.log10(power[sel] + 1e-20)
    slope = float(np.polyfit(lf, ldb, 1)[0]) if sel.sum() > 4 else 0.0
    # resonances: narrow peaks standing out of a 1/3-octave smoothed curve
    res = []
    spec_db = 10 * np.log10(power + 1e-20)
    smooth = np.copy(spec_db)
    for i, f in enumerate(freqs):
        if f < 40 or f > 16000:
            continue
        lo, hi = np.searchsorted(freqs, f / 2 ** (1 / 6)), np.searchsorted(freqs, f * 2 ** (1 / 6))
        smooth[i] = spec_db[lo:hi + 1].mean()
    excess = spec_db - smooth
    cand = np.where((freqs > 40) & (freqs < 16000) & (excess > 6))[0]
    for i in cand:
        if excess[i] == excess[max(0, i - 3):i + 4].max():
            res.append((float(excess[i]), float(freqs[i])))
    res.sort(reverse=True)
    rolloff_idx = np.searchsorted(np.cumsum(power[valid]) / (power[valid].sum() + 1e-20), 0.95)
    return {"band_energy_db": bands, "spectral_centroid_hz": round(centroid, 1),
            "tilt_db_per_octave": round(slope, 2), "rolloff_95_hz": round(float(freqs[valid][min(rolloff_idx, valid.sum() - 1)]), 1),
            "resonances": [{"freq_hz": round(f, 1), "excess_db": round(e, 1), "note": hz_to_note(f)} for e, f in res[:6]]}


# ----------------------------------------------------------------------------- music

def hz_to_midi(f: float) -> float:
    return 69 + 12 * math.log2(f / 440.0)


def hz_to_note(f: float) -> str:
    m = int(round(hz_to_midi(f)))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def chromagram(mono: np.ndarray, sr: int, n_fft: int = 8192, hop: int = 2048) -> np.ndarray:
    S = stft_mag(mono, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs >= 55) & (freqs <= 4200)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0)).astype(int) + 9) % 12
    M = np.zeros((sel.sum(), 12), dtype=np.float32)
    M[np.arange(sel.sum()), pcs] = 1.0
    E = (S[:, sel].astype(np.float64) ** 2) @ M
    C = np.sqrt(E)
    C /= C.max(axis=1, keepdims=True) + 1e-12
    C[np.sqrt(E.sum(axis=1)) < 1e-4] = 0
    return C


_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def detect_key(chroma: np.ndarray) -> Dict[str, Any]:
    prof = chroma.mean(axis=0)
    scores = []
    for i in range(12):
        for mode, P in (("major", _MAJ), ("minor", _MIN)):
            scores.append((float(np.corrcoef(prof, np.roll(P, i))[0, 1]), f"{NOTE_NAMES[i]} {mode}"))
    scores.sort(reverse=True)
    return {"key": scores[0][1], "confidence": round(scores[0][0], 3),
            "alternatives": [{"key": k, "score": round(s, 3)} for s, k in scores[1:4]],
            "pitch_class_profile": {NOTE_NAMES[i]: round(float(prof[i]), 3) for i in range(12)}}


def _chord_templates() -> List[Tuple[str, np.ndarray]]:
    out = []
    for name, ivs in (("", [0, 4, 7]), ("m", [0, 3, 7]), ("7", [0, 4, 7, 10]), ("maj7", [0, 4, 7, 11]),
                      ("m7", [0, 3, 7, 10]), ("dim", [0, 3, 6]), ("sus4", [0, 5, 7])):
        for r in range(12):
            t = np.zeros(12)
            t[[(r + i) % 12 for i in ivs]] = 1
            out.append((NOTE_NAMES[r] + name, t / np.linalg.norm(t)))
    return out


def detect_chords(chroma: np.ndarray, frame_s: float, segment_s: float = 1.0, offset_s: float = 0.0) -> List[Dict[str, Any]]:
    tmpl = _chord_templates()
    step = max(1, int(round(segment_s / frame_s)))
    out: List[Dict[str, Any]] = []
    for s in range(0, chroma.shape[0], step):
        v = chroma[s:s + step].mean(axis=0)
        if v.max() < 0.05:
            name, score = "N.C.", 0.0
        else:
            v = v / (np.linalg.norm(v) + 1e-9)
            score, name = max((float(v @ t), n) for n, t in tmpl)
        t0 = offset_s + s * frame_s
        if out and out[-1]["chord"] == name:
            out[-1]["end_s"] = round(t0 + step * frame_s, 2)
        else:
            out.append({"chord": name, "start_s": round(t0, 2), "end_s": round(t0 + step * frame_s, 2), "fit": round(score, 2)})
    return out


def detect_tempo(mono: np.ndarray, sr: int) -> Dict[str, Any]:
    hop = 512
    S = stft_mag(mono, 2048, hop)
    flux = np.maximum(0, np.diff(np.log1p(S * 100), axis=0)).sum(axis=1)
    if flux.size < 16:
        return {"bpm": None}
    flux = flux - np.convolve(flux, np.ones(16) / 16, mode="same")
    flux = np.maximum(flux, 0)
    ac = np.correlate(flux, flux, mode="full")[flux.size - 1:]
    fps = sr / hop
    lags = np.arange(len(ac))
    bpm = 60 * fps / np.maximum(lags, 1)
    sel = (bpm >= 60) & (bpm <= 200)
    if not sel.any():
        return {"bpm": None}
    weight = np.exp(-0.5 * (np.log2(bpm[sel] / 120.0) / 0.9) ** 2)
    scores = ac[sel] * weight
    best = lags[sel][int(np.argmax(scores))]
    # parabolic refinement
    if 1 <= best < len(ac) - 1:
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
        best = best + max(-0.5, min(0.5, d))
    tempo = 60 * fps / best
    conf = float(ac[sel].max() / (ac[0] + 1e-12))
    return {"bpm": round(float(tempo), 2), "confidence": round(conf, 3),
            "half_double": [round(float(tempo / 2), 2), round(float(tempo * 2), 2)]}


def transcribe_melody(mono: np.ndarray, sr: int, bpm: float = 120.0, fmin: float = 60.0, fmax: float = 1500.0,
                      threshold: float = 0.15, min_note_s: float = 0.06, quantize: Optional[float] = 0.25) -> List[Dict[str, Any]]:
    """Monophonic pitch tracking (YIN) -> notes in beats (for record_notes_to_track)."""
    frame, hop = 2048, 256
    x = mono.astype(np.float64)
    if len(x) < frame:
        return []
    n = 1 + (len(x) - frame) // hop
    tau_max = min(frame // 2, int(sr / fmin))
    tau_min = max(2, int(sr / fmax))
    pitches = np.zeros(n)
    rms = np.zeros(n)
    for i in range(n):
        f = x[i * hop:i * hop + frame]
        rms[i] = math.sqrt((f * f).mean())
        w = f[:frame - tau_max]
        # difference function via FFT autocorrelation
        F = np.fft.rfft(f, 2 * frame)
        acf = np.fft.irfft(F * np.conj(np.fft.rfft(w, 2 * frame)))[:tau_max + 1]
        e0 = (w * w).sum()
        cs = np.concatenate([[0], np.cumsum(f * f)])
        e_tau = cs[np.arange(tau_max + 1) + len(w)] - cs[np.arange(tau_max + 1)]
        d = e0 + e_tau - 2 * acf
        d[0] = 0
        cmnd = d[1:] * np.arange(1, tau_max + 1) / (np.cumsum(d[1:]) + 1e-12)
        cmnd = np.concatenate([[1], cmnd])
        cand = np.where(cmnd[tau_min:] < threshold)[0]
        if cand.size and rms[i] > 0.01:
            t = cand[0] + tau_min
            while t + 1 <= tau_max and cmnd[t + 1] < cmnd[t]:
                t += 1
            if 1 <= t < tau_max:
                a, b_, c = cmnd[t - 1], cmnd[t], cmnd[t + 1]
                t = t + 0.5 * (a - c) / (a - 2 * b_ + c + 1e-12)
            pitches[i] = sr / t
    midi = np.where(pitches > 0, np.round(69 + 12 * np.log2(np.maximum(pitches, 1e-9) / 440.0)), 0).astype(int)
    # median smoothing to kill octave glitches
    sm = midi.copy()
    for i in range(2, n - 2):
        win = midi[i - 2:i + 3]
        nz = win[win > 0]
        if midi[i] > 0 and nz.size >= 3:
            sm[i] = int(np.median(nz))
    notes = []
    spb = 60.0 / bpm
    i = 0
    frame_s = hop / sr
    peak_rms = rms.max() + 1e-9
    while i < n:
        if sm[i] <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and sm[j + 1] == sm[i]:
            j += 1
        dur = (j - i + 1) * frame_s
        if dur >= min_note_s:
            start_b = i * frame_s / spb
            dur_b = dur / spb
            if quantize:
                start_b = round(start_b / quantize) * quantize
                dur_b = max(quantize, round(dur_b / quantize) * quantize)
            vel = int(max(30, min(127, 40 + 87 * rms[i:j + 1].mean() / peak_rms)))
            notes.append({"pitch": int(sm[i]), "start": round(start_b, 4), "duration": round(dur_b, 4),
                          "velocity": vel, "name": f"{NOTE_NAMES[int(sm[i]) % 12]}{int(sm[i]) // 12 - 1}"})
        i = j + 1
    return notes


# ----------------------------------------------------------------------------- report

def mix_suggestions(rep: Dict[str, Any], target_lufs: float = -14.0) -> List[str]:
    tips: List[str] = []
    L = rep.get("loudness", {})
    lufs, tp = L.get("integrated_lufs"), L.get("true_peak_dbtp")
    if lufs is not None and lufs > -70:
        if lufs < target_lufs - 3:
            tips.append(f"Loudness {lufs} LUFS is {round(target_lufs - lufs, 1)} LU under the {target_lufs} LUFS target: "
                        f"add limiting/gain on the master.")
        elif lufs > target_lufs + 2:
            tips.append(f"Loudness {lufs} LUFS is above the {target_lufs} LUFS target; streaming platforms will turn it down.")
    if tp is not None and tp > -1.0:
        tips.append(f"True peak {tp} dBTP exceeds -1 dBTP: lower the limiter ceiling to avoid inter-sample clipping.")
    d = rep.get("dynamics", {})
    if d.get("clipped_samples", 0) > 0:
        tips.append(f"{d['clipped_samples']} clipped samples: reduce gain before the output bus.")
    if d.get("crest_factor_db") is not None and d["crest_factor_db"] < 6:
        tips.append(f"Crest factor {d['crest_factor_db']} dB: very compressed/squashed; transients may be lost.")
    if any(abs(x) > 0.01 for x in d.get("dc_offset", [])):
        tips.append("DC offset detected: use a high-pass/DC filter (pre-filter low cut at 20-30 Hz).")
    s = rep.get("stereo", {})
    if s.get("correlation") is not None:
        if s["correlation"] < 0:
            tips.append("Negative stereo correlation: phase problems, the mix will collapse in mono.")
        if abs(s.get("balance_db_left_minus_right", 0)) > 1.5:
            tips.append(f"Left/right imbalance of {s['balance_db_left_minus_right']} dB.")
        if s.get("side_to_mid_db", -99) > -3:
            tips.append("Very wide image (side close to mid): check mono compatibility.")
    sp = rep.get("spectrum", {})
    b = sp.get("band_energy_db", {})
    if b:
        if b.get("sub", -99) > -8:
            tips.append(f"Strong sub energy ({b['sub']} dB of total): high-pass non-bass tracks, check kick/bass.")
        if b.get("bass", -99) - b.get("mid", -99) > 9:
            tips.append("Bass-heavy balance vs mids: consider cutting 60-250 Hz or boosting presence.")
        if b.get("low_mid", -99) > -6:
            tips.append("Low-mids (250-500 Hz) are dense: possible muddiness, try cutting around 300 Hz.")
        if b.get("high_mid", -99) > -8:
            tips.append("Strong 2-4 kHz: may sound harsh; check vocals/guitars/synths.")
        if b.get("air", -99) < -30:
            tips.append("Little air above 6 kHz: the mix may sound dull; a gentle high shelf can help.")
    tilt = sp.get("tilt_db_per_octave")
    if tilt is not None:
        if tilt > -1.5:
            tips.append(f"Spectral tilt {tilt} dB/oct is bright vs. typical mixes (-3 to -5 dB/oct).")
        elif tilt < -6:
            tips.append(f"Spectral tilt {tilt} dB/oct is dark vs. typical mixes (-3 to -5 dB/oct).")
    for r in sp.get("resonances", [])[:3]:
        if r["excess_db"] > 10:
            tips.append(f"Resonance at {r['freq_hz']} Hz ({r['note']}, +{r['excess_db']} dB): consider a narrow EQ cut.")
    if not tips:
        tips.append("No obvious technical problems detected.")
    return tips


def analyze(audio: np.ndarray, sr: int, what: Optional[List[str]] = None, bpm_hint: Optional[float] = None,
            target_lufs: float = -14.0, chord_segment_s: Optional[float] = None) -> Dict[str, Any]:
    """Full analysis. what ⊆ {loudness, dynamics, stereo, spectrum, tempo, key, chords, melody}."""
    a = to_float(audio)
    what = what or ["loudness", "dynamics", "stereo", "spectrum", "tempo", "key", "chords"]
    rep: Dict[str, Any] = {"sample_rate": sr, "duration_s": round(a.shape[0] / sr, 3)}
    mono = a.mean(axis=1)
    if "loudness" in what:
        rep["loudness"] = loudness(a, sr)
    if "dynamics" in what:
        rep["dynamics"] = dynamics(a, sr)
    if "stereo" in what:
        rep["stereo"] = stereo(a)
    if "spectrum" in what:
        rep["spectrum"] = spectrum(a, sr)
    if "tempo" in what:
        rep["tempo"] = detect_tempo(mono, sr)
    if "key" in what or "chords" in what:
        hop = 2048
        C = chromagram(mono, sr, 8192, hop)
        if "key" in what:
            rep["key"] = detect_key(C)
        if "chords" in what:
            bpm = bpm_hint or (rep.get("tempo") or {}).get("bpm") or 120.0
            seg = chord_segment_s or (60.0 / bpm) * 2  # half a bar in 4/4
            rep["chords"] = detect_chords(C, hop / sr, seg)
    if "melody" in what:
        bpm = bpm_hint or (rep.get("tempo") or {}).get("bpm") or 120.0
        rep["melody"] = transcribe_melody(mono, sr, bpm)
    rep["suggestions"] = mix_suggestions(rep, target_lufs)
    return rep


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Difference 'mix - reference' for loudness, dynamics, stereo and band balance."""
    out: Dict[str, Any] = {}
    for sec, keys in (("loudness", ["integrated_lufs", "true_peak_dbtp", "loudness_range_lu"]),
                      ("dynamics", ["crest_factor_db", "rms_dbfs"]), ("stereo", ["correlation", "side_to_mid_db"]),
                      ("spectrum", ["spectral_centroid_hz", "tilt_db_per_octave"])):
        for k in keys:
            x, y = (a.get(sec) or {}).get(k), (b.get(sec) or {}).get(k)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                out[f"{sec}.{k}"] = round(x - y, 2)
    ba, bb = (a.get("spectrum") or {}).get("band_energy_db", {}), (b.get("spectrum") or {}).get("band_energy_db", {})
    out["band_difference_db"] = {k: round(ba[k] - bb[k], 2) for k in ba if k in bb}
    tips = []
    for k, v in out["band_difference_db"].items():
        if abs(v) >= 3:
            tips.append(f"{k}: {'+' if v > 0 else ''}{v} dB vs reference → {'cut' if v > 0 else 'boost'} this range.")
    lu = out.get("loudness.integrated_lufs")
    if lu is not None and abs(lu) >= 1:
        tips.append(f"Loudness differs by {lu} LU from the reference.")
    out["suggestions"] = tips or ["Balance is close to the reference."]
    return out
