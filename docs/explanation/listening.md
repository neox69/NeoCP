# How NeoCP listens

NeoCP has three ways to listen. They need different amounts of setup.

| Way | Tool | Setup |
|---|---|---|
| Meters | `listen_to_meters` | None |
| Real audio | `listen_to_cubase` | A loopback audio device |
| Microphone or file | `transcribe_audio` | A microphone, or none for a file |

## Meters

`listen_to_meters` plays the project and samples the VU meter of each track. It reports which track is loudest, which track clips, and which track is silent.

## Real audio

`listen_to_cubase` moves the cursor, plays N bars, records the Cubase output, and analyzes it. The analysis gives:

* integrated loudness (LUFS, ITU-R BS.1770 with gating), short-term loudness, loudness range (LRA), and true peak (dBTP);
* dynamics: crest factor, RMS, clipping, and DC offset;
* stereo image: correlation, width, and mono compatibility;
* spectral balance by band, spectral tilt, and resonances with their musical note;
* tempo, key, and chords with bar numbers;
* specific mixing advice, for example "resonance at 312 Hz: narrow cut".

With `solo_track`, it listens to one track. It saves the audio as a WAV file. `compare_to_reference` compares the WAV file with a reference track.

Cubase output reaches the server only through a loopback device. [Set up listening](../how-to/set-up-listening.md) tells you how to configure one.

## Microphone

`transcribe_audio` listens to the microphone or reads a file. It converts a hummed, sung, or played melody into notes. With `record_to_track`, it records the notes on a track.

## Polyphonic transcription

`transcribe_polyphonic` converts audio with chords, bass and melody into MIDI. It separates the parts and names the chord in each bar. It exports one `.mid` file with one track per part. It can also record the parts on your tracks.

The method uses only numpy. It uses no large models:

1. It builds a spectrogram on a semitone scale.
2. It applies non-negative matrix factorization (NMF) with harmonic templates for the piano keys.
3. It detects note onsets with high time resolution. Thus notes start where they sound, and repeated notes stay separate.
4. It removes false harmonics (octave and fifth ghosts) and spectral leakage in the bass.

## Accuracy claims

The analysis code is tested with synthetic signals. The project states these results. They come from the test suite, not from real recordings:

* A 1 kHz sine at 0 dBFS in stereo measures 0.0 LUFS, as the standard specifies.
* The progression C–Am–F–G is detected as C major with its four chords.
* Chords with bass: 100 % accuracy.
* Pad, melody and percussion: about 87 % precision and about 90 % recall.

Transcription works best with clean material: piano, guitar, pads, or synths. With loud drums or dense mixes it is less accurate. In that case, adjust `threshold` and check the result.
