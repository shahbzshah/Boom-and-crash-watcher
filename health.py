"""Tiny stdlib HTTP health server for the alert bot.

Container hosts on free tiers (Render free web services) need an HTTP
door to knock on: they sleep the process after ~15 idle minutes and only
stay awake while the port sees traffic. This module adds that door with
zero new dependencies.

It activates ONLY when the PORT environment variable is set (Render
always sets it; Render also requires the process to bind 0.0.0.0:$PORT).
Everywhere else — Northflank, a plain PC, a Docker run with no published
port — the import is a no-op and the daemon behaves exactly as before.

Usage (run.py):
    from health import maybe_start_health_server
    watcher = Watcher()
    maybe_start_health_server(lambda: watcher._connected)
    asyncio.run(watcher.run())
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

log = logging.getLogger("crash_alert.health")


def _make_handler(feed_connected: Callable[[], bool]):
    class HealthHandler(BaseHTTPRequestHandler):
        server_version = "CrashAlertHealth/1.0"

        def _payload(self) -> bytes:
            try:
                connected = bool(feed_connected())
            except Exception:  # health must never crash the bot
                connected = False
            return json.dumps(
                {"status": "ok", "feed_connected": connected}
            ).encode("utf-8")

        def do_GET(self):
            if self.path not in ("/", "/health", "/healthz"):
                self.send_response(404)
                self.end_headers()
                return
            body = self._payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the bot's logs clean
            pass

    return HealthHandler


def start(port: int, feed_connected: Optional[Callable[[], bool]] = None):
    """Start the health server on a background daemon thread.

    Returns the server instance so tests can read the bound port.
    """
    handler = _make_handler(feed_connected or (lambda: False))
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
):
    """Start the health server only when PORT is set (Render free tier).

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
    return start(port, feed_connected)
