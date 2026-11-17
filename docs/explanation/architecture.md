# Architecture

NeoCP has two parts. A Python MCP server runs on your computer. A MIDI Remote script runs inside Cubase or Nuendo. The two parts talk through three MIDI ports.

```
Claude ──MCP (stdio)──> neocp (Python)
                          │  "NeoCP to Cubase"   SysEx requests + NRPN writes
                          │  "NeoCP from Cubase" SysEx responses + state events
                          │  "NeoCP Notes"       notes to play or record on tracks
                          ▼
              Cubase ── MIDI Remote script "NeoCP - Bridge" (JavaScript, MIDI Remote API 1.1–1.3)
```

## Value writes

The MIDI Remote script is `neocp/midi_remote/NeoCP/Bridge/NeoCP_Bridge.js`. A build step generates it from `midi_remote/NeoCP_Bridge.template.js`. The script creates about 2700 virtual controls.

Each control is bound to one Cubase value, for example a volume, a send, an EQ band, a plug-in parameter, or a command. Each control is also bound to a 14-bit NRPN. The server moves the controls as a hardware controller does. This is the most reliable write path in the MIDI Remote API.

When a value changes in Cubase, the script sends the new value back by SysEx. The message includes the display text, for example `-6.02 dB`. The server keeps a live mirror of the project state from these events.

## Requests

Some operations are not values: set the tempo, set the locators, change the mixer bank, and DirectAccess. The server sends these as JSON in SysEx messages. The JSON is split into 7-bit chunks. The script processes the requests in its `mOnIdle` callback, because the API supplies the active mapping there.

The [SysEx format reference](../reference/sysex-format.md) gives the byte layout.

## DirectAccess

DirectAccess is part of MIDI Remote API 1.2 and later (Cubase 13.0.50 and later). It walks the tree of internal Cubase objects. NeoCP uses it to read and write any plug-in parameter. In Cubase 15.0.20 and later (API 1.3), it also loads plug-ins into slots.

## The ports

| Port | Direction | Purpose |
|---|---|---|
| `NeoCP to Cubase` | server to Cubase | Requests and value changes |
| `NeoCP from Cubase` | Cubase to server | Responses and live state |
| `NeoCP Notes` | server to tracks | Notes to play or record |

Cubase removes the ports of a MIDI Remote device from *All MIDI Inputs*. Thus server messages never play as notes on your tracks. Only `NeoCP Notes` reaches the tracks.
