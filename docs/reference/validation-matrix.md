# Validation matrix

This table records which tools work in which environment. The [validation strategy](../explanation/validation-strategy.md) defines the values. The [validation procedure](../how-to/validate-against-cubase.md) tells you how to fill a cell.

The table has one row for each of the 87 tools. The "Needs" column tells you what the tool requires.

## Environments

| Column | Operating system | Cubase version | NeoCP version | Date | Tester |
|---|---|---|---|---|---|
| macOS / Cubase 15 | | | | | |
| macOS / Cubase 16 | macOS 27.2 | Cubase 16.0.5 | 1.3.4 | 2026-11-17 | fork maintainer |
| Windows / Cubase 15 | | | | | |
| Windows / Cubase 16 | | | | | |

## Self-test

Record the output of the self-test for each environment. [Run the self-test](../how-to/run-self-test.md) tells you how to run it.

The macOS / Cubase 16 results come from `run_selftest` called by Claude, with a project that had two tracks and a selected track.

| Self-test check | macOS / Cubase 15 | macOS / Cubase 16 | Windows / Cubase 15 | Windows / Cubase 16 |
|---|---|---|---|---|
| handshake | not run | pass | not run | not run |
| script version | not run | pass (versions match) | not run | not run |
| host | not run | pass | not run | not run |
| idle callbacks | not run | pass | not run | not run |
| write mode | not run | not reported | not run | not run |
| tracks mirrored | not run | pass | not run | not run |
| selected track | not run | pass | not run | not run |
| tempo read | not run | pass | not run | not run |
| tempo write | not run | pass | not run | not run |
| cursor read | not run | pass | not run | not run |
| round trip | not run | pass | not run | not run |
| value write (NRPN) | not run | pass | not run | not run |
| value write (SysEx) | not run | pass | not run | not run |
| display strings | not run | pass | not run | not run |
| commands bound | not run | pass (1254 of 1454) | not run | not run |
| transport toggle | not run | pass | not run | not run |
| DirectAccess | not run | pass | not run | not run |
| plug-in manager | not run | pass | not run | not run |
| notes port | not run | pass | not run | not run |
| audio capture | not run | pass | not run | not run |
| loopback for listening | not run | n/a (no BlackHole) | not run | not run |

## Installers

| Check | macOS | Windows |
|---|---|---|
| Double-click installer on a clean computer | not run | not run |
| `neocp-install --check` | not run | not run |
| `neocp-install --uninstall` | not run | not run |
| Import `NeoCP_Bridge.midiremote` in MIDI Remote Manager | not run | not run |

## Tools

