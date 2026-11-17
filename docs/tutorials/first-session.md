# Your first session with NeoCP

In this tutorial, you install NeoCP, connect it to Cubase, and let Claude change your project. You need about 15 minutes.

You need:

* Cubase 13 or later, or Nuendo 13 or later;
* Claude Desktop;
* macOS or Windows.

## 1. Install NeoCP

Follow the installer steps in [Install NeoCP](../how-to/install.md#use-the-installer-recommended). On Windows, also create the three loopMIDI ports. See [Set up the MIDI ports](../how-to/set-up-midi-ports.md#on-windows).

## 2. Start Claude, then Cubase

1. Start Claude Desktop. On macOS, Claude starts the server, and the server creates the MIDI ports.
2. Start Cubase.
3. Create a new project with two instrument tracks.

## 3. Check that Cubase sees NeoCP

1. In the lower zone, open the **MIDI Remote** tab.
2. Make sure that the **NeoCP – Bridge** controller appears.
3. If it does not appear, press **Reload Scripts**.

## 4. Ask Claude about the project

Ask Claude: "What is in my Cubase project?"

Claude lists your two tracks and the tempo.

## 5. Change the project

Ask Claude: "Set the tempo to 100 and set the volume of the first track to -6 dB."

Look at the transport bar and the mixer. The tempo shows 100. The first fader shows -6.0 dB.

## 6. Run the self-test

Ask Claude: "Run the NeoCP self-test."

Claude reports each check. If a check fails, the result tells you how to fix it.

## What next

* To let Claude write notes, follow [Set up Cubase to record notes](../how-to/set-up-note-recording.md).
* To let Claude hear the mix, follow [Set up listening](../how-to/set-up-listening.md).
* For the full list of tools, read the [tools reference](../reference/tools.md).
