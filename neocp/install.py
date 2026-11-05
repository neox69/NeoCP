"""NeoCP installer / setup helper.

    neocp-install                 # install the Cubase script + configure Claude Desktop/Code
    neocp-install --app Nuendo    # only Nuendo
    neocp-install --no-claude     # only copy the MIDI Remote script
    neocp-install --check         # diagnose ports, audio devices and installation
    neocp-install --uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__

SCRIPT = Path(__file__).parent / "midi_remote" / "NeoCP" / "Bridge" / "NeoCP_Bridge.js"
APPS = ("Cubase", "Nuendo")
PORTS = ("NeoCP to Cubase", "NeoCP from Cubase", "NeoCP Notes")


def documents_dir() -> Path:
    if sys.platform == "win32":
        try:  # real "Documents" folder (may be redirected to OneDrive)
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def script_dir(app: str = "Cubase") -> Path:
    return documents_dir() / "Steinberg" / app / "MIDI Remote" / "Driver Scripts" / "Local" / "NeoCP" / "Bridge"


def default_destination(app: str = "Cubase") -> Path:  # backwards compatible name
    return script_dir(app)


EXTRA_FILE = Path.home() / ".neocp" / "extra_commands.json"


def extra_commands() -> List[List[str]]:
    """User-added key commands (e.g. new Cubase 16 commands) bound in addition to the catalog."""
    try:
        return [list(x) for x in json.loads(EXTRA_FILE.read_text(encoding="utf-8"))]
    except Exception:
        return []


def add_extra_command(category: str, command: str) -> List[List[str]]:
    cur = extra_commands()
    if [category, command] not in cur:
        cur.append([category, command])
    EXTRA_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTRA_FILE.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf-8")
    return cur


def render_script() -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    extra = "\n".join(f"{c}\t{m}" for c, m in extra_commands())
    return src.replace('var EXTRA_COMMANDS = ""', "var EXTRA_COMMANDS = " + json.dumps(extra, ensure_ascii=True), 1)


def install_script(app: str = "Cubase", dest: Optional[Path] = None) -> Path:
    target_dir = dest or script_dir(app)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / SCRIPT.name
    target.write_text(render_script(), encoding="utf-8")
    return target


def reinstall_everywhere() -> List[str]:
    """Re-install the script for every host that already has it (or the detected ones)."""
    done = []
    for app in APPS:
        if (script_dir(app) / SCRIPT.name).exists() or app in detected_apps():
            done.append(str(install_script(app)))
    return done


install = install_script


def detected_apps() -> List[str]:
    base = documents_dir() / "Steinberg"
    found = [a for a in APPS if (base / a).exists()]
    return found or ["Cubase"]


def server_command() -> List[str]:
    """Absolute command for MCP clients (they often do not inherit the user's PATH)."""
    exe = "neocp.exe" if sys.platform == "win32" else "neocp"
    cand = Path(sys.argv[0]).resolve().parent / exe
    if cand.exists():
        return [str(cand)]
    found = shutil.which("neocp")
    if found:
        return [found]
    return [sys.executable, "-m", "neocp.server"]


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def configure_claude_desktop(env: Optional[dict] = None) -> Path:
    path = claude_desktop_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            backup = path.with_suffix(".json.bak")
            shutil.copy2(path, backup)
            print(f"  ! existing config was not valid JSON, backed up to {backup}")
    cmd = server_command()
    entry = {"command": cmd[0], "args": cmd[1:]}
    if env:
        entry["env"] = env
    data.setdefault("mcpServers", {})["neocp"] = entry
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def configure_claude_code() -> Optional[str]:
    claude = shutil.which("claude")
    if not claude:
        return None
    cmd = server_command()
    subprocess.run([claude, "mcp", "remove", "neocp", "-s", "user"], capture_output=True)
    r = subprocess.run([claude, "mcp", "add", "-s", "user", "neocp", "--", *cmd], capture_output=True, text=True)
    return "ok" if r.returncode == 0 else (r.stderr or r.stdout).strip()


def check() -> int:
    ok = True
    print(f"NeoCP {__version__} diagnostics\n")
    for app in APPS:
        f = script_dir(app) / SCRIPT.name
        print(f"[{'x' if f.exists() else ' '}] {app} MIDI Remote script: {f}")
    try:
        import mido
        outs, ins = mido.get_output_names(), mido.get_input_names()
        print("\nMIDI ports:")
        for p in PORTS:
            present = any(p.lower() in n.lower() for n in outs + ins)
            note = "" if present else ("  <- create it in loopMIDI" if sys.platform == "win32" else "  (created automatically while NeoCP runs)")
            print(f"[{'x' if present else ' '}] {p}{note}")
            if not present and sys.platform == "win32":
                ok = False
    except Exception as e:
        ok = False
        print(f"\n[ ] MIDI backend not available: {e}")
    try:
        from . import capture
        devs = capture.list_input_devices()
        print("\nAudio inputs (for listening):")
        for d in devs:
            print(f"  {'*' if d['looks_like_loopback'] else ' '} [{d['id']}] {d['name']} ({d['api']})")
        if not any(d["looks_like_loopback"] for d in devs):
            print("  no loopback device found: see docs/CONFIGURAR_CUBASE.md section 6 (BlackHole / VoiceMeeter / VB-Cable)")
    except Exception as e:
        print(f"\nAudio capture not available: {e}")
    cfg = claude_desktop_config()
    has = False
    if cfg.exists():
        try:
            has = "neocp" in json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {})
        except Exception:
            pass
    print(f"\n[{'x' if has else ' '}] Claude Desktop configured: {cfg}")
    print(f"    server command: {' '.join(server_command())}")
    return 0 if ok else 1


