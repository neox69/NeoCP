# How NeoCP writes automation

`write_automation` works as an engineer who moves a fader. It does these steps:

1. It sets Cubase to *Auto-Latch*.
2. It arms automation writing on the track.
3. It starts playback a short time before the target bar.
4. During playback, it sends the curve at 40 values per second.
5. It ends the write with *Punch Out*.
6. It sets the track to *Read*, so that you hear the result.

Before you record, `dry_run=True` shows a preview of the curve and changes nothing.

The write happens in real time. Thus 8 bars of automation take 8 bars plus one bar of pre-roll.

## Fader calibration

The fader position is not linear in dB. To convert dB to a fader position, NeoCP measures the fader curve once per session. This takes a few seconds the first time.

## Example requests

| Request | Arguments |
|---|---|
| "Fade in the pad over 8 bars from bar 1" | `target="volume"`, `shape="fade_in"` |
| "Sidechain-style pumping on the bass, every quarter note, 12 dB" | `shape="duck"`, `depth=12`, `rate_beats=1` |
| "Open the synth filter from bar 17 to bar 25" | `target="plugin:Retrologue/Cutoff"`, `shape="ramp"`, `start=0.1`, `end=0.9` |
| "Pan from side to side every 2 bars" | `target="pan"`, `shape="lfo"`, `rate_beats=8` |
