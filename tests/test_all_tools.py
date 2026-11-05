"""Call EVERY registered tool through the MCP server with realistic arguments (mock Cubase).

A tool may return a JSON {"error": ...} for a legitimate reason (e.g. mock has no Control Room),
but it must never raise through the MCP layer ("Error executing tool"), and errors must be
readable messages, not tracebacks.
"""
import asyncio
import json

import numpy as np
import pytest

from neocp import server as S

SR = 44100


def _wav(tmp_path, name="a.wav"):
    from neocp import audio as A
    t = np.arange(SR * 3) / SR
    x = 0.3 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 277.2 * t) + np.sin(2 * np.pi * 329.6 * t)) / 3
    x[:: SR // 4] += 0.8  # clicks for tempo/drums
    p = tmp_path / name
    A.save_wav(str(p), np.stack([x, x], 1), SR)
    return str(p)


NOTES = [{"pitch": "C4", "start": 0, "duration": 0.5, "velocity": 100}, {"pitch": 64, "start": 0.5, "duration": 0.5}]


def args_for(name, wav):
    table = {
        "get_track_info": {"track": "Bass"}, "select_track": {"track": 0},
        "set_track_volume": {"track": "Keys", "db": 50.0}, "set_track_pan": {"track": 1, "pan": 0.25},
        "set_track_state": {"track": "Drums", "mute": True, "solo": False},
        "set_send": {"track": 0, "send_slot": 0, "level_db": 40.0, "on": True},
        "transport": {"action": "play"}, "set_tempo": {"bpm": 98}, "set_position": {"bar": 3},
        "set_loop_range": {"start_bar": 1, "end_bar": 5}, "set_cycle": {"on": True},
        "set_metronome": {"on": False}, "bank": {"action": "reset"},
        "create_track": {"track_type": "audio"}, "track_operation": {"operation": "duplicate", "track": 0},
        "search_commands": {"query": "quantize"}, "execute_command": {"category": "Edit", "command": "Undo"},
        "project_action": {"action": "save"}, "set_quantize_grid": {"grid": "1/8"},
        "set_eq_band": {"band": 2, "track": 0, "gain_db": 30.0, "freq_hz": 40.0},
        "set_pre_filter": {"track": 0, "low_cut_hz": 30.0}, "set_strip_module": {"module": "comp", "active": True},
        "set_insert_state": {"slot": 0, "bypass": True}, "set_instrument_parameter": {"index": 0, "value": 0.5},
        "instrument_parameter_bank": {"direction": "next"}, "set_quick_control": {"index": 0, "value": 0.3},
        "explore_host_objects": {}, "find_host_objects": {"query": "comp"},
        "get_object_parameters": {"object_id": 2}, "set_object_parameter": {"object_id": 2, "tag": 10, "value": 0.4},
        "set_plugin_parameter": {"plugin": "Compressor", "parameter": "ratio", "value": 0.6},
        "list_available_plugins": {"slot_object_id": 2}, "load_plugin": {"slot_object_id": 2, "plugin_name": "rev"},
        "remove_plugin": {"slot_object_id": 2}, "control_room": {"dim": True},
        "list_controls": {"prefix": "sel."}, "set_control": {"key": "ch0.pan", "value": 0.5},
        "music_theory": {"key": "D dorian", "chord": "Gm7"},
        "generate_chord_progression": {"chords": ["vi", "IV", "I", "V"], "key": "C major"},
        "generate_drum_pattern": {"style": "house", "bars": 1},
        "generate_bassline": {"chords": ["Am", "F"], "style": "walking"},
        "generate_arpeggio": {"chords": ["C", "G"]}, "generate_melody": {"key": "A minor", "bars": 2},
        "transform_notes": {"notes": NOTES, "transpose": 2, "strum_beats": 0.02, "stretch_factor": 2.0},
        "export_midi_file": {"tracks": {"Keys": NOTES}, "filename": "sweep.mid"},
        "play_notes": {"notes": NOTES, "bpm": 480}, "stop_notes": {},
        "record_notes_to_track": {"track": "Keys", "notes": NOTES, "speed": 4.0},
        "create_song_sketch": {"bars": 4}, "list_audio_inputs": {},
        "listen": {"seconds": 0.2}, "listen_to_cubase": {"bars": 0.5}, "listen_to_meters": {"seconds": 0.3},
        "analyze_audio_file": {"path": wav}, "compare_to_reference": {"reference_path": wav, "mix_path": wav},
        "transcribe_audio": {"path": wav, "bpm": 120}, "show_spectrogram": {"path": wav},
        "analyze_song_structure": {"path": wav, "bpm": 120}, "gain_stage_tracks": {"tracks": [0], "bars": 0.5},
        "fix_resonances": {"track": 0, "bars": 0.5, "verify": False}, "match_loudness": {"bars": 0.5, "max_passes": 1},
        "auto_mix_pass": {"bars": 0.5, "do_gain_staging": False},
        "transcribe_polyphonic": {"path": wav, "bpm": 120, "include_notes": False},
        "transcribe_drums": {"path": wav, "bpm": 120, "include_notes": False},
        "check_mix_against_genre": {"genre": "pop", "path": wav}, "list_genre_profiles": {},
        "list_plugin_slots": {"track": 0}, "search_plugins": {"query": "synth", "kind": "instrument"},
        "load_instrument": {"name": "Retrologue", "track": 0}, "load_effect": {"name": "reverb", "track": 0},
        "create_instrument_track": {"instrument": "HALion", "wait_seconds": 1},
        "write_automation": {"track": 0, "target": "pan", "bars": 0.25, "shape": "lfo", "preroll_beats": 0},
        "clear_automation": {"track": 0, "confirm": True}, "set_automation_mode": {"mode": "touch"},
        "produce_song": {"genre": "house", "bars": 4, "tracks": {"drums": 0, "bass": 1, "chords": 2},
                         "parts": ["drums", "bass", "chords"], "create_tracks": False, "speed": 8.0,
                         "listen_after": False},
        "bind_new_command": None,  # writes to the user's home; covered in test_e2e with a temp dir
        "run_selftest": {}, "review_project": {}, "reconnect": {},
    }
    return table.get(name, {})


# tools that legitimately report {"error": ...} with the mock / without real audio hardware
EXPECTED_ERRORS = {"control_room", "list_audio_inputs", "listen", "create_instrument_track"}


def _content_text(res):
    content = res.content if hasattr(res, "content") else (res[0] if isinstance(res, tuple) else res)
    return content, next((c.text for c in content if getattr(c, "type", None) == "text"), None)


def test_every_tool(mock_bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("NEOCP_EXPORT_DIR", str(tmp_path))
    from neocp import capture

    class FakeRec:
        device_name = "BlackHole 2ch"

        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def stop(self):
            t = np.arange(SR) / SR
            x = 0.2 * np.sin(2 * np.pi * 440 * t) + 0.02 * np.random.default_rng(0).standard_normal(SR)
            return np.stack([x, x], 1).astype(np.float32), SR

    monkeypatch.setattr(capture, "Recorder", FakeRec)
    wav = _wav(tmp_path)
    tools = asyncio.run(S.mcp.list_tools())
    problems = []
    for t in tools:
        a = args_for(t.name, wav)
        if a is None:
            continue
        try:
            res = asyncio.run(S.mcp.call_tool(t.name, a))
        except Exception as e:  # raised through the MCP layer = bug
            problems.append(f"{t.name}: RAISED {type(e).__name__}: {str(e)[:200]}")
            continue
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
            problems.append(f"{t.name}: isError {str(res)[:200]}")
            continue
        content, text = _content_text(res)
        if text is None:
            problems.append(f"{t.name}: no text content")
            continue
        if t.name == "show_spectrogram":
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            problems.append(f"{t.name}: non-JSON output {text[:100]}")
            continue
        if isinstance(data, dict) and "error" in data and t.name not in EXPECTED_ERRORS:
            problems.append(f"{t.name}: error {str(data['error'])[:200]}")
    assert not problems, "\n".join(problems)
