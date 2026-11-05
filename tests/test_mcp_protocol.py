"""Call tools THROUGH the MCP server (as Claude does), not as plain Python functions.

Regression test for structured-output validation rejecting the JSON-string results
("Error executing tool ...") on mcp >= 1.10.
"""
import asyncio
import json

import numpy as np

from neocp import server as S


def _call(name, args):
    res = asyncio.run(S.mcp.call_tool(name, args))
    # mcp 2.x: CallToolResult; mcp 1.x: (content, structured) tuple or list of content
    if hasattr(res, "content"):
        assert not getattr(res, "is_error", False) and not getattr(res, "isError", False), res
        content = res.content
    elif isinstance(res, tuple):
        content = res[0]
    else:
        content = res
    return content


def _text(content):
    for c in content:
        if getattr(c, "type", None) == "text":
            return c.text
    raise AssertionError(f"no text content in {content}")


def test_offline_tool_through_mcp():
    out = json.loads(_text(_call("music_theory", {"key": "A minor"})))
    assert out["notes"][0].startswith("A")


def test_all_tools_have_no_output_schema():
    tools = asyncio.run(S.mcp.list_tools())
    bad = [t.name for t in tools if getattr(t, "outputSchema", None) or getattr(t, "output_schema", None)]
    assert not bad, bad


def test_cubase_tool_through_mcp(mock_bridge):
    out = json.loads(_text(_call("get_session_info", {})))
    assert [t["name"] for t in out["tracks"]] == ["Drums", "Bass", "Keys"]
    out = json.loads(_text(_call("set_track_volume", {"track": 1, "normalized": 0.3})))
    assert abs(out["value"] - 0.3) < 0.01


def test_image_tool_through_mcp(tmp_path):
    from neocp import audio as A
    p = tmp_path / "x.wav"
    t = np.arange(48000) / 48000
    A.save_wav(str(p), np.sin(2 * np.pi * 440 * t) * 0.3, 48000)
    content = _call("show_spectrogram", {"path": str(p)})
    types = [getattr(c, "type", None) for c in content]
    assert "image" in types and "text" in types
