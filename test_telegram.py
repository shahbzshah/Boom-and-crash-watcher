"""Tests for telegram_alerts.send_alert_detailed — the diagnosis path."""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-token-123")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import telegram_alerts


class _Resp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_detailed_success(monkeypatch):
    monkeypatch.setattr(
        telegram_alerts.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(b'{"ok": true}'))
    ok, detail = telegram_alerts.send_alert_detailed("hi")
    assert ok is True and detail == "sent"


def test_detailed_api_error_description(monkeypatch):
    body = json.dumps({"ok": False,
                       "description": "Bad Request: chat not found"}
                      ).encode()

    def _raise(req, timeout=15):
        raise urllib.error.HTTPError(
            "https://api.telegram.org", 400, "Bad Request", None,
            io.BytesIO(body))

    monkeypatch.setattr(telegram_alerts.urllib.request, "urlopen", _raise)
    ok, detail = telegram_alerts.send_alert_detailed("hi")
    assert ok is False
    assert detail == "Bad Request: chat not found"


def test_detailed_never_leaks_token(monkeypatch):
    def _raise(req, timeout=15):
        raise RuntimeError(
            "boom at https://api.telegram.org/botdummy-token-123/x")

    monkeypatch.setattr(telegram_alerts.urllib.request, "urlopen", _raise)
    ok, detail = telegram_alerts.send_alert_detailed("hi")
    assert ok is False
    assert "dummy-token-123" not in detail
    assert "***" in detail
