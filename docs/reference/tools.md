# Tools

NeoCP has 87 MCP tools, 2 resources, and 3 prompts. Each tool has a description in `neocp/server.py`. Claude reads that description. The [validation matrix](validation-matrix.md) records which tools are tested in a real Cubase.

## Connection and session

* `connection_status`, `reconnect`, `get_session_info`, `list_tracks`, `get_track_info`, `get_selected_track_info`
* `review_project`: an instant review of the project. It finds forgotten solos, armed tracks, faders that are too high or not balanced, all tracks panned to the center, and tracks without sends.

## Transport

* `transport`: play, stop, record, go to the start, the end, a locator, or a marker.
* `get_transport_state`, `set_tempo`, `set_position`, `set_loop_range`, `set_cycle`, `set_metronome`

## Mixer

* `select_track`, `set_track_volume` (in dB), `set_track_pan`, `set_send`, `mixer_overview`, `get_meters`, `bank`
* `set_track_state`: mute, solo, arm, monitor, automation read and write, and open the editor or the instrument.
* `control_room`: Cubase Pro and Nuendo only.

## Tracks and editing

* `create_track`: audio, mono, instrument, MIDI, sampler, FX, group, folder, marker, chord, arranger, tempo, VCA, and more.
* `track_operation`: duplicate, delete, fold, freeze, color, and more.
* `project_action`: undo, save, copy, paste, split at the cursor, glue, quantize, legato, insert a marker, export the mixdown, render in place, and more.
* `set_quantize_grid`

## Commands

* `search_commands`, `list_command_categories`
* `execute_command`: runs any of the 1454 Cubase commands in the catalog.
* `bind_new_command`: adds a command that is not in the catalog. See [Add a Cubase command](../how-to/add-cubase-command.md).

## Channel strip

`set_eq_band`, `set_pre_filter`, `set_strip_module`, `set_insert_state`, `set_quick_control`, `set_instrument_parameter`, `instrument_parameter_bank`

## Plug-ins and internal objects (DirectAccess)

Requires Cubase 13.0.50 or later. Loading and removing plug-ins requires Cubase 15.0.20 or later.

* `explore_host_objects`, `find_host_objects`, `get_object_parameters`, `set_object_parameter`
* `set_plugin_parameter`: sets a parameter by name, for example "set the Compressor threshold to -18 dB".
* `list_available_plugins`, `load_plugin`, `remove_plugin`

## Instruments and effects by name

Requires Cubase 15.0.20 or later.

`load_instrument`, `load_effect`, `create_instrument_track`, `search_plugins`, `list_plugin_slots`

## Music generation

These tools work without Cubase.

* `music_theory`, `generate_chord_progression`, `generate_drum_pattern`, `generate_bassline`, `generate_arpeggio`, `generate_melody`, `create_song_sketch`
* `transform_notes`: transposes and transforms notes. `strum_beats` and `stretch_factor` give the same effect as the Cubase 16 Strum and Stretch functions.
* `export_midi_file`: writes a multi-track `.mid` file.

## Notes

* `play_notes`, `stop_notes`
* `record_notes_to_track`: see [How NeoCP writes notes](../explanation/writing-notes.md).

## Listening

See [How NeoCP listens](../explanation/listening.md).

* `listen_to_meters`: needs no setup.
* `listen_to_cubase`, `listen`, `list_audio_inputs`: need a loopback device.
* `transcribe_audio`: a microphone or a file.

## Audio analysis

These tools work on audio files without Cubase.

* `analyze_audio_file`, `compare_to_reference`
* `show_spectrogram`: returns an image.
* `analyze_song_structure`: can insert markers in the project.
* `transcribe_polyphonic`: converts chords, bass and melody to MIDI.
* `transcribe_drums`: converts kick, snare and hi-hat to MIDI.
* `check_mix_against_genre`, `list_genre_profiles`

## Closed-loop mixing

These tools need a loopback device.

* `gain_stage_tracks`
* `fix_resonances`: finds a resonance, cuts it with the EQ, and listens again.
* `match_loudness`: brings the master to -14 LUFS or another target.
* `auto_mix_pass`: does the full loop and reports the result before and after.

## Automation

See [How NeoCP writes automation](../explanation/automation.md).

* `write_automation`: ramps, fade in and out, LFO, sidechain-style duck, swell, and trance gate by steps or points per bar. Targets: volume, pan, mute, sends, quick controls, and instrument or plug-in parameters.
* `clear_automation`, `set_automation_mode`

## Production

* `produce_song`: sets the genre tempo, creates tracks and instruments, records drums, bass, chords and melody, sets the loop, listens, and checks the result against the genre.

## Diagnostics

* `run_selftest`: see [Run the self-test](../how-to/run-self-test.md).

## Advanced

* `list_controls`, `set_control`: direct access to any control of the bridge script.

## Resources

* `neocp://commands`: the command catalog.
* `neocp://drum-map`: the drum note map.

## Prompts

* `produce_track`
* `mix_check`
* `transcribe_reference`
