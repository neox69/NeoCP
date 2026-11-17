# Set up listening

`listen_to_meters` needs no setup. The tools that analyze real audio need a loopback device: a virtual sound card whose output you can record as an input. These tools need it:

`listen_to_cubase`, `show_spectrogram`, `analyze_song_structure`, `gain_stage_tracks`, `fix_resonances`, `match_loudness`, `auto_mix_pass` and `compare_to_reference`.

The server detects loopback devices automatically. To select a specific device, set `NEOCP_LISTEN_DEVICE` to its name or ID.

## On macOS, with BlackHole

1. Install [BlackHole 2ch](https://existential.audio/blackhole/). It is free. You can also run `brew install blackhole-2ch`.
2. Open **Audio MIDI Setup**. Click **+ > Create Multi-Output Device**.
3. Select your interface or speakers, and BlackHole 2ch.
4. Set your interface as the primary device.
5. Turn on drift correction for BlackHole.
6. In Cubase, open **Studio > Studio Setup > Audio System**. Select the multi-output device as the driver.
7. In **Studio > Audio Connections > Outputs**, route *Stereo Out* to channels 1-2 of that device.
8. Ask Claude: "List my audio inputs." This uses `list_audio_inputs`. BlackHole must appear, marked as loopback.

For lower latency with Cubase Pro, use an aggregate device instead:

1. Create an aggregate device with your interface and BlackHole. Select it as the driver.
2. In **Audio Connections > Control Room**, add a *Monitor* on the BlackHole channels. Or send the mix to those outputs with *Studio Sends*.

## On Windows

Cubase uses ASIO, which usually takes exclusive control of the sound card. Use one of these options. The first is the simplest.

### Option A: your interface has loopback

Many interfaces have loopback: Steinberg UR (*Loopback* in dspMixFx), Focusrite Scarlett 3rd and 4th generation (*Loopback 1-2* inputs), RME (TotalMix *Loopback*), MOTU, Audient EVO.

1. Turn on loopback in the interface software.
2. Set `NEOCP_LISTEN_DEVICE` to the device name, for example `"Loopback"`.

### Option B: VoiceMeeter Banana

1. Install [VoiceMeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm). It is free.
2. In Cubase, select **Voicemeeter Virtual ASIO** as the driver.
3. In VoiceMeeter, route the virtual input to **A1** (your interface) and to **B1**.
4. Set `NEOCP_LISTEN_DEVICE="Voicemeeter Out B1"`.

### Option C: VB-Cable with the Generic Low Latency ASIO Driver

1. Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/).
2. In **Studio Setup > Generic Low Latency ASIO Driver**, turn on your sound card and **CABLE Input** as outputs.
3. In **Audio Connections > Outputs**, create a stereo bus to CABLE Input. Turn on Stereo Out there. In Cubase Pro, you can use Control Room instead.
4. The server listens to `CABLE Output`.

## Without a loopback device

1. Export the mix with **File > Export > Audio Mixdown**, or ask Claude for `project_action('export_mixdown')`.
2. Ask Claude: "Analyze the file …/Mixdown.wav." This uses `analyze_audio_file`.

All analysis functions work on the file. The closed loop (listen, adjust, listen again) does not work.

## Microphone

`transcribe_audio` uses the default system input, or the input that you set with `device`. On macOS, the system asks for microphone permission the first time, for the app that starts the server (Claude or Terminal). For precise notes, sing or play one voice only, with the Cubase metronome on.
