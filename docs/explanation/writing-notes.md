# How NeoCP writes notes

The MIDI Remote API cannot insert MIDI events into parts. This is a limit of the Steinberg API. Thus `record_notes_to_track` records the notes as a musician does:

1. It selects the track and arms it for recording.
2. It moves the cursor to a position before the start (pre-roll).
3. Optionally, it raises the tempo. With `speed=2`, it records at double speed. The musical positions do not change.
4. It starts recording.
5. It plays the notes through the `NeoCP Notes` port with precise timing.
6. It stops, and restores the tempo and the arm state.
7. If you ask for it (`quantize_after=True`), it quantizes the recorded notes.

This method depends on the Cubase settings in [Set up Cubase to record notes](../how-to/set-up-note-recording.md).

The alternative is `export_midi_file`. It writes a multi-track `.mid` file to `~/Documents/NeoCP`. You can drag the file into the project.
