# Comparison with other MCP servers

> **Not verified.** The original author wrote this comparison. Nobody has tested these claims for this repository. The [validation matrix](../reference/validation-matrix.md) records what is tested.

NeoCP started as an analysis of two existing servers:

* [hedidjs/cubase-mcp](https://github.com/hedidjs/cubase-mcp) sends MIDI CC messages in one direction through a Cubase *Generic Remote*.
* [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp) is the reference design.

The goal was to redesign the Cubase server until it matched or exceeded the Ableton server.

| | hedidjs/cubase-mcp | ableton-mcp | NeoCP |
|---|---|---|---|
| Communication | MIDI CC, one direction | Bidirectional TCP socket | Bidirectional SysEx JSON-RPC and 14-bit NRPN |
| Reads the DAW state | no | yes | yes, with a live mirror from events |
| Setup in the DAW | Map each CC by hand in Generic Remote | Remote Script | One MIDI Remote script, detected automatically |
| Tracks | 8 fixed, no names | yes | Banks of 32, with names and colors, with paging |
| Volume, pan, mute, solo, arm, monitor | partial, no feedback | yes | yes, in real dB, checked against the Cubase display |
| Sends, EQ, pre-filter, strip modules | no | partial | yes |
| Loaded plug-ins | no | yes | yes (name and vendor per slot) |
| Plug-in parameters | no | yes | yes (DirectAccess: any parameter, by name) |
| Load plug-ins | no | yes (browser) | yes (Cubase 15, plug-in manager) |
| Menu commands | 5 | about 10 | 1454, with search |
| Tempo, position, locators, cycle | no | yes | yes |
| Write notes | exports .mid only | yes | yes, recorded live, synchronized, faster than real time, quantized |
| Music generation | basic | no | Chords, Roman numerals, voice leading, 15 drum styles, bass lines, arpeggios, melodies, full song |
| VU meters, Control Room | no | no | yes |
| Listen to the audio | no | no | yes: LUFS, true peak, spectrum, resonances, key, chords, tempo, stereo, mixing advice |
| Hum to notes on a track | no | no | yes |
| Drums to MIDI (kick, snare, hi-hat) | no | no | yes, with grid and swing, recorded on your drum track |
| Mix profiles by genre | no | no | yes, 15 genres |
| Produce a song from start to end | no | no | yes, `produce_song` |
| Self-test of the installation | no | no | yes, `run_selftest` |
| Polyphonic transcription (audio to MIDI chords, bass, melody) | no | no | yes, with chord names, recorded on your tracks |
| Spectrogram image | no | no | yes, with a bar grid |
| Song structure | no | no | yes: intro, verse, chorus, bridge, with markers in the project |
| Closed-loop mixing | no | no | yes: listen, adjust faders and EQ, listen again |
| Automation (curves, fades, LFO, sidechain, trance gate) | no | no | yes: volume, pan, sends, quick controls, any plug-in parameter |
| Load an instrument or effect by name | no | yes | yes, "add a Retrologue", "add a reverb" (Cubase 15) |
| MCP tools | 13 | about 20 | 87, plus resources and prompts |
| Tests | no | partial | End to end: the real script runs in a simulated Cubase |
