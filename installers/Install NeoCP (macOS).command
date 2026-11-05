#!/bin/bash
# NeoCP one-click installer for macOS — double-click this file in Finder.
# (If macOS blocks it: right-click > Open, or System Settings > Privacy & Security > Open Anyway.)
set -e
VERSION="__VERSION__"
REPO="__REPO__"
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "=============================================="
echo "  NeoCP $VERSION — AI co-producer for Cubase"
echo "=============================================="

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "→ Installing uv (Python manager, no admin rights needed)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

WHEEL="$(ls "$HERE"/neocp-*.whl 2>/dev/null | head -n1 || true)"
if [ -z "$WHEEL" ]; then
  WHEEL="https://github.com/$REPO/releases/download/v$VERSION/neocp-$VERSION-py3-none-any.whl"
fi
echo "→ Installing NeoCP ($WHEEL)…"
uv tool install --python 3.12 --force "$WHEEL"
TOOL_BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"

echo "→ Installing the Cubase MIDI Remote script and configuring Claude…"
LISTEN=""
if system_profiler SPAudioDataType 2>/dev/null | grep -qi blackhole; then LISTEN="--listen-device BlackHole"; fi
"$TOOL_BIN/neocp-install" $LISTEN

if [ -z "$LISTEN" ]; then
  echo
  echo "Optional — to let Claude LISTEN to Cubase install BlackHole 2ch:"
  echo "   https://existential.audio/blackhole/   (or: brew install blackhole-2ch)"
  echo "   then follow docs/CONFIGURAR_CUBASE.md section 6."
fi
echo
echo "Done! Restart Claude Desktop and (re)start Cubase. Press Enter to close."
read -r _
