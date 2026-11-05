"""End-to-end: Python server <-> real MIDI Remote script running in a Node mock of Cubase."""
import json
import time

from neocp import server as S


def call(fn, *a, **kw):
    return json.loads(fn(*a, **kw))


def test_handshake(mock_bridge):
    b = mock_bridge
    assert b.info["features"]["app"] == "MockCubase"
    assert len(b.keys) > 2000
    assert ("Edit", "Undo") in b.command_ids
    assert b.info["features"]["direct_access"]


def test_session_and_tracks(mock_bridge):
    info = call(S.get_session_info)
    assert [t["name"] for t in info["tracks"]] == ["Drums", "Bass", "Keys"]
    assert info["tempo_bpm"] == 120


def test_volume_and_toggles(mock_bridge):
    r = call(S.set_track_volume, "Bass", normalized=0.42)
    assert abs(r["value"] - 0.42) < 0.001
    # mock display = value*100 -> bisection to a display target
    r = call(S.set_track_volume, 1, db=73.0)
    assert abs(float(r["display"].split()[0]) - 73.0) < 0.2
    r = call(S.set_track_state, "Keys", mute=True, solo=True)
    assert r["mute"] is True and r["solo"] is True
    r = call(S.set_track_pan, 0, -1.0)
    assert r["value"] == 0.0


def test_commands_and_tempo(mock_bridge):
    call(S.execute_command, "Edit", "Undo")
    call(S.project_action, "quantize")
    call(S.create_track, "midi", 1)
    r = call(S.set_tempo, 133.0)
    assert r["tempo_bpm"] == 133.0
    time.sleep(0.2)
    info = mock_bridge.link.query()
    assert "Edit/Undo" in info["executed"]
    assert "Quantize Category/Quantize" in info["executed"]
    assert "AddTrack/MIDI" in info["executed"]


def test_sysex_write_mode(mock_bridge):
    mock_bridge.write_mode = "sysex"
    r = call(S.set_track_volume, 0, normalized=0.25)
    assert abs(r["value"] - 0.25) < 1e-6
    call(S.execute_command, "File", "Save")
    time.sleep(0.1)
    assert "File/Save" in mock_bridge.link.query()["executed"]


def test_select_and_strip(mock_bridge):
    r = call(S.select_track, "Bass")
    assert r["name"] == "Bass"
    r = call(S.set_eq_band, 2, gain_db=30.0)
    assert r["on"] is True
    info = call(S.get_selected_track_info)
    assert info["name"] == "Bass" and len(info["eq"]) == 4


def test_direct_access(mock_bridge):
    tree = call(S.explore_host_objects)
    assert tree["title"] == "Kick" and len(tree["kids"]) == 3
    found = call(S.find_host_objects, "compressor")
    assert found["found"][0]["id"] == 2
    params = call(S.get_object_parameters, 2, filter="thresh")
    assert params["params"][0]["title"] == "Threshold"
    r = call(S.set_plugin_parameter, "Compressor", "ratio", value=0.8)
    assert r["result"]["value"] == 0.8
    pl = call(S.list_available_plugins, 2)
    assert pl["items"][0]["pluginname"] == "REVerence"
    r = call(S.load_plugin, 2, plugin_name="reverence")
    assert r["ok"] is True


def test_locators_and_actions(mock_bridge):
    r = call(S.set_position, bar=5)
    assert r["set"] == "5.1.1.0"
    r = call(S.set_loop_range, 9, 17)
    assert r["left_locator"] == "9.1.1.0" and r["right_locator"] == "17.1.1.0"
    r = call(S.bank, "next")  # only 3 tracks: Cubase cannot move the bank -> verified, stays at 0
    assert r["bank_offset"] == 0 and r["at_last_page"] is True
    r = call(S.set_track_volume, 70, normalized=0.5)
    assert "error" in r and "no track" in r["error"]


