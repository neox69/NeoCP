# Compatibility

The features depend on the MIDI Remote API version in each Cubase or Nuendo version.

| Cubase or Nuendo version | API | Features |
|---|---|---|
| 12.0 | 1.0 | Mixer, transport, commands. There are no idle callbacks, so NeoCP uses a fallback mode that is less smooth. |
| 13.0 | 1.1 | All basic features, with smooth operation. Not DirectAccess. |
| 13.0.50 or later | 1.2 | DirectAccess: the object tree and plug-in parameters by name. |
| 15.0.20 or later | 1.3 | Plug-in loading, parameter types, and `canPerform` on commands. |

Control Room exists only in Cubase Pro and Nuendo.

These claims are not verified in a real Cubase. The [validation matrix](validation-matrix.md) records what is verified.

## Cubase 16

The project states that NeoCP works with Cubase 16. Cubase 16 uses MIDI Remote API 1.3, so all Cubase 15 features stay available. In addition:

* Cubase 16 Pro has 16 sends per channel. The script reads the number of sends from the host and mirrors all of them. `set_send` accepts slots 0 to 15.
* New Cubase 16 commands are not in the bundled catalog. [Add a Cubase command](../how-to/add-cubase-command.md) tells you how to add them.

## Python

Python 3.10 or later. For pip installations, use Python 3.10 to 3.12, because these versions have prebuilt `python-rtmidi` packages. The installer uses Python 3.12.

## Operating systems

* macOS: the server creates the MIDI ports automatically.
* Windows: create the MIDI ports with loopMIDI.
* Linux: for development and tests only. Cubase does not run on Linux.
