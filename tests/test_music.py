import mido

from neocp import music as M


def test_note_names():
    assert M.note_to_midi("C4") == 60
    assert M.note_to_midi("F#3") == 54
    assert M.note_to_midi("Bb2") == 46
    assert M.midi_to_note(61) == "C#4"


def test_chords_and_roman():
    assert M.parse_chord("Am7", 4) == [69, 72, 76, 79]
    assert M.parse_chord("C/E", 4)[0] % 12 == 4
    assert [n % 12 for n in M.roman_to_chord("vi", "C major")] == [9, 0, 4]
    assert [n % 12 for n in M.roman_to_chord("V7", "A minor")] == [4, 8, 11, 2]  # E G# B D (dominant V7 in minor)


def test_generators():
    prog = M.chord_progression(["I", "V", "vi", "IV"], "G major", rhythm="pop")
    assert prog and max(n["start"] for n in prog) < 16
    for style in M.DRUM_STYLES:
        d = M.drum_pattern(style, 2, fill=True, seed=1)
        assert d and all(0 <= n["start"] < 8.5 for n in d)
    for st in ("root", "root_fifth", "octave", "eighths", "syncopated", "walking", "arp"):
        assert M.bassline(["Am", "F", "C", "G"], style=st, seed=2)
    mel = M.melody("D dorian", 4, chords=["Dm", "G"], seed=3)
    pcs = {n["pitch"] % 12 for n in mel}
    root, scale = M.parse_key("D dorian")
    assert pcs <= {(root + i) % 12 for i in M.SCALES[scale]}
    assert M.arpeggio(["C", "Am"], pattern="updown")


def test_midi_file(tmp_path):
    p = tmp_path / "x.mid"
    M.write_midi_file(str(p), {"Drums": M.drum_pattern("house", 1), "Keys": M.chord_progression(["C", "G"])}, 128)
    mf = mido.MidiFile(str(p))
    assert len(mf.tracks) == 3
    assert any(m.type == "note_on" and m.channel == 9 for m in mf.tracks[1])
