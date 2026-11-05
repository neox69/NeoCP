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
    r = call(S.bank, "next")
    assert r["bank_offset"] == 32


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
