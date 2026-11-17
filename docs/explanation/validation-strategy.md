# Validation strategy

This page explains why the automated tests do not prove that NeoCP works in Cubase. It also defines the evidence levels that the [validation matrix](../reference/validation-matrix.md) records.

## What the automated tests prove

The test suite runs the real MIDI Remote script under Node.js. A file named `tests/mock_cubase/harness.js` simulates the Cubase MIDI Remote API. The tests send SysEx and NRPN messages from Python to the script and read the replies.

This proves three things:

* The Python server and the script agree on the message format.
* Each of the 87 tools returns readable JSON and does not raise an exception through the MCP layer.
* The offline code (music generation and audio analysis) gives the expected results for synthetic test signals.

## What the automated tests do not prove

The simulated host object accepts any property name and any method call. It returns a new object or itself for every access. Thus a call to a method that Cubase does not have still passes in the tests.

The tests also do not check:

* that Cubase accepts about 2700 custom value variables in one script;
* that Cubase delivers NRPN and SysEx messages at the speed that the server sends them;
* that the 1454 command names in `neocp/data/commands.json` match the names in Cubase;
* that the display strings from Cubase (for example, `-6.02 dB`) parse correctly;
* timing-dependent behavior: recording notes, writing automation, and capturing audio;
* the installers on a clean macOS or Windows computer.

## A static check that was done

All method and property names that the script calls were compared with the API definition file that Cubase installs. On macOS, this file is `~/Documents/Steinberg/Cubase/MIDI Remote/Driver Scripts/.api/v1/midiremote_api_v1.d.ts`. All names exist in that file. This check proves that the names are correct. It does not prove that the calls behave as the script expects.

## Evidence levels

The validation matrix uses these values. Each value is a word, so the matrix does not depend on color.

| Value | Meaning |
|---|---|
| `not run` | Nobody has tried the tool in this environment. |
| `pass` | The tool did what the [validation procedure](../how-to/validate-against-cubase.md) expects, and the Cubase project showed the change. |
| `partial` | The tool worked with a limitation. The Notes column describes the limitation. |
| `fail` | The tool did not do what the procedure expects. The Notes column links to the evidence. |
| `n/a` | The tool cannot work in this environment by design (for example, Control Room in Cubase Artist). |

A `pass` in the mock tests is not a value in the matrix. Every tool already passes the mock tests.

## Order of validation

Validate in this order, because each step depends on the step before it:

1. Installation and connection (`connection_status`, `run_selftest`).
2. Reading the project (session, tracks, transport state).
3. Writing values (tempo, volume, pan, mute).
4. Commands.
5. DirectAccess and plug-ins.
6. Notes and automation. These depend on timing.
7. Listening and closed-loop mixing. These need a loopback audio device.

The offline tools (music generation and audio analysis) do not need Cubase. Their check is musical correctness, which is a separate question. The matrix lists them so that the count stays at 87.

## How to measure progress

Count the `pass` cells per column. A column with all Cubase rows at `pass` means that NeoCP works on that platform and Cubase version. Release a claim of support for a platform only after its column is complete.
