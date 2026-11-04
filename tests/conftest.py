import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cubase_mcp.link import Link  # noqa: E402

SCRIPT = ROOT / "cubase_mcp" / "midi_remote" / "CubaseMCP" / "Bridge" / "CubaseMCP_Bridge.js"
HARNESS = ROOT / "tests" / "mock_cubase" / "harness.js"


class SubprocessLink(Link):
    """Talks to the Node mock that runs the real MIDI Remote script."""

    def __init__(self):
        super().__init__()
        self.proc = subprocess.Popen(["node", str(HARNESS), str(SCRIPT)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.notes = []
        self.info_lines = []
        self._info_evt = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            if line.startswith("sx "):
                self._deliver(list(bytes.fromhex(line[3:].strip())))
            elif line.startswith("info "):
                self.info_lines.append(line[5:])
                self._info_evt.set()

    def _w(self, s):
        with self._lock:
            self.proc.stdin.write(s + "\n")
            self.proc.stdin.flush()

    def send_sysex(self, data):
        self._w("sx " + bytes(data).hex())

    def send_cc(self, channel, cc, value):
        self._w(f"cc {channel} {cc} {value}")

    def send_note(self, kind, channel, note, velocity):
        self.notes.append((kind, channel, note, velocity))

    def send_notes_raw(self, kind, channel, a, b=0):
        pass

    def query(self):
        import json
        self._info_evt.clear()
        self._w("q")
        self._info_evt.wait(3)
        return json.loads(self.info_lines[-1])

    def close(self):
        self.proc.kill()


@pytest.fixture
def mock_bridge():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_midi_remote.py")], check=True, capture_output=True)
    from cubase_mcp.bridge import CubaseBridge
    from cubase_mcp import server
    link = SubprocessLink()
    b = CubaseBridge(link, write_mode="nrpn")
    b.connect(timeout=10)
    server.set_bridge(b)
    yield b
    server.set_bridge(None)
    link.close()
