"""Tests for the ntfy backend and the alerts dispatcher."""
import io
import os
import sys
import urllib.error

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-token-123")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import alerts
import config
import ntfy_alerts


class _Resp:
    def __init__(self, body=b"ok"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _capture(monkeypatch):
    """Replace urlopen; return a dict with the captured request."""
    seen = {}

    def fake(req, timeout=15):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode("utf-8")
        seen["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(ntfy_alerts.urllib.request, "urlopen", fake)
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic-abc123")
    return seen


def test_ntfy_success_posts_to_topic(monkeypatch):
    seen = _capture(monkeypatch)
    ok, detail = ntfy_alerts.send_alert_detailed("hello")
    assert ok is True and detail == "sent"
    assert seen["url"] == "https://ntfy.sh/test-topic-abc123"
    assert seen["body"] == "hello"
    assert "Title" in seen["headers"]


def test_ntfy_strips_telegram_html(monkeypatch):
    seen = _capture(monkeypatch)
    ntfy_alerts.send_alert_detailed("🚨 <b>[CRT] PRE-REVERSAL SELL</b>\nPrice: 1.2")
    assert "<b>" not in seen["body"]
    assert "[CRT] PRE-REVERSAL SELL" in seen["body"]


def test_ntfy_custom_server(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.example.com/")
    ntfy_alerts.send_alert_detailed("hello")
    assert seen["url"] == "https://ntfy.example.com/test-topic-abc123"


def test_ntfy_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    ok, detail = ntfy_alerts.send_alert_detailed("hello")
    assert ok is False
    assert "not configured" in detail


def test_ntfy_http_error(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic-abc123")

    def _raise(req, timeout=15):
        raise urllib.error.HTTPError(
            "https://ntfy.sh", 400, "Bad Request", None,
            io.BytesIO(b"topic name invalid"))

    monkeypatch.setattr(ntfy_alerts.urllib.request, "urlopen", _raise)
    ok, detail = ntfy_alerts.send_alert_detailed("hello")
    assert ok is False
    assert "topic name invalid" in detail


def test_ntfy_network_error(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic-abc123")

    def _raise(req, timeout=15):
        raise OSError("connection refused")

    monkeypatch.setattr(ntfy_alerts.urllib.request, "urlopen", _raise)
    ok, detail = ntfy_alerts.send_alert_detailed("hello")
    assert ok is False
    assert "connection refused" in detail


def test_send_alert_bool_wraps_detailed(monkeypatch):
    _capture(monkeypatch)
    assert ntfy_alerts.send_alert("hello") is True


def test_dispatcher_prefers_ntfy_when_topic_set(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic-abc123")
    calls = []
    monkeypatch.setattr(
        ntfy_alerts, "send_alert_detailed",
        lambda text: (calls.append("ntfy"), (True, "sent"))[1])
    monkeypatch.setattr(
        alerts.telegram_alerts, "send_alert_detailed",
        lambda text: (calls.append("telegram"), (True, "sent"))[1])
    assert alerts.backend_name() == "ntfy"
    assert alerts.send_alert("hi") is True
    assert calls == ["ntfy"]


def test_dispatcher_falls_back_to_telegram(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    calls = []
    monkeypatch.setattr(
        ntfy_alerts, "send_alert_detailed",
        lambda text: (calls.append("ntfy"), (True, "sent"))[1])
    monkeypatch.setattr(
        alerts.telegram_alerts, "send_alert_detailed",
        lambda text: (calls.append("telegram"), (True, "sent"))[1])
    assert alerts.backend_name() == "telegram"
    assert alerts.send_alert("hi") is True
    assert calls == ["telegram"]


def test_dispatcher_reexports_formatters():
    assert alerts.format_alert is not None
    assert alerts.format_crt_alert is not None
    assert alerts.format_confirmed_alert is not None
    assert alerts.format_status is not None


def test_validate_passes_with_ntfy_only(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic-abc123")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert config.validate() == []


def test_validate_suggests_ntfy_when_nothing_set(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    problems = config.validate()
    assert any("NTFY_TOPIC" in p for p in problems)


def test_validate_passes_with_telegram_only(monkeypatch):
    # Telegram configured, no ntfy: valid setup, no problems — the
    # hosted bot must start, not idle (regression: the ntfy hint was
    # appended unconditionally and counted as a failure).
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:real-token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    assert config.validate() == []
