import json
import time

from neocp import automation as AU
from neocp import server as S


def test_curves():
    f = AU.shape_curve("ramp", 8, -20, 0, unit="db")
    assert f(0) == -20 and f(8) == 0 and abs(f(4) + 10) < 1e-9
    g = AU.shape_curve("lfo", 4, center=0, depth=1, rate_beats=2, unit="pan")
    assert abs(g(0.5) - 1) < 1e-9 and abs(g(1.5) + 1) < 1e-9
    d = AU.shape_curve("duck", 4, rate_beats=1, unit="db")
    assert d(0) == -12 and d(0.9) == 0
    st = AU.shape_curve("steps", 4, steps=[1, 0, 1, 0], rate_beats=0.5)
    assert [st(x) for x in (0, 0.5, 1.0, 1.5)] == [1, 0, 1, 0]
    p = AU.points_curve([{"bar": 5, "value": -12}, {"bar": 7, "value": 0}], start_bar=5)
    assert p(0) == -12 and p(4) == -6 and p(100) == 0


def test_dry_run():
    r = json.loads(S.write_automation("Bass", "volume", 9, 4, shape="fade_in", dry_run=True))
    assert r["preview"][0]["value"] == -60 and r["preview"][-1]["value"] == 0


def test_write_automation_e2e(mock_bridge):
    r = json.loads(S.write_automation("Bass", "volume", start_bar=1, bars=0.5, shape="ramp", start=20, end=80,
                                      preroll_beats=0))
    assert r["values_sent"] > 3, r
    assert abs(mock_bridge.value("ch1.volume") - 0.8) < 0.02  # mock display = value*100
    assert mock_bridge.value("ch1.aread") == 1.0 and mock_bridge.value("ch1.awrite") == 0.0
    r = json.loads(S.write_automation("Keys", "plugin:Compressor/Threshold", bars=0.25, shape="lfo",
                                      preroll_beats=0))
    assert r["target"] == "Compressor / Threshold" and r["values_sent"] > 2
    time.sleep(0.2)
    info = mock_bridge.link.query()
    assert "Automation/Automation Mode - Auto-Latch" in info["executed"]
    assert "Automation/Punch Out of Latch Automation" in info["executed"]
    assert info["daWrites"] > 2


def test_instruments_and_effects(mock_bridge):
    r = json.loads(S.list_plugin_slots("Keys"))
    kinds = {s["kind"] for s in r["slots"]}
    assert kinds == {"instrument", "effect"}
    r = json.loads(S.search_plugins("synth", kind="instrument"))
    assert {i["name"] for i in r["results"]} == {"HALion Sonic", "Retrologue"}
    r = json.loads(S.load_instrument("retrologue", track="Keys"))
    assert r["loaded"] == "Retrologue" and r["ok"]
    r = json.loads(S.load_effect("reverb", track="Keys"))
    assert r["loaded"] == "REVerence"
    time.sleep(0.1)
    ex = mock_bridge.link.query()["executed"]
    assert "load:4:RETRO2" in ex and "load:2:ABC123" in ex