def test_record_notes(mock_bridge, monkeypatch):
    notes = [{"pitch": "C4", "start": 0, "duration": 0.5, "velocity": 100},
             {"pitch": 64, "start": 0.5, "duration": 0.5}]
    r = call(S.record_notes_to_track, "Keys", notes, start_bar=1, speed=2.0)
    assert r["recorded_notes"] == 2 and r["record_tempo"] == 240.0
    sent = [n for n in mock_bridge.link.notes if n[0] == "on"]
    assert [n[2] for n in sent] == [60, 64]


def test_search_commands():
    r = json.loads(S.search_commands("quantize"))
    assert any(c["command"] == "Quantize" for c in r["results"])
    r = json.loads(S.search_commands("add track midi"))
    assert r["results"][0]["category"] == "AddTrack"


def test_hardcoded_commands_exist():
    known = {(c["category"], c["command"]) for c in S.commands()}
    used = list(S.QUICK.values()) + list(S.TRACK_TYPES.values())
    import re, inspect
    src = inspect.getsource(S)
    used += re.findall(r'\("([A-Z][\w &]+)", "([^"]+)"\)', src)
    missing = sorted({u for u in used if u not in known and not u[0].startswith("NEOCP_")})
    assert not missing, missing


def test_selftest(mock_bridge):
    r = call(S.run_selftest)
    st = {x["test"]: x["status"] for x in r["results"]}
    for k in ("handshake", "tracks mirrored", "tempo write", "value write (NRPN)", "value write (SysEx)",
              "commands bound", "DirectAccess", "plug-in manager", "transport toggle"):
        assert st[k] == "ok", (k, r)
    assert r["write_mode"] == "nrpn"


def test_bind_new_command(mock_bridge, tmp_path, monkeypatch):
    from neocp import install as I
    monkeypatch.setattr(I, "EXTRA_FILE", tmp_path / "extra.json")
    monkeypatch.setattr(I, "documents_dir", lambda: tmp_path)
    r = call(S.bind_new_command, "Audio", "Stem Separation")
    assert r["bound"] == "Audio/Stem Separation" and r["script_reinstalled"]
    js = open(r["script_reinstalled"][0], encoding="utf-8").read()
    assert 'var EXTRA_COMMANDS = "Audio\\tStem Separation"' in js
    assert any(c.get("user_added") for c in S.commands())
    S._COMMANDS = None


def test_produce_song_existing_tracks(mock_bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))
    r = call(S.produce_song, "lofi", "D minor", bars=4,
             tracks={"drums": "Drums", "bass": "Bass", "chords": "Keys"}, parts=["drums", "bass", "chords"],
             create_tracks=False, speed=4.0, listen_after=False)
    assert r["tempo"] == 82.0 and r["midi_file"].endswith(".mid")
    rec = [s for s in r["steps"] if "recorded" in s]
    assert [s["recorded"] for s in rec] == ["drums", "bass", "chords"] and all(s["notes"] > 0 for s in rec)


def test_parsing_and_positions():
    from neocp.bridge import _parse_number as pn

    class B:
        transport = {"cursor_format": "Bars+Beats", "tempo": 120}
    assert pn("-oo dB") == float("-inf") and pn("-6.02 dB") == -6.02
    assert pn("1.20 kHz") == 1200 and pn("1,200 Hz") == 1200 and pn("0,5") == 0.5
    assert S._position_string(B, 1, beat=4.9999, beats_per_bar=4) == "2.1.1.0"
    assert S._position_string(B, 3.5, beats_per_bar=4) == "3.3.1.0"
    assert S._position_string(B, 2, beats_per_bar=3) == "2.1.1.0"


def test_review_project(mock_bridge):
    call(S.set_track_state, "Keys", solo=True, record_arm=True)
    r = call(S.review_project)
    levels = [f["level"] for f in r["findings"]]
    assert r["tracks"] == 3 and levels[0] == "high"
    assert any("Solo" in f["finding"] for f in r["findings"]) and any("armed" in f["finding"] for f in r["findings"])
