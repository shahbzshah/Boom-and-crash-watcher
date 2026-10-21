"""Tiny stdlib HTTP server for the alert bot: health door + dashboard.

Container hosts on free tiers (Render free web services) need an HTTP
door to knock on: they sleep the process after ~15 idle minutes and only
stay awake while the port sees traffic. This module adds that door with
zero new dependencies.

It activates ONLY when the PORT environment variable is set (Render
always sets it; Render also requires the process to bind 0.0.0.0:$PORT).
Everywhere else — Northflank, a plain PC, a Docker run with no published
port — the import is a no-op and the daemon behaves exactly as before.

Routes (when the extras are wired in run.py):
    GET  /              the Crash Watch dashboard page (or health JSON)
    GET  /health        {"status": "ok", "feed_connected": bool}
    GET  /healthz       same as /health
    GET  /ping          keep-alive target; records the hit time so the
                        dashboard can show the pinger is alive
    GET  /status.json   live dashboard data (key-gated when a
                        DASHBOARD_KEY is configured)
    POST /test-alert    send a Telegram test alert (key-gated)

Usage (run.py):
    from health import maybe_start_health_server
    watcher = Watcher()
    maybe_start_health_server(lambda: watcher._connected, ...)
    asyncio.run(watcher.run())
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("crash_alert.health")

_LAST_PING = None  # epoch of the last GET /ping (the keep-alive pinger)


def last_ping():
    """Epoch seconds of the most recent /ping hit, or None."""
    return _LAST_PING


def _make_handler(feed_connected, status_provider=None, page_html=None,
                  test_alert=None, dashboard_key=None):
    class HealthHandler(BaseHTTPRequestHandler):
        server_version = "CrashAlertHealth/1.0"

        # -- helpers ----------------------------------------------------

        def _send(self, code, body, content_type="application/json"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _health_payload(self) -> bytes:
            try:
                connected = bool(feed_connected())
            except Exception:  # health must never crash the bot
                connected = False
            return json.dumps(
                {"status": "ok", "feed_connected": connected}
            ).encode("utf-8")

        def _key_ok(self, query) -> bool:
            if not dashboard_key:
                return True
            if self.headers.get("X-Dash-Key") == dashboard_key:
                return True
            return parse_qs(query).get("k", [None])[0] == dashboard_key

        # -- verbs --------------------------------------------------------

        def do_GET(self):
            global _LAST_PING
            parts = urlsplit(self.path)
            path, query = parts.path, parts.query
            if path == "/ping":
                _LAST_PING = int(time.time())
                self._send(200, b'{"status": "ok"}')
                return
            if path in ("/health", "/healthz"):
                self._send(200, self._health_payload())
                return
            if path == "/":
                if page_html:
                    self._send(200, page_html, "text/html; charset=utf-8")
                else:
                    self._send(200, self._health_payload())
                return
            if path == "/status.json":
                if status_provider is None:
                    self._send(404, b'{"error": "not found"}')
                    return
                if not self._key_ok(query):
                    self._send(401, b'{"error": "key required"}')
                    return
                try:
                    payload = status_provider()
                except Exception as exc:  # never take the bot down
                    log.error("status provider failed: %s", exc)
                    self._send(500, b'{"error": "status unavailable"}')
                    return
                self._send(200, json.dumps(payload))
                return
            self._send(404, b'{"error": "not found"}')

        def do_POST(self):
            parts = urlsplit(self.path)
            if parts.path == "/test-alert" and test_alert is not None:
                if not self._key_ok(parts.query):
                    self._send(401, b'{"error": "key required"}')
                    return
                try:
                    result = test_alert()
                    if isinstance(result, tuple):
                        sent, detail = bool(result[0]), str(result[1])
                    else:
                        sent, detail = bool(result), ""
                except Exception as exc:
                    log.error("test alert failed: %s", exc)
                    sent, detail = False, "Server error while sending."
                self._send(200, json.dumps({"sent": sent,
                                            "detail": detail}))
                return
            self._send(404, b'{"error": "not found"}')

        def log_message(self, *args):  # keep the bot's logs clean
            pass

    return HealthHandler


def start(port: int, feed_connected: Optional[Callable[[], bool]] = None,
          status_provider=None, page_html=None, test_alert=None,
          dashboard_key=None):
    """Start the HTTP server on a background daemon thread.

    Returns the server instance so tests can read the bound port.
    """
    handler = _make_handler(feed_connected or (lambda: False),
                            status_provider, page_html, test_alert,
                            dashboard_key)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, name="health-server", daemon=True
    )
    thread.start()
    log.info("Health server listening on 0.0.0.0:%d", server.server_port)
    return server


def maybe_start_health_server(
    feed_connected: Optional[Callable[[], bool]] = None,
    status_provider=None, page_html=None, test_alert=None,
    dashboard_key=None,
):
    """Start the HTTP server only when PORT is set (Render free tier).

    Returns the server instance, or None when not activated.
    """
    port_raw = os.environ.get("PORT", "").strip()
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        log.warning("Ignoring invalid PORT=%r; health server not started.",
                    port_raw)
        return None
    return start(port, feed_connected, status_provider, page_html,
                 test_alert, dashboard_key)
