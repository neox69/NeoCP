from neocp import protocol as P


def test_roundtrip_chunked():
    obj = {"op": "x", "text": "ñandú ♫ " * 200, "n": [1, 2.5, None, True]}
    frames = P.encode(P.TYPE_REQUEST, 1234, obj)
    assert len(frames) > 1
    r = P.Reassembler()
    out = None
    for f in frames:
        assert f[0] == 0xF0 and f[-1] == 0xF7
        assert all(b < 0x80 for b in f[1:-1])
        out = r.feed(P.parse_frame(f)) or out
    assert out == (P.TYPE_REQUEST, 1234, obj)


def test_nrpn():
    ch, n = P.slot_address(20000)
    assert (ch, n) == (1, 20000 - 16384)
    msgs = P.nrpn_messages(ch, n, 1.0)
    assert msgs[0] == (1, 99, n >> 7) and msgs[1] == (1, 98, n & 0x7F)
    assert msgs[2][2] == 127 and msgs[3][2] == 127
