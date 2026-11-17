# Validate NeoCP against a real Cubase

Use this procedure to fill one column of the [validation matrix](../reference/validation-matrix.md). The [validation strategy](../explanation/validation-strategy.md) explains why this procedure is necessary.

## Before you start

1. Use a test computer or a test user account. The installer changes the Claude Desktop and Claude Code configuration.
2. Create an empty Cubase project. Do not use a project that you need to keep.
3. Add three tracks: an audio track named `Drums`, an instrument track named `Bass`, and an instrument track named `Keys`.
4. Load an instrument on `Bass` and on `Keys`.
5. Record the Cubase version, the operating system, and the NeoCP version.

## Install and connect

1. Install NeoCP. Follow [Install NeoCP](install.md).
2. Install the MIDI Remote script. Follow [Install the MIDI Remote script](install-midi-remote-script.md).
3. Open the Script Console in Cubase. Make sure that it shows `[NeoCP] activated`.
4. Run the self-test. Follow [Run the self-test](run-self-test.md).
5. Save the self-test output. Each self-test result with status `FAIL` is a finding.

## Test each tool

Do these steps for each row of the matrix, in the order of the [validation strategy](../explanation/validation-strategy.md#order-of-validation).

1. Ask Claude to use the tool with simple arguments. Name the tool in the request.
2. Read the JSON result.
3. Look at Cubase. Make sure that the change is visible in the project, the mixer, or the transport.
4. Undo the change if the tool changed the project.
5. Write the result in the matrix cell: `pass`, `partial`, `fail` or `n/a`.
6. For `partial` and `fail`, write the cause in the Notes column. Attach the JSON result and the Script Console output.

## Record a failure

1. Copy the request to Claude, the JSON result, and the Script Console output.
2. Set `NEOCP_LOG=DEBUG` and repeat the request. Copy the server log.
3. Create an issue with this evidence, or add it to a text file next to the matrix.

## When you finish

1. Count the `pass` cells in your column.
2. Commit the matrix with the environment in the commit message.
