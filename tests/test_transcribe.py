import json

import numpy as np

from neocp import transcribe as T

SR = 44100


def _note(m, dur, amp=0.2):
    t = np.arange(int(SR * dur)) / SR
    f = 440 * 2 ** ((m - 69) / 12)
    env = np.exp(-t * 1.5) * np.minimum(1, t / 0.005)
    return amp * env * sum((0.8 ** h) * np.sin(2 * np.pi * f * (h + 1) * t) for h in range(8))


TRUTH = [[48, 60, 64, 67], [45, 57, 60, 64], [41, 57, 60, 65], [43, 59, 62, 67]]


def _audio():
    return np.concatenate([sum(_note(m, 1.0) for m in c) for c in TRUTH])


def test_polyphonic_chords():
    r = T.transcribe(_audio(), SR, 120)
    got = {(n["pitch"], n["start"]) for n in r["notes"]}
    truth = {(m, i * 2.0) for i, c in enumerate(TRUTH) for m in c}
    tp = len(got & truth)
    assert tp / len(truth) >= 0.9 and tp / len(got) >= 0.9, (sorted(got - truth), sorted(truth - got))
    assert [c["chord"] for c in r["chords"]] == ["C", "Am", "F", "G"]
    assert [n["pitch"] for n in r["parts"]["bass"]] == [48, 45, 41, 43]


def test_tool_with_file_and_recording(mock_bridge, tmp_path, monkeypatch):
    from neocp import audio as A, server as S
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))
    p = tmp_path / "keys.wav"
    A.save_wav(str(p), _audio(), SR)
    r = json.loads(S.transcribe_polyphonic(path=str(p), bpm=120, record_to_tracks={"bass": "Bass"},
                                           include_notes=False))
    assert r["count"] >= 15 and r["midi_file"].endswith(".mid")
    assert r["recorded"]["bass"]["recorded_notes"] == 4
    assert [c["chord"] for c in r["chords"]][:4] == ["C", "Am", "F", "G"]
