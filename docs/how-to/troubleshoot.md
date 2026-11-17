# Troubleshoot NeoCP

| Symptom | Probable cause | Solution |
|---|---|---|
| "timeout waiting for Cubase" | The script is not active, or the ports are not correct. | Press Reload Scripts. Check the MIDI Remote Manager. Make sure that the ports match ([Install the MIDI Remote script](install-midi-remote-script.md)). |
| Claude reads the project, but changes have no effect | Cubase does not process the NRPN messages. | Set `NEOCP_WRITE_MODE=sysex` and restart Claude. |
| "cannot open MIDI ports" on Windows | The loopMIDI ports do not exist. | Create them with the exact names ([Set up the MIDI ports](set-up-midi-ports.md)). |
| `listen_to_cubase` reports "silence captured" | The Cubase output does not reach the loopback device. | Follow [Set up listening](set-up-listening.md). Try `listen` while audio plays. |
| Recorded notes start early or late | The precount is on, or there is latency. | Turn off the precount. Set `NEOCP_RECORD_LATENCY_MS`. |
| Notes are not recorded | The track input does not receive `NeoCP Notes`. | Set the track input to All MIDI Inputs or to `NeoCP Notes`. |
| `load_plugin` fails | Cubase is earlier than 15.0.20. | Load the plug-in manually. Its parameters are accessible from Cubase 13.0.50. |
| Tracks are missing from the list | The project has more than 32 channels. | Use `list_tracks(page=1)` or `bank('next')`. |
| Volumes in dB are not exact | The server follows the fader curve. | No action. The server adjusts in steps to a tolerance of 0.1 dB. |

For more detail, set `NEOCP_LOG=DEBUG` and read the server log. Read the script messages in the Cubase Script Console.
