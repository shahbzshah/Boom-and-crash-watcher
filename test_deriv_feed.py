"""Offline tests for deriv_feed.py — endpoint/symbol negotiation.

Run:  python test_deriv_feed.py
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")

import deriv_feed


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_endpoints_trims_and_dedupes():
    raw = ("wss://ws.derivws.com/websockets/v3, "
           "wss://ws.binaryws.com/websockets/v3 , ,"
           "wss://ws.derivws.com/websockets/v3")
    got = deriv_feed.parse_endpoints(raw)
    assert got == ["wss://ws.derivws.com/websockets/v3",
                   "wss://ws.binaryws.com/websockets/v3"], got


def test_parse_endpoints_empty():
    assert deriv_feed.parse_endpoints("") == []
    assert deriv_feed.parse_endpoints(None) == []
    assert deriv_feed.parse_endpoints("  ,  ") == []


def test_parse_symbols_order_and_dedupes():
    got = deriv_feed.parse_symbols("R_1000, R_500 ,R_1000,R_300")
    assert got == ["R_1000", "R_500", "R_300"], got


def test_build_ws_url_appends_path_and_app_id():
    url = deriv_feed.build_ws_url("wss://ws.derivws.com", 1089)
    assert url == "wss://ws.derivws.com/websockets/v3?app_id=1089", url


def test_build_ws_url_keeps_existing_path():
    url = deriv_feed.build_ws_url("wss://red.derivws.com/websockets/v3", 42)
    assert url == "wss://red.derivws.com/websockets/v3?app_id=42", url


def test_build_ws_url_keeps_custom_path():
    url = deriv_feed.build_ws_url(
        "wss://api.derivws.com/trading/v1/options/ws/public", 1089)
    assert url == ("wss://api.derivws.com/trading/v1/options/ws/public"
                   "?app_id=1089"), url


def test_build_ws_url_appends_app_id_to_existing_query():
    url = deriv_feed.build_ws_url(
        "wss://api.derivws.com/trading/v1/options/ws/public?l=EN", 1089)
    assert url.endswith("?l=EN&app_id=1089"), url


def test_build_ws_url_upgrades_scheme_and_trims_slash():
    url = deriv_feed.build_ws_url("https://ws.derivws.com/", 1089)
    assert url.startswith("wss://"), url
    assert not url.endswith("/"), url


def test_build_ws_url_rejects_empty():
    try:
        deriv_feed.build_ws_url("  ")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_symbol_candidates_primary_first():
    got = deriv_feed.symbol_candidates("R_1000", "R_500,R_1000,R_300")
    assert got == ["R_1000", "R_500", "R_300"], got


def test_symbol_candidates_crash_new_and_legacy_code():
    got = deriv_feed.symbol_candidates("CRASH1000", "R_1000")
    assert got == ["CRASH1000", "R_1000"], got


# ---------------------------------------------------------------------------
# Async helpers (fake websocket)
# ---------------------------------------------------------------------------


class FakeWS:
    """Fake websocket that answers each request from a handler.

    The handler receives the decoded request and returns either a single
    response dict, a list of response dicts (to simulate stray frames),
    or None for "no answer".
    """

    def __init__(self, handler):
        self.handler = handler
        self.sent = []
        self._queue = []

    async def send(self, raw):
        req = json.loads(raw)
        self.sent.append(req)
        out = self.handler(req)
        if out is None:
            return
        if isinstance(out, dict):
            out = [out]
        for resp in out:
            resp = dict(resp)
            resp.setdefault("req_id", req.get("req_id"))
            self._queue.append(resp)

    async def recv(self):
        if not self._queue:
            raise asyncio.TimeoutError
        return json.dumps(self._queue.pop(0))


def test_probe_symbols_splits_streamable_and_rejected():
    def handler(req):
        if req.get("forget") is not None:
            return {"msg_type": "forget"}
        if req["ticks"] == "R_900":
            return {"error": {"code": "InvalidSymbol", "message": "no"}}
        return {"msg_type": "tick", "tick": {"symbol": req["ticks"]}}

    ws = FakeWS(handler)
    streamable, rejected = asyncio.run(
        deriv_feed.probe_symbols(ws, ["R_1000", "R_900", "R_500"]))
    assert streamable == ["R_1000", "R_500"], streamable
    assert rejected == ["R_900"], rejected


def test_probe_symbols_forgets_by_subscription_id():
    def handler(req):
        if req.get("forget") is not None:
            return {"msg_type": "forget"}
        return {"msg_type": "tick", "subscription": {"id": "sub-uuid-1"},
                "tick": {"symbol": "CRASH1000"}}

    ws = FakeWS(handler)
    asyncio.run(deriv_feed.probe_symbols(ws, ["CRASH1000"]))
    assert ws.sent[0]["ticks"] == "CRASH1000"
    assert ws.sent[0]["subscribe"] == 1
    assert ws.sent[1]["forget"] == "sub-uuid-1"


def test_probe_symbols_ignores_frames_of_other_requests():
    """A tick from another subscription must not desync the probe."""
    def handler(req):
        if req.get("forget") is not None:
            return {"msg_type": "forget"}
        return [
            {"msg_type": "tick", "req_id": 99, "tick": {"symbol": "CRASH1000"}},
            {"msg_type": "tick", "tick": {"symbol": "CRASH1000"}},
        ]

    ws = FakeWS(handler)
    streamable, rejected = asyncio.run(
        deriv_feed.probe_symbols(ws, ["CRASH1000"]))
    assert streamable == ["CRASH1000"], streamable
    assert rejected == []


def test_probe_symbols_timeout_is_rejection():
    ws = FakeWS(lambda req: None)
    streamable, rejected = asyncio.run(
        deriv_feed.probe_symbols(ws, ["R_1000"], timeout=0.01))
    assert streamable == []
    assert rejected == ["R_1000"]


def test_fetch_candles_returns_payload():
    ws = FakeWS(lambda req: {"msg_type": "candles", "candles": [{"epoch": 1}]})
    out = asyncio.run(deriv_feed.fetch_candles(ws, "CRASH1000", 10, 1))
    assert out == [{"epoch": 1}], out
    sent = ws.sent[0]
    assert sent["ticks_history"] == "CRASH1000"
    assert sent["count"] == 10
    assert sent["style"] == "candles"
    assert sent["granularity"] == 60


def test_fetch_candles_raises_on_error():
    ws = FakeWS(lambda req: {"error": {"code": "InvalidSymbol",
                                        "message": "bad"}})
    try:
        asyncio.run(deriv_feed.fetch_candles(ws, "CRASH1000", 10, 1))
    except RuntimeError as exc:
        assert "bad" in str(exc), exc
        return
    raise AssertionError("expected RuntimeError")


def test_fetch_candles_skips_interleaved_ticks():
    def handler(req):
        return [
            {"msg_type": "tick", "req_id": 77, "tick": {"symbol": "CRASH1000"}},
            {"msg_type": "candles", "candles": [{"epoch": 7}]},
        ]

    ws = FakeWS(handler)
    out = asyncio.run(deriv_feed.fetch_candles(ws, "CRASH1000", 10, 1))
    assert out == [{"epoch": 7}], out


def test_ping_true_and_false():
    ws = FakeWS(lambda req: {"msg_type": "ping", "ping": "pong"})
    assert asyncio.run(deriv_feed.ping(ws)) is True
    assert asyncio.run(deriv_feed.ping(FakeWS(lambda req: None))) is False


def test_subscribe_ticks_returns_req_id():
    ws = FakeWS(lambda req: None)
    req_id = asyncio.run(deriv_feed.subscribe_ticks(ws, "CRASH1000"))
    assert ws.sent[0]["req_id"] == req_id
    assert ws.sent[0]["ticks"] == "CRASH1000"


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
        print("PASS", name)
