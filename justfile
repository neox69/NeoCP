# NeoCP tasks for macOS. Run `just` to list the recipes.
# shortcut: macOS only; the recipes call no Windows tools. Add Windows recipes when someone can test them.

set shell := ["bash", "-euo", "pipefail", "-c"]

# python-rtmidi has prebuilt wheels for these versions; the installer also uses 3.12
python := "3.12"
min_python := ">=3.10"
min_node := "20"

# List the recipes
default:
    @just --list

# Check the operating system and the required tools
check:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    ok()   { echo "ok    $1"; }
    bad()  { echo "FAIL  $1"; fail=1; }
    warn() { echo "warn  $1"; }
    [[ "$(uname -s)" == "Darwin" ]] && ok "macOS $(sw_vers -productVersion)" || bad "this justfile supports macOS only"
    if command -v uv >/dev/null; then ok "$(uv --version)"; else bad "uv not found: see https://docs.astral.sh/uv/"; fi
    if command -v uv >/dev/null; then
        if p=$(uv python find '{{min_python}}' 2>/dev/null); then ok "Python $("$p" -c 'import platform; print(platform.python_version())') ($p)"
        else bad "no Python {{min_python}}: run 'uv python install {{python}}'"; fi
    fi
    if command -v node >/dev/null; then
        v=$(node --version); major=${v#v}; major=${major%%.*}
        if (( major >= {{min_node}} )); then ok "Node.js $v (tests only)"; else bad "Node.js $v is older than {{min_node}} (tests only)"; fi
    else bad "Node.js not found; the tests need it"; fi
    if compgen -G "/Applications/Cubase*.app" >/dev/null || compgen -G "/Applications/Nuendo*.app" >/dev/null; then
        ok "Cubase or Nuendo: $(cd /Applications && command ls -d Cubase*.app Nuendo*.app 2>/dev/null | tr '\n' ' ')"
    else warn "no Cubase or Nuendo in /Applications (needed only to use NeoCP, not to test it)"; fi
    exit $fail

# Create .venv (Python 3.12, downloaded by uv if missing) and install NeoCP with the development dependencies
sync: check
    uv venv --allow-existing --python {{python}} .venv
    uv pip install --python .venv -e ".[dev]"

# Run the tests (arguments go to pytest, for example: just test -k e2e)
test *args: sync
    .venv/bin/pytest {{args}}

# Regenerate the MIDI Remote script and fail if it differs from the committed file, as CI does
check-script: sync
    .venv/bin/python scripts/build_midi_remote.py
    git diff --exit-code -- neocp/midi_remote

# uv tool installs commands here; this folder may not be on PATH
tool_bin := '"$(uv tool dir --bin)"'

# Install NeoCP for your user: the commands, the Cubase script, and the Claude configuration
install: check
    uv tool install --force --python {{python}} .
    {{tool_bin}}/neocp-install

# Diagnose the installation
doctor:
    {{tool_bin}}/neocp-install --check

# Run the self-test against the running Cubase
selftest:
    {{tool_bin}}/neocp-install --selftest

# Remove the Cubase script, the Claude configuration, and the commands
uninstall:
    {{tool_bin}}/neocp-install --uninstall
    uv tool uninstall neocp
