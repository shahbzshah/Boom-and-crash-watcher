"""Tests for the Crash Watch dashboard plumbing: crt_engine.score_detail,
the watcher's outcome ledger + status_snapshot, and health.py routes."""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import crt_engine
import health
from crt_engine import CRTEngine, build_series, exhaustion_score
from test_crt_engine import drift_candles
from watcher import Watcher


# ---------------------------------------------------------------------------
# score_detail parity with the engine's own scorer
# ---------------------------------------------------------------------------

def test_score_detail_matches_engine():
    eng = CRTEngine({"asset_mode": "crash"})
    candles = drift_candles(300)  # steady up-drift: a sell candidate
    s = build_series(candles, eng.cfg)
    score, cond = exhaustion_score(len(candles) - 1, False, s, eng.cfg)
    d = crt_engine.score_detail(candles, eng.cfg, buy=False)
    assert d["score"] == score
    assert d["conditions"] == cond
    assert d["rsi"] is not None and d["rsi"] > 99  # one-way drift up
    assert d["consecutive"] >= 10
    assert d["stretch_atr"] is not None and d["stretch_atr"] > 0
    assert d["move_atr"] is not None and d["move_atr"] > 0


def test_score_detail_empty():
    d = crt_engine.score_detail([], CRTEngine({}).cfg)
    assert d["score"] == 0 and d["rsi"] is None


# ---------------------------------------------------------------------------
# Outcome ledger
# ---------------------------------------------------------------------------

def _rec(side, entry, sl, tp, epoch=1_700_000_000):
    return {"side": side, "epoch": epoch, "entry": entry, "sl": sl,
            "tp": tp, "score": 5, "conditions": {}, "atr": 3.0,
            "rr": 2.0, "outcome": "open", "points": None,
            "closed_epoch": None, "duration_sec": None}


def test_outcome_sell_tp_and_sl():
    w = Watcher()
    rec = _rec("SELL", 100.0, 110.0, 80.0)
    w.signal_history.append(rec)
    w._track_outcomes(rec["epoch"] + 60, 95.0)   # between: still open
    assert rec["outcome"] == "open"
    w._track_outcomes(rec["epoch"] + 252, 79.5)  # through TP
    assert rec["outcome"] == "tp"
    assert rec["points"] == 20.0
    assert rec["duration_sec"] == 252

    rec2 = _rec("SELL", 100.0, 110.0, 80.0)
    w.signal_history.append(rec2)
    w._track_outcomes(rec2["epoch"] + 30, 110.5)  # through SL
    assert rec2["outcome"] == "sl"
    assert rec2["points"] == -10.0


def test_outcome_buy_tp():
    w = Watcher()
    rec = _rec("BUY", 100.0, 90.0, 120.0)
    w.signal_history.append(rec)
    w._track_outcomes(rec["epoch"] + 10, 121.0)
    assert rec["outcome"] == "tp" and rec["points"] == 20.0


# ---------------------------------------------------------------------------
# status_snapshot
# ---------------------------------------------------------------------------

def test_status_snapshot_shape():
    w = Watcher()
    w.candles.seed(drift_candles(300))
    snap = w.status_snapshot()
    assert snap["candles_closed"] == 300
    assert len(snap["candles"]) == 40
    assert snap["config"]["timeframe"] == "M5"
    assert snap["score"]["value"] >= 0
    assert snap["last_signal"] is None
    assert snap["stats"] == {"signals": 0, "tp": 0, "sl": 0, "open": 0,
                             "hit_rate": None, "total_points": 0,
                             "avg_rr": None,
                             "confirmed": {"signals": 0, "tp": 0, "sl": 0,
                                           "open": 0, "hit_rate": None,
                                           "total_points": 0},
                             "ema_retest": {"signals": 0, "tp": 0, "sl": 0,
                                            "open": 0, "hit_rate": None,
                                            "total_points": 0}}
    assert snap["system"]["host"] == "Render · Frankfurt"
    assert w._endpoint_label("wss://api.derivws.com/x") == "public gateway"


# ---------------------------------------------------------------------------
# health.py dashboard routes
# ---------------------------------------------------------------------------

def _req(server, path, key=None, method="GET"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    req = urllib.request.Request(url, method=method)
    if key:
        req.add_header("X-Dash-Key", key)
    return urllib.request.urlopen(req, timeout=5)


def test_dashboard_routes_and_key_gate():
    server = health.start(0, lambda: True,
                          status_provider=lambda: {"x": 1},
                          page_html="<html>dash</html>",
                          test_alert=lambda: (False, "chat not found"),
                          dashboard_key="sekret")
    try:
        with _req(server, "/") as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers["Content-Type"]
            assert b"dash" in resp.read()
        # status.json is key-gated
        try:
            _req(server, "/status.json")
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        with _req(server, "/status.json", key="sekret") as resp:
            assert json.loads(resp.read()) == {"x": 1}
        # also via ?k=
        with _req(server, "/status.json?k=sekret") as resp:
            assert resp.status == 200
        # ping records a hit
        with _req(server, "/ping") as resp:
            assert resp.status == 200
        assert health.last_ping() is not None
        # test alert, key-gated POST, detail passes through
        with _req(server, "/test-alert", key="sekret",
                  method="POST") as resp:
            assert json.loads(resp.read()) == {
                "sent": False, "detail": "chat not found"}
        # health JSON unchanged
        with _req(server, "/health") as resp:
            assert json.loads(resp.read()) == {
                "status": "ok", "feed_connected": True}
    finally:
        server.shutdown()


def test_candles_route_and_key_gate():
    # /candles serves the bot's closed-candle buffer for the
    # draggable chart, under the same key gate as /status.json.
    server = health.start(0, lambda: True,
                          status_provider=lambda: {"x": 1},
                          candles_provider=lambda: [
                              {"t": 1, "o": 1, "h": 2, "l": 0, "c": 1.5}],
                          page_html="<html>dash</html>",
                          dashboard_key="sekret")
    try:
        try:
            _req(server, "/candles")
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        with _req(server, "/candles", key="sekret") as resp:
            body = json.loads(resp.read())
            assert body["candles"][0]["t"] == 1
        with _req(server, "/candles?k=sekret") as resp:
            assert resp.status == 200
    finally:
        server.shutdown()
