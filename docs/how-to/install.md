# Install NeoCP

## Requirements

* Cubase Pro, Artist or Elements, or Nuendo, version 12 or later. The [compatibility reference](../reference/compatibility.md) lists the features per version.
* Claude Desktop or Claude Code.
* For a manual installation: Python 3.10 to 3.12. These versions have prebuilt packages of `python-rtmidi`.

## Use the installer (recommended)

The installer needs no administrator rights and no Python. It does these steps:

* It installs [uv](https://docs.astral.sh/uv/) and a private Python 3.12.
* It installs NeoCP.
* It copies the MIDI Remote script into Cubase and Nuendo.
* It adds NeoCP to the Claude Desktop and Claude Code configuration.
* On Windows, it offers to install loopMIDI.
* If BlackHole, VoiceMeeter or VB-Cable is installed, it configures listening.

On macOS:

1. Download `NeoCP-x.y.z-macOS.zip` from [Releases](https://github.com/neox69/NeoCP/releases/latest).
2. Unzip it.
3. Double-click **Install NeoCP (macOS).command**. If macOS blocks it, right-click it and select **Open**.
4. Restart Claude Desktop and Cubase.

On Windows:

1. Download `NeoCP-x.y.z-Windows.zip` from [Releases](https://github.com/neox69/NeoCP/releases/latest).
2. Unzip it.
3. Double-click **Install NeoCP (Windows).bat**.
4. Create the three MIDI ports in loopMIDI. Follow [Set up the MIDI ports](set-up-midi-ports.md).
5. Restart Claude Desktop and Cubase.

## Install manually

1. Install the package with uv:

   ```bash
   uv tool install --python 3.12 neocp-x.y.z-py3-none-any.whl
   ```

   Or install it with pip:

   ```bash
   pip install git+https://github.com/neox69/NeoCP.git
   ```

2. Install the MIDI Remote script and the Claude configuration:

   ```bash
   neocp-install
   ```

3. Restart Claude Desktop and Cubase.

The package installs two commands. `neocp` is the MCP server. `neocp-install` installs the script, configures Claude, and diagnoses the installation.

If `python-rtmidi` fails to install with pip, use Python 3.10 to 3.12, or use uv. On Linux, install `libasound2-dev` and `libjack-dev` first.

## Install from the source code on macOS

You need [just](https://just.systems/) and [uv](https://docs.astral.sh/uv/).

1. Clone the repository.
2. Run:

   ```bash
   just install
   ```

The recipe checks the tools, installs NeoCP with Python 3.12, and runs `neocp-install`. To remove NeoCP, run `just uninstall`.

## Check the installation

```bash
neocp-install --check
```

## Uninstall

```bash
neocp-install --uninstall
```
