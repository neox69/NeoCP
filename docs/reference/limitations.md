# Limitations

These limits come from the Steinberg MIDI Remote API.

* NeoCP cannot rename tracks or markers.
* NeoCP cannot write or delete specific notes inside a part. It records notes instead. See [How NeoCP writes notes](../explanation/writing-notes.md).
* NeoCP cannot read the list of markers, events or parts in the project. To navigate, it uses commands such as `Locate Next Marker`.
* Some commands open a dialog that needs a click: Add Track > Instrument, Import MIDI, and Export Mixdown.