def uninstall() -> int:
    for app in APPS:
        d = script_dir(app)
        if d.exists():
            shutil.rmtree(d)
            print(f"removed {d}")
        try:
            d.parent.rmdir()  # empty 'NeoCP' vendor folder
        except OSError:
            pass
    cfg = claude_desktop_config()
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if data.get("mcpServers", {}).pop("neocp", None) is not None:
                cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
                print(f"removed neocp from {cfg}")
        except Exception:
            pass
    if shutil.which("claude"):
        subprocess.run(["claude", "mcp", "remove", "neocp", "-s", "user"], capture_output=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="neocp-install", description="Install NeoCP (AI co-producer for Cubase)")
    ap.add_argument("--app", choices=APPS, help="only this host (default: every Cubase/Nuendo found)")
    ap.add_argument("--dest", type=Path, help="custom destination folder for the MIDI Remote script")
    ap.add_argument("--no-claude", action="store_true", help="do not touch Claude Desktop / Claude Code config")
    ap.add_argument("--listen-device", help="audio input used for listening (e.g. 'BlackHole 2ch')")
    ap.add_argument("--check", action="store_true", help="diagnose the installation")
    ap.add_argument("--add-command", metavar="CATEGORY/COMMAND",
                    help="bind an extra Cubase key command (e.g. a new Cubase 16 command) and reinstall the script")
    ap.add_argument("--selftest", action="store_true", help="test every feature against the running Cubase")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--print", action="store_true", help="only print the bundled script path")
    a = ap.parse_args(argv)
    if a.print:
        print(SCRIPT)
        return 0
    if a.check:
        return check()
    if a.add_command:
        cat, sep, cmd = a.add_command.partition("/")
        if not sep:
            ap.error("--add-command needs CATEGORY/COMMAND, e.g. 'Audio/Stem Separation'")
        add_extra_command(cat.strip(), cmd.strip())
        for t in reinstall_everywhere():
            print(f"✓ reinstalled {t}")
        print("Now press 'Reload Scripts' in Cubase's MIDI Remote tab.")
        return 0
    if a.uninstall:
        return uninstall()
    if a.selftest:
        from .server import run_selftest
        rep = json.loads(run_selftest())
        print(f"NeoCP selftest: {rep['summary']}\n")
        for r in rep["results"]:
            mark = {"ok": "✓", "skip": "-", "FAIL": "✗"}[r["status"]]
            print(f" {mark} {r['test']}: {r['detail']}" + (f"\n     fix: {r['fix']}" if r.get("fix") else ""))
        return 0 if rep["summary"] == "all good" else 1
    print(f"NeoCP {__version__} — AI co-producer for Cubase\n")
    apps = [a.app] if a.app else detected_apps()
    for app in apps:
        t = install_script(app, a.dest)
        print(f"✓ MIDI Remote script installed for {app}: {t}")
    if not a.no_claude:
        env = {"NEOCP_LISTEN_DEVICE": a.listen_device} if a.listen_device else None
        p = configure_claude_desktop(env)
        print(f"✓ Claude Desktop configured: {p}  (restart Claude Desktop)")
        r = configure_claude_code()
        if r == "ok":
            print("✓ Claude Code configured (claude mcp add neocp)")
        elif r:
            print(f"  Claude Code not configured: {r}")
    print("\nNext steps:")
    if sys.platform == "win32":
        print("  1. Install loopMIDI and create 3 ports: " + ", ".join(f"'{p}'" for p in PORTS))
    else:
        print("  1. MIDI ports are created automatically while NeoCP runs (nothing to do).")
    print("  2. In Cubase: lower zone > MIDI Remote > 'Reload Scripts' (or restart Cubase).")
    print("     The 'NeoCP - Bridge' controller appears automatically.")
    print("  3. Open Claude and ask: \"what's in my Cubase project?\"")
    print("  To let Claude LISTEN to Cubase see docs/CONFIGURAR_CUBASE.md (section 6). Diagnose: neocp-install --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
