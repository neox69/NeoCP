# Install the MIDI Remote script

The installer does these steps for you. Use this page if you install manually or if the script is not detected.

## Copy the script

For Cubase:

```bash
neocp-install
```

For Nuendo:

```bash
neocp-install --app Nuendo
```

The command copies the script to this folder:

* On macOS: `~/Documents/Steinberg/Cubase/MIDI Remote/Driver Scripts/Local/NeoCP/Bridge/`
* On Windows: `Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\Local\NeoCP\Bridge\`

Alternatively, import the release file: in Cubase, open **MIDI Remote Manager > Scripts > Import Script** and select `NeoCP_Bridge.midiremote`.

## Activate the script in Cubase

1. In the lower zone, open the **MIDI Remote** tab.
2. Press **Reload Scripts**, or restart Cubase.
3. Make sure that the **NeoCP – Bridge** controller appears. Cubase detects it when the ports exist.
4. If the controller does not appear, add it manually:
   1. Press **+** (Add MIDI Controller Surface).
   2. Select vendor **NeoCP** and model **Bridge**.
   3. Set the MIDI input to `NeoCP to Cubase`.
   4. Set the MIDI output to `NeoCP from Cubase`.
5. Open **Studio > MIDI Remote Manager**. Make sure that the device is active.

## Read the script messages

Open **MIDI Remote > menu > Script Console**. When the script activates, it shows `[NeoCP] activated, NNNN slots`.
