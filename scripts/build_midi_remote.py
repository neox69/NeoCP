#!/usr/bin/env python3
"""Generate the final MIDI Remote script with the embedded Cubase command catalog.

Usage: python scripts/build_midi_remote.py
Output: neocp/midi_remote/NeoCP/Bridge/NeoCP_Bridge.js
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "midi_remote" / "NeoCP_Bridge.template.js"
COMMANDS = ROOT / "neocp" / "data" / "commands.json"
OUT = ROOT / "neocp" / "midi_remote" / "NeoCP" / "Bridge" / "NeoCP_Bridge.js"


def js_string(s: str) -> str:
    return json.dumps(s, ensure_ascii=True)


def main() -> None:
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "neocp" / "__init__.py").read_text()).group(1)
    cmds = json.loads(COMMANDS.read_text(encoding="utf-8"))
    seen = set()
    lines = []
    for c in cmds:
        k = (c["category"], c["command"])
        if k in seen:
            continue
        seen.add(k)
        lines.append(f'{c["category"]}\t{c["command"]}')
    src = TEMPLATE.read_text(encoding="utf-8")
    src = src.replace("'__COMMANDS__'", js_string("\n".join(lines)))
    src = src.replace("'__VERSION__'", js_string(version))
    src = src.replace("'__EXTRA_COMMANDS__'", '""')  # filled at install time (neocp-install --add-command)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(src, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(lines)} commands, v{version})")


if __name__ == "__main__":
    main()
