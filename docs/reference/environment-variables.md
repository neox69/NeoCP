# Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `NEOCP_LISTEN_DEVICE` | Automatic (finds a loopback device) | The audio device that the server listens to. A partial name or an ID. |
| `NEOCP_EXPORT_DIR` | `~/Documents/NeoCP` | The folder for recorded WAV files and exported MIDI files. |
| `NEOCP_WRITE_MODE` | `nrpn` | Set to `sysex` if the controls do not respond to NRPN. The script then calls `setProcessValue`. |
| `NEOCP_RECORD_LATENCY_MS` | `0` | A time offset in milliseconds for recorded notes. Can be negative. |
| `NEOCP_PORT_TO_CUBASE` | `NeoCP to Cubase` | The name of the request port. |
| `NEOCP_PORT_FROM_CUBASE` | `NeoCP from Cubase` | The name of the response port. |
| `NEOCP_PORT_NOTES` | `NeoCP Notes` | The name of the notes port. |
| `NEOCP_LOG` | `INFO` | The log level. Set to `DEBUG` to diagnose problems. |
