import json
import runpy
from pathlib import Path

import numpy as np

from neocp import drums as D, genres as G, music as M

SR = 44100
BPM = 110
SPB = 60 / BPM
RNG = np.random.default_rng(5)


def _hp(x, fc):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[f < fc] = 0
    return np.fft.irfft(X, len(x))


def _kick():
    t = np.arange(int(0.35 * SR)) / SR
    f = 50 + 90 * np.exp(-t * 30)
    return 0.9 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _snare():
    t = np.arange(int(0.25 * SR)) / SR
    return 0.35 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25) + 0.5 * _hp(RNG.standard_normal(len(t)), 1200) * np.exp(-t * 18)


def _hat(o=False):
    t = np.arange(int((0.4 if o else 0.06) * SR)) / SR
    return 0.25 * _hp(RNG.standard_normal(len(t)), 7000) * np.exp(-t * (6 if o else 70))


def _loop():
    pat = {"kick": "X.......X.X.....", "snare": "....X.......X...", "closed_hat": "x.x...x.x.x...o."}
    L = int(SR * SPB * 8) + SR
    a = np.zeros(L)
    truth = set()
    for bar in range(2):
        for name, p in pat.items():
            for i, c in enumerate(p):
                if c == ".":
                    continue
                nm = "open_hat" if c == "o" else name
                x = _kick() if nm == "kick" else _snare() if nm == "snare" else _hat(nm == "open_hat")
                s = int((bar * 4 + i * 0.25) * SPB * SR)
                a[s:s + len(x)] += x
                truth.add((D.GM[nm], bar * 4 + i * 0.25))
    return a + 0.002 * RNG.standard_normal(L), truth


def test_drum_transcription():
    a, truth = _loop()
    r = D.transcribe_drums(a, SR, BPM)
    got = {(n["pitch"], n["start"]) for n in r["notes"]}
    tp = len(got & truth)
    assert tp / len(got) >= 0.9 and tp / len(truth) >= 0.9, (sorted(truth - got), sorted(got - truth))
    assert r["grid"]["kick"].startswith("X.......X.X.....")


def test_drum_tool_records(mock_bridge, tmp_path, monkeypatch):
    from neocp import audio as A, server as S
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))
    a, _ = _loop()
    p = tmp_path / "loop.wav"
    A.save_wav(str(p), a, SR)
    r = json.loads(S.transcribe_drums(path=str(p), bpm=BPM, record_to_track="Drums", include_notes=False))
    assert r["per_instrument"]["kick"] == 6 and r["recorded"]["recorded_notes"] == r["count"]


def test_genre_check_and_strum():
    rep = {"loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -0.2}, "dynamics": {"crest_factor_db": 12},
           "spectrum": {"tilt_db_per_octave": -4.5, "band_energy_db": {"sub": -20, "air": -20}}}
    r = G.check(rep, "trap")
    st = {c["metric"]: c["status"] for c in r["checks"]}
    assert st["integrated_lufs"] == "low" and st["true_peak_dbtp"] == "high" and st["sub_share_db"] == "low"
    assert r["match_score"] < 50
    ch = M.chord_progression(["C", "F"], beats_per_chord=2)
    s = M.strum(ch, 0.05, "alternate")
    first = sorted(n["start"] for n in s if n["start"] < 1)
    assert first == [0.0, 0.05, 0.1]
    assert M.stretch(ch, 2.0)[-1]["start"] == 4.0
