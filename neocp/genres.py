"""Approximate mastering / mix reference profiles per genre.

Typical commercial-release ranges collected from common mastering practice. They are orientation
values, not rules: always trust your ears and a real reference track (compare_to_reference).
"""
from typing import Any, Dict, List

PROFILES: Dict[str, Dict[str, Any]] = {
    # lufs = integrated loudness range, tp = max true peak, crest = crest factor range (dB),
    # tilt = spectral tilt range (dB/oct), sub/air = band share of total energy range (dB)
    "edm":       {"lufs": (-9, -6), "tp": -0.5, "crest": (5, 9), "tilt": (-5.5, -3.5), "sub": (-14, -6), "air": (-28, -16)},
    "house":     {"lufs": (-10, -7), "tp": -0.5, "crest": (6, 10), "tilt": (-5.5, -3.5), "sub": (-14, -7), "air": (-28, -16)},
    "techno":    {"lufs": (-10, -7), "tp": -0.5, "crest": (6, 10), "tilt": (-5.5, -3.5), "sub": (-14, -6), "air": (-30, -16)},
    "trap":      {"lufs": (-9, -6), "tp": -0.5, "crest": (5, 9), "tilt": (-6.5, -4.0), "sub": (-10, -4), "air": (-30, -16)},
    "hiphop":    {"lufs": (-10, -7), "tp": -0.5, "crest": (6, 10), "tilt": (-6.0, -4.0), "sub": (-12, -5), "air": (-30, -17)},
    "pop":       {"lufs": (-10, -7), "tp": -1.0, "crest": (6, 10), "tilt": (-5.0, -3.0), "sub": (-18, -9), "air": (-26, -14)},
    "rock":      {"lufs": (-10, -7), "tp": -1.0, "crest": (6, 10), "tilt": (-4.5, -2.5), "sub": (-22, -12), "air": (-26, -14)},
    "metal":     {"lufs": (-9, -6), "tp": -1.0, "crest": (5, 8), "tilt": (-4.0, -2.0), "sub": (-24, -12), "air": (-24, -13)},
    "reggaeton": {"lufs": (-9, -6), "tp": -0.5, "crest": (5, 9), "tilt": (-6.0, -4.0), "sub": (-12, -5), "air": (-28, -16)},
    "lofi":      {"lufs": (-14, -10), "tp": -1.0, "crest": (8, 13), "tilt": (-7.0, -5.0), "sub": (-18, -9), "air": (-40, -24)},
    "jazz":      {"lufs": (-18, -13), "tp": -1.0, "crest": (11, 18), "tilt": (-6.0, -4.0), "sub": (-24, -12), "air": (-30, -18)},
    "acoustic":  {"lufs": (-16, -12), "tp": -1.0, "crest": (10, 16), "tilt": (-5.5, -3.5), "sub": (-26, -14), "air": (-28, -16)},
    "classical": {"lufs": (-23, -16), "tp": -1.0, "crest": (14, 22), "tilt": (-6.5, -4.0), "sub": (-30, -14), "air": (-32, -18)},
    "podcast":   {"lufs": (-17, -15), "tp": -1.0, "crest": (9, 16), "tilt": (-6.0, -3.0), "sub": (-36, -18), "air": (-34, -18)},
    "streaming": {"lufs": (-15, -13), "tp": -1.0, "crest": (7, 14), "tilt": (-5.5, -3.0), "sub": (-22, -8), "air": (-30, -14)},
}


def check(report: Dict[str, Any], genre: str) -> Dict[str, Any]:
    g = genre.lower().replace("-", "").replace(" ", "")
    if g not in PROFILES:
        raise ValueError(f"unknown genre '{genre}'. Known: {', '.join(PROFILES)}")
    P = PROFILES[g]
    L = report.get("loudness") or {}
    D = report.get("dynamics") or {}
    S = report.get("spectrum") or {}
    bands = S.get("band_energy_db") or {}
    rows: List[Dict[str, Any]] = []
    tips: List[str] = []

    def rng(name, val, lo, hi, low_tip, high_tip):
        if val is None:
            return
        st = "ok" if lo <= val <= hi else ("low" if val < lo else "high")
        rows.append({"metric": name, "value": val, "target": [lo, hi], "status": st})
        if st == "low":
            tips.append(low_tip)
        elif st == "high":
            tips.append(high_tip)

    rng("integrated_lufs", L.get("integrated_lufs"), *P["lufs"],
        f"Too quiet for {genre}: raise master loudness (limiter) towards {P['lufs'][1]} LUFS (match_loudness).",
        f"Louder than typical {genre}: back off the limiter, you are losing dynamics.")
    tp = L.get("true_peak_dbtp")
    if tp is not None:
        ok = tp <= P["tp"]
        rows.append({"metric": "true_peak_dbtp", "value": tp, "target": ["<=", P["tp"]], "status": "ok" if ok else "high"})
        if not ok:
            tips.append(f"True peak {tp} dBTP above {P['tp']}: lower the limiter ceiling.")
    rng("crest_factor_db", D.get("crest_factor_db"), *P["crest"],
        "Over-compressed for the style: less limiting / slower attack to keep transients.",
        "More dynamic than typical: more bus compression / limiting for density.")
    rng("tilt_db_per_octave", S.get("tilt_db_per_octave"), *P["tilt"],
        "Darker than typical: high shelf boost (~+1.5 dB @ 8-10 kHz) or less low end.",
        "Brighter than typical: tame 3-8 kHz or add low end.")
    rng("sub_share_db", bands.get("sub"), *P["sub"],
        "Weaker sub than typical: boost 40-60 Hz on kick/bass or add a sub layer.",
        "More sub than typical: high-pass non-bass tracks, cut 30-50 Hz on the bass.")
    rng("air_share_db", bands.get("air"), *P["air"],
        "Less air than typical: gentle high shelf on master / cymbals / vocals.",
        "More air than typical: de-ess / tame cymbals above 8 kHz.")
    score = round(100 * sum(r["status"] == "ok" for r in rows) / max(1, len(rows)))
    return {"genre": g, "match_score": score, "checks": rows, "suggestions": tips or [f"Mix sits within typical {genre} ranges."],
            "note": "Approximate reference ranges; confirm with compare_to_reference against a real track."}
