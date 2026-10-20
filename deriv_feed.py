"""Deriv WebSocket feed helpers: endpoint failover, symbol negotiation
and candle/tick retrieval.

Deriv publishes several WebSocket endpoints behind one CDN. They fail
independently and their symbol catalogues differ — the current API
(`/trading/v1/options/ws/public`) serves the crash indices as
`CRASH1000`, the legacy clusters as `R_1000`, and the public gateway
closes a connection after two minutes.

Because a tick subscription keeps pushing frames onto the same socket,
responses are matched on `req_id` instead of arrival order — otherwise a
single stray tick desynchronises everything that follows.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time

log = logging.getLogger("crash_alert.deriv_feed")

DEFAULT_APP_ID = 1089
WS_PATH = "/websockets/v3"

_req_ids = itertools.count(1)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_endpoints(raw):
    """Split the WS_ENDPOINTS setting into an ordered list of base URLs.

    Blank entries are dropped and duplicates are removed, so the first
    reachable endpoint in the list wins.
    """
    out = []
    for part in (raw or "").split(","):
        part = part.strip().rstrip("/")
        if part and part not in out:
            out.append(part)
    return out


def parse_symbols(raw):
    """Split a comma-separated symbol list, preserving order, no dupes."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def build_ws_url(endpoint, app_id=DEFAULT_APP_ID):
    """Turn a base endpoint into a full WebSocket URL carrying app_id.

    A bare host gets the legacy `/websockets/v3` path; an endpoint that
    already carries a path (such as the public market-data gateway at
    `/trading/v1/options/ws/public`) is used exactly as given.
    """
    endpoint = (endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("empty endpoint")
    if endpoint.startswith("https://"):
        endpoint = "wss://" + endpoint[len("https://"):]
    elif not endpoint.startswith("wss://"):
        endpoint = "wss://" + endpoint
    if "/" not in endpoint.split("://", 1)[1] and not endpoint.endswith(WS_PATH):
        endpoint += WS_PATH
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}app_id={app_id}"


def symbol_candidates(primary, fallbacks=""):
    """Ordered candidate list: the configured symbol first, then fallbacks."""
    return [primary] + [s for s in parse_symbols(fallbacks) if s != primary]


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


def next_req_id():
    """Monotonic request id used to match responses to requests."""
    return next(_req_ids)


def _matches(msg, req_id):
    """True when `msg` is the response to the request tagged `req_id`."""
    if msg.get("req_id") == req_id:
        return True
    echo = msg.get("echo_req") or {}
    return echo.get("req_id") == req_id


async def request(ws, payload, timeout=25, req_id=None):
    """Send one request and return its matching response.

    Frames belonging to an active tick subscription share the socket and
    are skipped until the response carrying `req_id` shows up. Raises
    RuntimeError when Deriv answers with an error and TimeoutError when
    no response arrives in time.
    """
    if req_id is None:
        req_id = next_req_id()
    body = dict(payload)
    body["req_id"] = req_id
    await ws.send(json.dumps(body))
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError(
                "no response for %s" % list(payload)[0])
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if not _matches(msg, req_id):
            continue
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message")
                               or msg["error"].get("code"))
        return msg


async def ping(ws, timeout=20):
    """True when the endpoint answers a ping."""
    try:
        msg = await request(ws, {"ping": 1}, timeout)
    except Exception:
        return False
    return msg.get("msg_type") == "ping"


async def _forget(ws, target, timeout=10):
    """Cancel a subscription. `target` is a subscription id or a symbol."""
    try:
        await request(ws, {"forget": target}, timeout)
    except Exception as exc:
        log.debug("forget %s failed: %s", target, exc)


async def probe_symbols(ws, symbols, timeout=15):
    """Split `symbols` into (streamable, rejected).

    A symbol counts as streamable when a tick subscription is accepted.
    The probe subscription is cancelled again straight away, so the
    caller can subscribe for real afterwards.
    """
    streamable, rejected = [], []
    for sym in symbols:
        try:
            msg = await request(ws, {"ticks": sym, "subscribe": 1},
                                timeout=timeout)
        except RuntimeError as exc:
            log.info("Symbol %s rejected: %s", sym, exc)
            rejected.append(sym)
            continue
        except asyncio.TimeoutError:
            log.warning("Symbol %s: no reply (timeout).", sym)
            rejected.append(sym)
            continue
        sub = (msg.get("subscription") or {}).get("id")
        await _forget(ws, sub or sym)
        streamable.append(sym)
    return streamable, rejected


async def fetch_candles(ws, symbol, count, minutes, timeout=30):
    """Fetch the last `count` candles for `symbol`.

    Returns the raw Deriv candle list (oldest first).
    """
    msg = await request(ws, {
        "ticks_history": symbol,
        "count": int(count),
        "end": "latest",
        "style": "candles",
        "granularity": int(minutes) * 60,
    }, timeout=timeout)
    return msg.get("candles") or []


async def subscribe_ticks(ws, symbol):
    """Subscribe to the live tick stream of `symbol`; returns its req_id."""
    req_id = next_req_id()
    await ws.send(json.dumps({"ticks": symbol, "subscribe": 1,
                              "req_id": req_id}))
    return req_id
