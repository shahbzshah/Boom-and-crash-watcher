"""Tests for POST /explain (display-only AI explanations).

The explainer sidecar writes through this endpoint AFTER alerts have
already fired. The endpoint must never touch entry/SL/TP/outcome,
never overwrite an existing explanation, and never create a record.
LLM text is untrusted: the dashboard escapes it before display.
"""
import json
import re
import subprocess
import urllib.error
import urllib.request

import dashboard
import health
from watcher import Watcher

KEY = "testkey"


def _rec(epoch, tier, entry=100.0):
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


def _post(server, body, key=KEY, path="/explain"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Dash-Key"] = key
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers,
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _server_with(watcher):
    return health.start(0, lambda: True, dashboard_key=KEY,
                        explain=watcher.attach_explanation)


def test_explain_rejects_wrong_or_missing_key():
    w = Watcher()
    w.signal_history.append(_rec(1000, "setup"))
    server = _server_with(w)
    try:
        status, _ = _post(server, {"epoch": 1000, "tier": "setup",
                                   "explanation": "hello"}, key=None)
        assert status == 401
        status, _ = _post(server, {"epoch": 1000, "tier": "setup",
                                   "explanation": "hello"}, key="nope")
        assert status == 401
        assert w.signal_history[0]["explanation"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_explain_accepts_query_string_key():
    w = Watcher()
    w.signal_history.append(_rec(1000, "setup"))
    server = _server_with(w)
    try:
        status, body = _post(server, {"epoch": 1000, "tier": "setup",
                                      "explanation": "plain read"},
                             key=None, path="/explain?k=testkey")
        assert status == 200
        assert body == {"ok": True}
        assert w.signal_history[0]["explanation"] == "plain read"
    finally:
        server.shutdown()
        server.server_close()


def test_explain_rejects_unknown_epoch():
    w = Watcher()
    w.signal_history.append(_rec(1000, "setup"))
    server = _server_with(w)
    try:
        status, body = _post(server, {"epoch": 9999, "tier": "setup",
                                      "explanation": "hello"})
        assert status == 404
        assert body["ok"] is False
        status, body = _post(server, {"epoch": 1000, "tier": "confirmed",
                                      "explanation": "hello"})
        assert status == 404  # same epoch, wrong tier: no match
    finally:
        server.shutdown()
        server.server_close()


def test_explain_refuses_to_overwrite():
    w = Watcher()
    rec = _rec(1000, "setup")
    rec["explanation"] = "first read"
    w.signal_history.append(rec)
    server = _server_with(w)
    try:
        status, body = _post(server, {"epoch": 1000, "tier": "setup",
                                      "explanation": "second read"})
        assert status == 409
        assert body["ok"] is False
        assert w.signal_history[0]["explanation"] == "first read"
    finally:
        server.shutdown()
        server.server_close()


def test_explain_rejects_bad_explanations():
    w = Watcher()
    w.signal_history.append(_rec(1000, "setup"))
    server = _server_with(w)
    try:
        for bad in ("", "x" * 601, 123, None, ["a"]):
            status, body = _post(server, {"epoch": 1000, "tier": "setup",
                                          "explanation": bad})
            assert status == 400, bad
            assert body["ok"] is False
        assert w.signal_history[0]["explanation"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_explain_attaches_and_leaves_numbers_alone():
    w = Watcher()
    rec = _rec(1000, "confirmed", entry=200.0)
    w.signal_history.append(rec)
    server = _server_with(w)
    try:
        status, body = _post(server, {"epoch": 1000, "tier": "confirmed",
                                      "explanation": "price displaced down"})
        assert status == 200
        assert body == {"ok": True}
        assert rec["explanation"] == "price displaced down"
        assert rec["entry"] == 200.0
        assert rec["sl"] == 205.0
        assert rec["tp"] == 192.5
        assert rec["outcome"] == "open"
    finally:
        server.shutdown()
        server.server_close()


def test_explain_works_on_legacy_record_without_field():
    w = Watcher()
    rec = _rec(1000, "ema_retest")
    del rec["explanation"]  # records created before this deploy
    w.signal_history.append(rec)
    server = _server_with(w)
    try:
        status, body = _post(server, {"epoch": 1000, "tier": "ema_retest",
                                      "explanation": "retest read"})
        assert status == 200
        assert rec["explanation"] == "retest read"
    finally:
        server.shutdown()
        server.server_close()


def test_snapshot_carries_explanation_through():
    w = Watcher()
    rec = _rec(1000, "setup")
    rec["explanation"] = "a read"
    w.signal_history.append(rec)
    snap = w.status_snapshot()
    assert snap["history"][0]["explanation"] == "a read"
    assert snap["last_signal"]["explanation"] == "a read"


def test_dashboard_escape_neutralizes_script_payload():
    match = re.search(r"function esc\(t\)\{[^}]*\}", dashboard.PAGE)
    assert match, "esc helper missing from dashboard page"
    payload = "<script>alert(\"x\")</script> & 'quotes'"
    proc = subprocess.run(
        ["node", "-e",
         match.group(0) + "; console.log(esc(process.argv[1]))",
         payload],
        capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&quot;" in out
    assert "&#39;" in out
    assert "&amp;" in out


def test_dashboard_ai_read_blocks_use_escape():
    assert "esc(ls.explanation)" in dashboard.PAGE
    assert "esc(r.explanation)" in dashboard.PAGE
    assert "AI READ" in dashboard.PAGE
