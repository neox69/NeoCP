# Set up the MIDI ports

NeoCP uses three MIDI ports. The [architecture](../explanation/architecture.md#the-ports) explains their purpose. The names must be exact:

* `NeoCP to Cubase`
* `NeoCP from Cubase`
* `NeoCP Notes`

## On macOS

You do not need to do anything. The server creates virtual ports when it starts.

Virtual ports exist only while the server runs. If Cubase starts before Claude, press **Reload Scripts** in the MIDI Remote tab after Claude starts.

To make permanent ports:

1. Open **Audio MIDI Setup**.
2. Select **Window > Show MIDI Studio**.
3. Double-click **IAC Driver**.
4. Select **Device is online**.
5. Add three buses with the names above.

The server finds the existing ports and uses them.

## On Windows

Python cannot create virtual MIDI ports on Windows. Use loopMIDI:

1. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html). It is free.
2. Type each port name and click **+**.
3. In the loopMIDI tray icon menu, set loopMIDI to start with Windows.

## On Linux

The server creates the ports with ALSA. Cubase does not run on Linux. Use Linux only for development and tests.

## Use other port names

Set `NEOCP_PORT_TO_CUBASE`, `NEOCP_PORT_FROM_CUBASE` and `NEOCP_PORT_NOTES`. The [environment variables reference](../reference/environment-variables.md) lists them.
