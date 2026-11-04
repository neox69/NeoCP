#!/usr/bin/env python3
"""Assemble release assets into dist/:

* neocp-<v>-py3-none-any.whl / neocp-<v>.tar.gz   (python -m build must have run)
* NeoCP-<v>-macOS.zip    double-click installer + wheel + docs
* NeoCP-<v>-Windows.zip  double-click installer + wheel + docs
* NeoCP_Bridge.midiremote  Cubase MIDI Remote package (Import Script)
* NeoCP_Bridge.js          raw script

Usage: python scripts/make_release.py [--repo owner/name]
"""
import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="neox69/cubasis_mcp")
    a = ap.parse_args()
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "neocp" / "__init__.py").read_text()).group(1)
    wheel = DIST / f"neocp-{version}-py3-none-any.whl"
    if not wheel.exists():
        raise SystemExit(f"{wheel} missing: run `python -m build` first")
    script = ROOT / "neocp" / "midi_remote" / "NeoCP" / "Bridge" / "NeoCP_Bridge.js"
    docs = [ROOT / "README.md", ROOT / "docs" / "CONFIGURAR_CUBASE.md", ROOT / "LICENSE"]

    def render(p: Path) -> bytes:
        return p.read_text(encoding="utf-8").replace("__VERSION__", version).replace("__REPO__", a.repo).encode("utf-8")

    def add_exec(z: zipfile.ZipFile, arc: str, data: bytes) -> None:
        info = zipfile.ZipInfo(arc)
        info.external_attr = 0o100755 << 16  # keep the executable bit for .command
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, data)

    inst = ROOT / "installers"
    for plat, files in (("macOS", ["Install NeoCP (macOS).command"]),
                        ("Windows", ["Install NeoCP (Windows).bat", "install-windows.ps1"])):
        out = DIST / f"NeoCP-{version}-{plat}.zip"
        top = f"NeoCP-{version}"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                add_exec(z, f"{top}/{f}", render(inst / f))
            z.write(wheel, f"{top}/{wheel.name}")
            for d in docs:
                z.write(d, f"{top}/docs/{d.name}" if d.name != "README.md" else f"{top}/README.md")
            z.write(script, f"{top}/MIDI Remote script/NeoCP/Bridge/{script.name}")
        print("wrote", out.relative_to(ROOT))
    mr = DIST / "NeoCP_Bridge.midiremote"
    with zipfile.ZipFile(mr, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(script, f"NeoCP/Bridge/{script.name}")
    print("wrote", mr.relative_to(ROOT))
    (DIST / script.name).write_bytes(script.read_bytes())
    print("wrote", (DIST / script.name).relative_to(ROOT))


if __name__ == "__main__":
    main()
