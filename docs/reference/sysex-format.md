# SysEx format

This page describes the messages between the server and the MIDI Remote script. The source of truth is the header of `midi_remote/NeoCP_Bridge.template.js` and the file `neocp/protocol.py`.

## Message layout

All bytes are 7-bit clean.

```
F0 7D 43 4D <type> <id_hi> <id_lo> <seq_hi> <seq_lo> <tot_hi> <tot_lo> <ASCII JSON ...> F7
```

| Field | Meaning |
|---|---|
| `F0 ... F7` | SysEx start and end |
| `7D` | The non-commercial manufacturer ID |
| `43 4D` | The NeoCP signature, `CM` in ASCII |
| `type` | `01` request (server to Cubase), `02` response, `03` event |
| `id` | The request ID, in two 7-bit bytes |
| `seq`, `tot` | The chunk number and the total number of chunks, in two 7-bit bytes each |
| payload | A part of the JSON text. All non-ASCII characters are escaped as `\uXXXX`. |

Each chunk holds at most 240 payload bytes. A message can have at most 16383 chunks.

## NRPN writes

Each control has a slot index. The script binds the slot to a 14-bit NRPN:

* MIDI channel = `index >> 14`
* NRPN number = `index & 0x3FFF`

Set `NEOCP_WRITE_MODE=sysex` to send value writes as SysEx requests instead. The script then calls `setProcessValue`.
