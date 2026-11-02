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
