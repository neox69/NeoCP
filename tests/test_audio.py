import json
import time

import numpy as np
import pytest

from neocp import audio as A

SR = 48000


def tone(freqs, dur, amp=0.3):
    t = np.arange(int(SR * dur)) / SR
    return amp * sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)


def test_loudness_reference_values():
    t = np.arange(SR * 5) / SR
    s = np.sin(2 * np.pi * 1000 * t)
    assert abs(A.loudness(np.stack([s, s], 1), SR)["integrated_lufs"]) < 0.1
    assert abs(A.loudness(s, SR)["integrated_lufs"] + 3.01) < 0.1
    assert abs(A.loudness(0.5 * s, SR)["true_peak_dbtp"] + 6.02) < 0.1


def test_key_chords_tempo_melody():
    prog = np.concatenate([tone([261.6, 329.6, 392.0], 2), tone([220, 261.6, 329.6], 2),
                           tone([174.6, 220, 261.6], 2), tone([196, 246.9, 293.7], 2)])
    C = A.chromagram(prog, SR)
    assert A.detect_key(C)["key"] == "C major"
    assert [c["chord"] for c in A.detect_chords(C, 2048 / SR, 1.0)] == ["C", "Am", "F", "G"]
    clicks = np.zeros(SR * 10)
    per = int(SR * 60 / 128)
    clicks[::per] = 1.0
    assert abs(A.detect_tempo(np.convolve(clicks, np.hanning(200), "same"), SR)["bpm"] - 128) < 1
    mel = np.concatenate([tone([f], 0.5) for f in (261.63, 293.66, 329.63, 392.0)])
    notes = A.transcribe_melody(mel, SR, 120)
    assert [n["pitch"] for n in notes] == [60, 62, 64, 67]
    assert [n["start"] for n in notes] == [0.0, 1.0, 2.0, 3.0]


def test_suggestions_and_compare():
    loud = np.clip(tone([50, 100], 4, 1.5), -1, 1)
    rep = A.analyze(np.stack([loud, loud], 1), SR, ["loudness", "dynamics", "stereo", "spectrum"])
    text = " ".join(rep["suggestions"])
    assert "True peak" in text and "clipped" in text
    ref = A.analyze(np.stack([tone([1000, 3000], 4)] * 2, 1), SR, ["loudness", "dynamics", "stereo", "spectrum"])
    cmp = A.compare(rep, ref)
    assert cmp["band_difference_db"]["bass"] > 3


def test_file_tools(tmp_path):
    from neocp import server as S
    p = tmp_path / "x.wav"
    A.save_wav(str(p), np.stack([tone([440], 3)] * 2, 1), SR)
    r = json.loads(S.analyze_audio_file(str(p)))
    assert r["key"]["key"].startswith("A")
    r = json.loads(S.transcribe_audio(path=str(p), bpm=120))
    assert r["notes"][0]["pitch"] == 69
    r = json.loads(S.compare_to_reference(str(p), mix_path=str(p)))
    assert r["mix_minus_reference"]["band_difference_db"]["mid"] == 0


def test_listen_to_cubase(mock_bridge, monkeypatch, tmp_path):
    from neocp import capture, server as S
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))

    class FakeRec:
        device_name = "BlackHole 2ch"

        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def stop(self):
            x = tone([261.6, 329.6, 392.0], 4)
            return np.stack([x, x], 1).astype(np.float32), SR

    monkeypatch.setattr(capture, "Recorder", FakeRec)
    r = json.loads(S.listen_to_cubase(start_bar=5, bars=1, solo_track="Bass"))
    assert r["key"]["key"] in ("C major", "A minor", "E minor")
    assert r["chords"][0]["chord"] == "C" and r["chords"][0]["bar"] == 5.0
    assert r["soloed_track"] == "Bass" and r["saved_wav"].endswith(".wav")
    assert r["loudness"]["integrated_lufs"] < 0
    m = json.loads(S.listen_to_meters(seconds=0.3))
    assert "levels" in m


def _song():
    rng = np.random.default_rng(0)
    bar = 2.0

    def t(fs, d, amp=0.3, noise=0.0):
        x = tone(fs, d, amp)
        return x + noise * rng.standard_normal(len(x))
    verse = np.concatenate([t([220, 261.6, 329.6], bar), t([174.6, 220, 261.6], bar)] * 4)
    chorus = np.concatenate([t([261.6, 329.6, 392], bar, 0.6, 0.05), t([196, 246.9, 293.7], bar, 0.6, 0.05)] * 4)
    return np.concatenate([t([110], bar * 4, 0.1), verse, chorus, verse, chorus, t([293.7, 349.2, 440], bar * 4, 0.4),
                           chorus, t([110], bar * 4, 0.1)])


def test_structure_and_spectrogram(tmp_path):
    from neocp import server as S
    song = _song()
    r = A.song_structure(song, SR, 120)
    assert [s["name"] for s in r["sections"]] == ["intro", "verse", "chorus", "verse", "chorus", "bridge", "chorus", "outro"]
    assert [s["start_bar"] for s in r["sections"]] == [1, 5, 13, 21, 29, 37, 41, 49]
    p = tmp_path / "song.wav"
    A.save_wav(str(p), song, SR)
    out = S.show_spectrogram(path=str(p))
    assert type(out[0]).__name__ == "Image" and out[0].data[:4] == b"\x89PNG"
    r = json.loads(S.analyze_song_structure(path=str(p), bpm=120))
    assert r["form"] == "ABCBCDCA"


class _FakeRec:
    device_name = "BlackHole 2ch"
    signal = None

    def __init__(self, *a, **k):
        pass

    def start(self):
        return self

    def stop(self):
        x = _FakeRec.signal
        return np.stack([x, x], 1).astype(np.float32), SR


def test_closed_loop_tools(mock_bridge, monkeypatch, tmp_path):
    from neocp import capture, server as S
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(capture, "Recorder", _FakeRec)
    rng = np.random.default_rng(1)
    _FakeRec.signal = 0.05 * rng.standard_normal(SR * 4) + tone([700], 4, 0.2)  # noise + 700 Hz ring
    r = json.loads(S.fix_resonances("Drums", bars=1, verify=False))
    assert r["eq_cuts"] and abs(r["eq_cuts"][0]["freq_hz"] - 700) < 10
    r = json.loads(S.gain_stage_tracks(target_lufs=-20, tracks=["Bass"], bars=1))
    assert r["tracks"][0]["measured_lufs"] is not None and "fader" in r["tracks"][0]
    r = json.loads(S.match_loudness(target_lufs=-14, bars=1, max_passes=2))
    assert r["bus"] == "Stereo Out" and len(r["passes"]) == 2
    r = json.loads(S.analyze_song_structure(bpm=120, mark_in_project=True))
    assert r["markers_inserted"] >= 1
    time.sleep(0.1)
    assert "Marker/Insert Marker" in mock_bridge.link.query()["executed"]
    r = json.loads(S.auto_mix_pass(bars=1, do_gain_staging=False, fix_resonances_on=["Keys"]))
    assert "before" in r and "after" in r and "master" in r
