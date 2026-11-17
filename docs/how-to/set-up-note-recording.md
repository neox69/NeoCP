# Set up Cubase to record notes

NeoCP records notes by playing them through the `NeoCP Notes` port. [How NeoCP writes notes](../explanation/writing-notes.md) explains why. Make these settings in Cubase:

1. Turn off the precount. Click the precount icon in the transport bar, or clear **Transport > Metronome Setup > Use Count-in**.
2. Set the input of the MIDI or instrument track to *All MIDI Inputs* (the default) or to `NeoCP Notes`.
3. Set the MIDI record mode to *New Parts*. Use the transport bar or **Transport > MIDI Record Mode**.
4. Turn off *Auto-Quantize*. Claude quantizes when you ask for it.
5. Optional: select **Preferences > MIDI > MIDI Thru Active**. Then you hear the notes from `play_notes` without recording. The track must be selected and armed, or have monitoring on.
6. To record faster than real time (`speed=2`), turn off the tempo track. Claude changes the fixed project tempo during the take and restores it after.

If the recorded notes are early or late, set `NEOCP_RECORD_LATENCY_MS` (for example, `-10` or `15`). Or ask Claude to quantize with `quantize_after=True`.

## Automation and instruments

* `write_automation` needs no setup. NeoCP sets the automation mode to *Auto-Latch* while it writes, and back to *Touch* after.
* `load_instrument`, `load_effect` and `create_instrument_track` need Cubase or Nuendo 15.0.20 or later. Cubase can show its *Add Track* dialog when NeoCP creates an instrument track. Accept the dialog with any instrument or none. NeoCP then loads the instrument that you asked for. In earlier versions, load the instrument manually. Its parameters stay accessible.
