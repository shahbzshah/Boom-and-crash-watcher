"""Tests for health.py — the tiny HTTP door for Render free tier."""
import gzip
import json
import os
import urllib.error
import urllib.request

import pytest

from health import maybe_start_health_server, start


def _get(server, path="/health"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def _get_raw(server, path, headers=None):
    # Raw bytes + headers: urllib never decompresses gzip on its
    # own, so this is how the gzip behaviour is observed.
    url = f"http://127.0.0.1:{server.server_port}{path}"
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.headers, resp.read()


def _big_status():
    # ~80 KB like the live /status.json snapshot.
    return {"history": [{"epoch": 1793000000 + i, "tier": "setup",
                         "outcome": "open", "entry": 6181.5,
                         "explanation": "x" * 200}
                        for i in range(200)]}


def test_health_reports_feed_state():
    server = start(0, lambda: True)
    try:
        status, body = _get(server)
        assert status == 200
        assert body == {"status": "ok", "feed_connected": True}
    finally:
        server.shutdown()


def test_health_unknown_path_is_404():
    server = start(0)
    try:
        url = f"http://127.0.0.1:{server.server_port}/nope"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url, timeout=5)
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_maybe_start_noop_without_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert maybe_start_health_server() is None


def test_maybe_start_uses_port_env(monkeypatch):
    monkeypatch.setenv("PORT", "0")
    server = maybe_start_health_server(lambda: False)
    try:
        assert server is not None
        status, body = _get(server)
        assert status == 200
        assert body["feed_connected"] is False
    finally:
        if server is not None:
            server.shutdown()


def test_maybe_start_ignores_bad_port(monkeypatch):
    monkeypatch.setenv("PORT", "notaport")
    assert maybe_start_health_server() is None


def test_status_json_gzipped_when_client_asks():
    # Bot-side fetch fix (2026-11-02): a client that sends
    # Accept-Encoding: gzip gets a gzipped snapshot that
    # decompresses to the exact provider payload.
    big = _big_status()
    server = start(0, lambda: True, status_provider=lambda: big)
    try:
        status, headers, raw = _get_raw(
            server, "/status.json", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert len(raw) < len(json.dumps(big).encode())
        assert json.loads(gzip.decompress(raw).decode()) == big
    finally:
        server.shutdown()


def test_status_json_plain_without_accept_encoding():
    # Clients that never ask (Crash Watch app's HttpURLConnection
    # poller, plain curl) get plain JSON exactly as before.
    big = _big_status()
    server = start(0, lambda: True, status_provider=lambda: big)
    try:
        status, headers, raw = _get_raw(server, "/status.json")
        assert status == 200
        assert headers.get("Content-Encoding") is None
        assert json.loads(raw.decode()) == big
    finally:
        server.shutdown()


def test_small_response_not_gzipped():
    # Below the 1024-byte threshold, even a client that asks for
    # gzip gets a plain response.
    server = start(0, lambda: True)
    try:
        status, headers, raw = _get_raw(
            server, "/health", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert headers.get("Content-Encoding") is None
        assert json.loads(raw.decode()) == {
            "status": "ok", "feed_connected": True}
    finally:
        server.shutdown()


# -- GET /history.json: compact history-only payload (2026-11-02) ----
# The explainer sidecar's 5-minute fetch kept dropping to 0-byte
# IncompleteReads even after the gzip fix, so it gets its own
# endpoint carrying exactly what it consumes — nothing else.

HISTORY_KEY = "historykey"


def _history_rec(epoch, tier, entry=100.0):
    return {
        "side": "SELL", "epoch": epoch, "entry": entry,
        "sl": entry + 5.0, "tp": entry - 7.5,
        "score": 4, "conditions": {"sweep": True},
        "atr": 1.0, "rr": 1.5,
        "outcome": "open", "points": None,
        "closed_epoch": None, "duration_sec": None,
        "tier": tier, "confirmed": False, "setup_epoch": None,
        "explanation": None,
    }


def _watcher_with_history():
    from watcher import Watcher

    w = Watcher()
    w.signal_history.append(_history_rec(1000, "setup"))
    w.signal_history.append(_history_rec(2000, "setup", entry=101.0))
    w.ctx["trend"] = "downtrend"
    w.ctx["phase"] = "distribution"
    w.ctx["phase_direction"] = "down"
    w.ctx["atr"] = 2.5
    for i in range(12):  # 12 closed candles; payload keeps the last 10
        w.candles.closed.append(
            {"t": 1000 + i * 300, "o": 100.0, "h": 101.0,
             "l": 99.0, "c": 100.5, "n": 5})
    return w


def _get_history(server, key=HISTORY_KEY):
    headers = {}
    if key is not None:
        headers["X-Dash-Key"] = key
    status, headers_out, raw = _get_raw(server, "/history.json", headers)
    return status, headers_out, json.loads(raw.decode())


def test_history_json_returns_compact_payload():
    w = _watcher_with_history()
    server = start(0, lambda: True, dashboard_key=HISTORY_KEY,
                   history_provider=w.history_payload)
    try:
        status, _, body = _get_history(server)
        assert status == 200
        # Exactly what the sidecar consumes — no score rows, no tier
        # stats, no config block, no last_signal duplicate.
        assert set(body) == {"history", "trend", "phase",
                             "phase_direction", "atr", "candles"}
        assert body["trend"] == "downtrend"
        assert body["phase"] == "distribution"
        assert body["phase_direction"] == "down"
        assert body["atr"] == 2.5
        # Newest first, raw ledger records: no dashboard display
        # strings (time_eat/date_eat/when_eat/setup_time_eat).
        assert [r["epoch"] for r in body["history"]] == [2000, 1000]
        for rec in body["history"]:
            assert "time_eat" not in rec
            assert "date_eat" not in rec
            assert "when_eat" not in rec
            assert "setup_time_eat" not in rec
            assert rec["explanation"] is None
        # Last 10 candles only, not the dashboard's 40.
        assert len(body["candles"]) == 10
        assert body["candles"][0]["t"] == 1000 + 2 * 300
        assert body["candles"][-1]["t"] == 1000 + 11 * 300
    finally:
        server.shutdown()


def test_history_json_requires_key():
    w = _watcher_with_history()
    server = start(0, lambda: True, dashboard_key=HISTORY_KEY,
                   history_provider=w.history_payload)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_history(server, key=None)
        assert exc.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_history(server, key="wrong")
        assert exc.value.code == 401
    finally:
        server.shutdown()


def test_history_json_404_without_provider():
    server = start(0, lambda: True, dashboard_key=HISTORY_KEY)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_history(server)
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_history_json_gzipped_when_client_asks():
    # Same gzip behaviour as /status.json: a client that asks gets a
    # gzipped payload that decompresses to the exact provider dict.
    big = {"history": [{"epoch": 1793000000 + i, "tier": "setup",
                        "outcome": "open", "entry": 6181.5,
                        "explanation": "x" * 200}
                       for i in range(200)],
           "trend": "ranging", "phase": None, "phase_direction": None,
           "atr": 1.5, "candles": []}
    server = start(0, lambda: True,
                   history_provider=lambda: big)
    try:
        status, headers, raw = _get_raw(
            server, "/history.json", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert len(raw) < len(json.dumps(big).encode())
        assert json.loads(gzip.decompress(raw).decode()) == big
    finally:
        server.shutdown()
