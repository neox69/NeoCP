# Run the self-test

The self-test checks each NeoCP function against the running Cubase. It restores every value that it changes.

1. Start Cubase with a project open. The project must have at least one track, and a track must be selected. On an empty project, the track, DirectAccess and plug-in checks fail.
2. Ask Claude: "Run the NeoCP self-test."

   On Windows, or on macOS with permanent IAC ports, you can also run the self-test from a terminal:

   ```bash
   neocp-install --selftest
   ```

   On macOS without IAC ports, the terminal command cannot reach Cubase. The command creates new virtual ports, and Cubase stays connected to the ports of the server that Claude started.

3. Read the result. For each failure, the result tells you how to fix it.

The self-test checks:

* the connection and the script version;
* that events arrive;
* the tracks and the tempo (it reads, writes and restores the tempo);
* value writes by NRPN and by SysEx (it moves a pan control and moves it back);
* the display strings;
* the commands;
* DirectAccess and the plug-in manager;
* the notes port and the audio devices.

If NRPN writes fail and SysEx writes work, the server switches to SysEx for the session. To make this permanent, set `NEOCP_WRITE_MODE=sysex`.

## Test manually

Ask Claude, in this order:

1. "Show the connection status with Cubase." This uses `connection_status`. The result must show the script, the number of controls, and whether DirectAccess is available.
2. "Set the tempo to 100 and press play."
3. "Listen to bars 1 to 8." This uses `listen_to_cubase`. The result must show LUFS, the key, and the chords.
4. "Show me the spectrogram." This uses `show_spectrogram`.
