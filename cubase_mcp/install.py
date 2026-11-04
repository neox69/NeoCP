"""Install the MIDI Remote bridge script into Cubase's user script folder.

    cubase-mcp-install [--app Cubase|Nuendo] [--dest PATH] [--print]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "midi_remote" / "CubaseMCP" / "Bridge" / "CubaseMCP_Bridge.js"


def default_destination(app: str = "Cubase") -> Path:
    docs = Path.home() / "Documents"
    return docs / "Steinberg" / app / "MIDI Remote" / "Driver Scripts" / "Local" / "CubaseMCP" / "Bridge"


def install(app: str = "Cubase", dest: Path | None = None) -> Path:
    target_dir = dest or default_destination(app)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / SCRIPT.name
    shutil.copy2(SCRIPT, target)
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", default="Cubase", help="Cubase (default) or Nuendo")
    ap.add_argument("--dest", type=Path, help="custom destination folder")
    ap.add_argument("--print", action="store_true", help="only print the script path")
    a = ap.parse_args(argv)
    if a.print:
        print(SCRIPT)
        return 0
    t = install(a.app, a.dest)
    print(f"Installed MIDI Remote script -> {t}")
    print("Next: in Cubase open the MIDI Remote tab (lower zone) and click 'Reload Scripts', or restart Cubase.")
    if sys.platform == "win32":
        print("Windows: create 3 loopMIDI ports named 'CubaseMCP to Cubase', 'CubaseMCP from Cubase', 'CubaseMCP Notes'.")
    else:
        print("macOS/Linux: ports are created automatically while the MCP server runs "
              "(or create IAC buses with the same names for always-on ports).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
