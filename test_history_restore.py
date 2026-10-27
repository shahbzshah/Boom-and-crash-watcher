"""Tests for history restore: the ledger surviving deploys.

The ledger is in-memory and resets on every Render deploy. The fix:
the explainer sidecar refreshes history_snapshot.json in the repo
from the permanent archive, and the watcher loads it at startup.
These tests cover the snapshot file format, the restore load, the
duplicate guard on the boundary candle after a restart, and the
re-arming of confirmation watches for restored open setups.
"""
import json
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import alerts
import config
import history_snapshot
from watcher import Watcher

T0 = 1_700_000_000


def _rec(epoch, tier="setup", outcome="open", side="SELL", **over):
    rec = {"side": side, "epoch": epoch, "entry": 101.0, "sl": 105.0,
           "tp": 95.0, "score": 4, "conditions": {}, "atr": 0.6,
           "rr": 1.5, "outcome": outcome, "points": None,
           "closed_epoch": None, "duration_sec": None,
           "tier": tier, "confirmed": False, "setup_epoch": None,
           "explanation": None, "timeframe": "M5"}
    rec.update(over)
    return rec


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_dockerfile_copies_snapshot():
    """Regression guard for the first deploy of this fix: the
    Dockerfile copied only *.py, so the snapshot never reached the
    image and the bot started with an empty ledger."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "Dockerfile"), encoding="utf-8") as f:
        text = f.read()
    assert "history_snapshot.json" in text


# -- snapshot file --------------------------------------------------------


def test_load_snapshot_missing_and_corrupt(tmp_path):
    assert history_snapshot.load_snapshot(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert history_snapshot.load_snapshot(str(bad)) == []
    _write(str(tmp_path / "shape.json"), {"epoch": 1})
    assert history_snapshot.load_snapshot(str(tmp_path / "shape.json")) == []


def test_load_snapshot_sorts_and_caps(tmp_path):
    recs = [_rec(T0 + i * 60) for i in range(105)]
    _write(str(tmp_path / "s.json"), list(reversed(recs)))
    loaded = history_snapshot.load_snapshot(str(tmp_path / "s.json"))
    assert len(loaded) == 100
    assert loaded[0]["epoch"] == T0 + 5 * 60
    assert loaded[-1]["epoch"] == T0 + 104 * 60


def test_load_snapshot_drops_invalid(tmp_path):
    good = _rec(T0)
    bad = dict(good, epoch="soon", tier="setup")
    _write(str(tmp_path / "s.json"), [bad, good])
    assert history_snapshot.load_snapshot(str(tmp_path / "s.json")) == [good]


def test_build_snapshot_last_line_wins_and_strips(tmp_path):
    arch = tmp_path / "arch.jsonl"
    open_line = dict(_rec(T0), archived_at="x", source="live",
                     time_eat="00:00", date_eat="d", when_eat="w")
    closed_line = dict(open_line, outcome="tp", points=6.0,
                       closed_epoch=T0 + 300, duration_sec=300,
                       explanation="read")
    with open(arch, "w", encoding="utf-8") as f:
        f.write(json.dumps(open_line) + "\n")
        f.write("garbage\n")
        f.write(json.dumps(closed_line) + "\n")
    built = history_snapshot.build_snapshot(str(arch))
    assert len(built) == 1
    assert built[0]["outcome"] == "tp"
    assert built[0]["explanation"] == "read"
    for key in ("archived_at", "source", "time_eat", "date_eat",
                "when_eat"):
        assert key not in built[0]


def test_build_snapshot_missing_archive(tmp_path):
    assert history_snapshot.build_snapshot(str(tmp_path / "no.jsonl")) == []


def test_snapshot_round_trip(tmp_path):
    recs = [_rec(T0), _rec(T0 + 60, tier="confirmed", setup_epoch=T0)]
    path = str(tmp_path / "s.json")
    history_snapshot.write_snapshot(path, recs)
    assert history_snapshot.load_snapshot(path) == recs


# -- watcher restore ------------------------------------------------------


def test_watcher_restores_snapshot(tmp_path):
    path = str(tmp_path / "s.json")
    history_snapshot.write_snapshot(path, [_rec(T0, outcome="tp",
                                                 points=6.0),
                                           _rec(T0 + 60)])
    w = Watcher(history_snapshot_path=path)
    assert len(w.signal_history) == 2
    assert w.signal_history[0]["outcome"] == "tp"
    assert w.signal_history[1]["outcome"] == "open"


def test_restored_open_record_tracks_outcomes(tmp_path):
    path = str(tmp_path / "s.json")
    history_snapshot.write_snapshot(path, [_rec(T0)])
    w = Watcher(history_snapshot_path=path)
    w._track_outcomes(T0 + 300, 94.0)  # below TP 95: SELL wins
    rec = w.signal_history[0]
    assert rec["outcome"] == "tp"
    assert rec["points"] == 6.0
    assert rec["closed_epoch"] == T0 + 300


def test_restored_record_accepts_explanation(tmp_path):
    path = str(tmp_path / "s.json")
    history_snapshot.write_snapshot(path, [_rec(T0)])
    w = Watcher(history_snapshot_path=path)
    ok, _, status = w.attach_explanation(T0, "setup", "AI read.")
    assert ok and status == 200
    assert w.signal_history[0]["explanation"] == "AI read."


def test_record_if_new_blocks_duplicates():
    w = Watcher()
    assert w._record_if_new(_rec(T0)) is True
    assert w._record_if_new(_rec(T0)) is False
    assert w._record_if_new(_rec(T0, tier="confirmed")) is True
    assert len(w.signal_history) == 2


# -- confirmation re-arm --------------------------------------------------


def _bar(dt, o, h, l, c):
    return {"t": T0 + dt, "o": o, "h": h, "l": l, "c": c, "n": None}


def _watcher_with_setup(tmp_path, bars, **rec_over):
    path = str(tmp_path / "s.json")
    history_snapshot.write_snapshot(path, [_rec(T0, **rec_over)])
    w = Watcher(history_snapshot_path=path)
    w.candles.seed(bars)
    return w


def test_rearm_one_bar_old(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.0, 102.0, 100.5, 101.5)]
    w = _watcher_with_setup(tmp_path, bars)
    w._restore_confirmations()
    assert len(w._pending_confirms) == 1
    p = w._pending_confirms[0]
    assert p["setup_epoch"] == T0 and p["bars_seen"] == 0
    assert p["setup_low"] == 100.0 and p["sl"] == 105.0


def test_rearm_two_bars_old_clean(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.0, 102.0, 100.5, 101.5),
            _bar(600, 101.5, 102.5, 101.0, 102.0)]
    w = _watcher_with_setup(tmp_path, bars)
    w._restore_confirmations()
    assert len(w._pending_confirms) == 1
    assert w._pending_confirms[0]["bars_seen"] == 1


def test_rearm_skips_voided_setup(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.0, 105.5, 101.0, 104.0),  # touched SL 105
            _bar(600, 104.0, 104.5, 103.0, 103.5)]
    w = _watcher_with_setup(tmp_path, bars)
    w._restore_confirmations()
    assert not w._pending_confirms


def test_rearm_skips_already_displaced(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.8, 102.2, 99.0, 99.5),  # displaced already
            _bar(600, 99.5, 100.0, 99.0, 99.2)]
    w = _watcher_with_setup(tmp_path, bars)
    w._restore_confirmations()
    assert not w._pending_confirms


def test_rearm_skips_confirmed_and_expired(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.0, 102.0, 100.5, 101.5),
            _bar(600, 101.5, 102.5, 101.0, 102.0)]
    w = _watcher_with_setup(tmp_path, bars, confirmed=True)
    w._restore_confirmations()
    assert not w._pending_confirms
    old = [_bar(0, 100.5, 102.0, 100.0, 101.0),
           _bar(300, 101.0, 102.0, 100.5, 101.5),
           _bar(600, 101.5, 102.5, 101.0, 102.0),
           _bar(900, 102.0, 102.5, 101.5, 102.2),
           _bar(1200, 102.2, 103.0, 102.0, 102.5)]
    w2 = _watcher_with_setup(tmp_path, old)
    w2._restore_confirmations()
    assert not w2._pending_confirms


def test_rearm_idempotent(tmp_path):
    bars = [_bar(0, 100.5, 102.0, 100.0, 101.0),
            _bar(300, 101.0, 102.0, 100.5, 101.5)]
    w = _watcher_with_setup(tmp_path, bars)
    w._restore_confirmations()
    w._restore_confirmations()
    assert len(w._pending_confirms) == 1
