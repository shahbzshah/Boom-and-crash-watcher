"""Tests for health.py — the tiny HTTP door for Render free tier."""
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
