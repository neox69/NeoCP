# NeoCP — AI co-producer for Cubase

Claude (or any MCP client) can now see, control and **listen to** Steinberg Cubase / Nuendo 12+.

## Install (2 minutes)
* **macOS**: download `NeoCP-<version>-macOS.zip`, unzip it and double-click **Install NeoCP (macOS).command**. If macOS blocks it, right-click → Open.
* **Windows**: download `NeoCP-<version>-Windows.zip`, unzip it and double-click **Install NeoCP (Windows).bat**. Then create 3 ports in loopMIDI: `NeoCP to Cubase`, `NeoCP from Cubase`, `NeoCP Notes`.
* Restart Claude Desktop and Cubase, then ask Claude: *"what's in my Cubase project?"*

The installer needs no admin rights and no pre-installed Python. Full guide (Spanish): `docs/CONFIGURAR_CUBASE.md`, inside the ZIP.

## New in 1.3.2
* Track names fall back to the object title sent with each channel's volume value, for hosts where channel title callbacks are not delivered. The self test now says whether the bank is empty or names are missing.

## New in 1.3.1
* `review_project`: instant project review without audio (forgotten solos and mutes, armed tracks, hot or unbalanced faders, everything centred, no sends, default tempo), with the tool that fixes each finding.

## New in 1.3 (Cubase 16 ready)
* **Cubase 16 support**: up to 16 sends per channel (Pro), and `bind_new_command` / `neocp-install --add-command "Category/Command"` to bind any new key command (stem separation, Strum, Stretch Selection, Instrument Stacks...) without waiting for a release.
* **Self test** (`run_selftest`, `neocp-install --selftest`): checks every feature on your setup with reversible writes, and switches to SysEx writes automatically if NRPN does not reach Cubase.
* **Drums to MIDI** (`transcribe_drums`): kick, snare, closed and open hi-hat, with step grid and swing; records into a drum track.
* **Genre mix profiles** (`check_mix_against_genre`, 15 genres) and `produce_song`, an end-to-end producer (tempo → tracks and instruments → parts recorded → loop → listen and genre check).
* `transform_notes`: Strum and Stretch (Cubase 16 style). New MCP prompts: `mix_check`, `transcribe_reference`.
* Fixes from a code review: mixer bank moves are verified with Cubase (never writes to the wrong track), resync after a script reload, tool calls serialised, note recording and automation timing anchored to the start command, robust parsing of Cubase values (`-oo dB`, `kHz`), positions with carry, working Cubase 12 fallback, and installer hardening.

## New in 1.2
* **Polyphonic transcription** (`transcribe_polyphonic`): turns audio with chords, bass and melody into MIDI, split into parts with chord names per half bar. Exports a MIDI file or records the parts straight into Cubase tracks. Uses harmonic-template NMF, high-resolution onsets and ghost-note removal, all numpy, no ML models.

## New in 1.1
* **Automation writing** (`write_automation`): ramps, fades, LFO, sidechain-style ducking, swells, trance gates and breakpoints on volume, pan, mute, sends, quick controls, instrument parameters or any plug-in parameter. It is performed in real time with Auto-Latch and the track is left in Read mode.
* **Instruments and effects by name** (Cubase 15+): `load_instrument`, `load_effect`, `create_instrument_track`, `search_plugins`, `list_plugin_slots`.

## What's inside (87 tools)
* Live, two-way control through a MIDI Remote script: transport, tempo, locators, a 32-channel mixer with real dB values, sends, EQ, pre-filter, strip modules, inserts, instrument, quick controls and Control Room.
* All 1454 Cubase key commands, with search.
* DirectAccess (Cubase 13.0.50+): read and set any plug-in parameter by name. Load plug-ins on Cubase 15+.
* Music generation: chords and roman numerals, 15 drum styles, bass lines, arpeggios, melodies and song sketches, recorded straight into tracks or exported as MIDI.
* **Listening**: loudness (LUFS), true peak, spectrum, resonances, key, chords, tempo, song structure, spectrogram images, reference comparison, and hum → notes transcription.
* **Closed-loop mixing**: gain staging, resonance notching and master loudness matching (listen → adjust → listen again).

## Assets
| File | What it is |
|---|---|
| `NeoCP-*-macOS.zip` / `NeoCP-*-Windows.zip` | One-click installers + wheel + docs |
| `neocp-*.whl`, `neocp-*.tar.gz` | Python package (`uv tool install` / `pip install`) |
| `NeoCP_Bridge.midiremote` | Cubase MIDI Remote package (MIDI Remote Manager → Import Script) |
| `NeoCP_Bridge.js` | Raw MIDI Remote script |