| Group | Tool | Needs | macOS / Cubase 15 | macOS / Cubase 16 | Windows / Cubase 15 | Windows / Cubase 16 | Notes |
|---|---|---|---|---|---|---|---|
| Connection and session | `connection_status` | Cubase | not run | pass | not run | not run | |
| Connection and session | `reconnect` | Cubase | not run | not run | not run | not run | |
| Connection and session | `get_session_info` | Cubase | not run | fail | not run | not run | Timed out on the handshake on the first call of a session. Not tried again after the connection worked. |
| Connection and session | `list_tracks` | Cubase | not run | pass | not run | not run | 9 tracks with names. |
| Connection and session | `get_track_info` | Cubase | not run | pass | not run | not run | Track name matched without regard to case. |
| Connection and session | `get_selected_track_info` | Cubase | not run | not run | not run | not run | |
| Connection and session | `review_project` | Cubase | not run | pass | not run | not run | Found the armed track, all tracks panned center, no sends. |
| Diagnostics | `run_selftest` | Cubase | not run | not run | not run | not run | |
| Diagnostics | `bind_new_command` | Cubase | not run | not run | not run | not run | |
| Transport | `transport` | Cubase | not run | not run | not run | not run | |
| Transport | `get_transport_state` | Cubase | not run | pass | not run | not run | |
| Transport | `set_tempo` | Cubase | not run | not run | not run | not run | |
| Transport | `set_position` | Cubase | not run | pass | not run | not run | |
| Transport | `set_loop_range` | Cubase | not run | not run | not run | not run | |
| Transport | `set_cycle` | Cubase | not run | not run | not run | not run | |
| Transport | `set_metronome` | Cubase | not run | not run | not run | not run | |
| Mixer | `select_track` | Cubase | not run | not run | not run | not run | |
| Mixer | `set_track_volume` | Cubase | not run | not run | not run | not run | |
| Mixer | `set_track_pan` | Cubase | not run | not run | not run | not run | |
| Mixer | `set_track_state` | Cubase | not run | not run | not run | not run | |
| Mixer | `set_send` | Cubase | not run | not run | not run | not run | |
| Mixer | `mixer_overview` | Cubase | not run | not run | not run | not run | |
| Mixer | `get_meters` | Cubase | not run | not run | not run | not run | |
| Mixer | `bank` | Cubase | not run | not run | not run | not run | |
| Mixer | `control_room` | Cubase Pro or Nuendo | not run | not run | not run | not run | |
| Tracks and editing | `create_track` | Cubase | not run | not run | not run | not run | |
| Tracks and editing | `track_operation` | Cubase | not run | not run | not run | not run | |
| Tracks and editing | `project_action` | Cubase | not run | not run | not run | not run | |
| Tracks and editing | `set_quantize_grid` | Cubase | not run | not run | not run | not run | |
| Commands | `search_commands` | Cubase | not run | not run | not run | not run | |
| Commands | `list_command_categories` | Cubase | not run | not run | not run | not run | |
| Commands | `execute_command` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_eq_band` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_pre_filter` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_strip_module` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_insert_state` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_quick_control` | Cubase | not run | not run | not run | not run | |
| Channel strip | `set_instrument_parameter` | Cubase | not run | not run | not run | not run | |
| Channel strip | `instrument_parameter_bank` | Cubase | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `explore_host_objects` | Cubase 13.0.50+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `find_host_objects` | Cubase 13.0.50+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `get_object_parameters` | Cubase 13.0.50+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `set_object_parameter` | Cubase 13.0.50+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `set_plugin_parameter` | Cubase 13.0.50+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `list_available_plugins` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `load_plugin` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins (DirectAccess) | `remove_plugin` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins by name | `list_plugin_slots` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins by name | `search_plugins` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins by name | `load_instrument` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins by name | `load_effect` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Plug-ins by name | `create_instrument_track` | Cubase 15.0.20+ | not run | not run | not run | not run | |
| Music generation | `music_theory` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `generate_chord_progression` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `generate_drum_pattern` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `generate_bassline` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `generate_arpeggio` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `generate_melody` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `transform_notes` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `export_midi_file` | nothing (offline) | not run | not run | not run | not run | |
| Music generation | `create_song_sketch` | nothing (offline) | not run | not run | not run | not run | |
| Notes | `play_notes` | Cubase, notes setup | not run | not run | not run | not run | |
| Notes | `stop_notes` | Cubase, notes setup | not run | not run | not run | not run | |
| Notes | `record_notes_to_track` | Cubase, notes setup | not run | not run | not run | not run | |
| Listening | `listen_to_meters` | Cubase | not run | not run | not run | not run | |
| Listening | `listen_to_cubase` | Cubase, loopback audio | not run | not run | not run | not run | |
| Listening | `list_audio_inputs` | Cubase, loopback audio | not run | not run | not run | not run | |
| Listening | `listen` | Cubase, loopback audio | not run | not run | not run | not run | |
| Listening | `transcribe_audio` | microphone | not run | not run | not run | not run | |
| Audio analysis | `analyze_audio_file` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `compare_to_reference` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `show_spectrogram` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `analyze_song_structure` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `transcribe_polyphonic` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `transcribe_drums` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `check_mix_against_genre` | nothing (offline) | not run | not run | not run | not run | |
| Audio analysis | `list_genre_profiles` | nothing (offline) | not run | not run | not run | not run | |
| Closed-loop mixing | `gain_stage_tracks` | Cubase, loopback audio | not run | not run | not run | not run | |
| Closed-loop mixing | `fix_resonances` | Cubase, loopback audio | not run | not run | not run | not run | |
| Closed-loop mixing | `match_loudness` | Cubase, loopback audio | not run | not run | not run | not run | |
| Closed-loop mixing | `auto_mix_pass` | Cubase, loopback audio | not run | not run | not run | not run | |
| Automation | `write_automation` | Cubase | not run | partial | not run | not run | A linear 4-bar volume fade written with beat points: 232 values, at most 6.7 ms late. Two problems: see Findings. |
| Automation | `clear_automation` | Cubase | not run | not run | not run | not run | |
| Automation | `set_automation_mode` | Cubase | not run | not run | not run | not run | |
| Production | `produce_song` | Cubase, notes setup | not run | not run | not run | not run | |
| Advanced | `list_controls` | Cubase | not run | pass | not run | not run | Read the volume value and display at several bars. |
| Advanced | `set_control` | Cubase | not run | not run | not run | not run | |

## Findings

### macOS / Cubase 16

1. `write_automation` with `shape="fade_out"` and `curve="linear"` gives a curved fade when the target is in dB. The dry-run preview showed it; writing uses the same curve. The code changes a `"linear"` curve to `"exp"` for dB targets. The default value is also `"linear"`, so an explicit request for a linear fade is not possible with this shape. Workaround: use `points`.
2. `write_automation` with points that use `"bar"` and no `start_bar` writes a flat line. A `"bar"` point is relative to `start_bar`, which defaults to 1. Points outside the window of `bars` are dropped with no warning. Workaround: set `start_bar`, or use `"beat"` points.
3. NeoCP cannot rename a track. This is a known limit of the MIDI Remote API. See [Limitations](limitations.md).
4. The self-test fails on an empty project. With no tracks, these checks fail: tracks mirrored, selected track, DirectAccess, and plug-in manager. The value-write checks do not run. Workaround: open a project with at least one track, and select a track.
5. On macOS, `neocp-install --selftest` from a terminal cannot reach Cubase without permanent IAC ports. The command creates new virtual ports, and Cubase stays connected to the ports of the server that Claude started. Workaround: ask Claude to run the self-test, or create the IAC ports. See [Set up the MIDI ports](../how-to/set-up-midi-ports.md).
6. Cubase 16 binds 1254 of the 1454 commands in the catalog. Nobody has checked which 200 commands are missing.
