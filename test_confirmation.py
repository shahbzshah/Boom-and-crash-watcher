"""Tests for the confirmation tier: displacement-confirmed setups.

The rule under test is the one the 2026-10-22 backtest measured: a
fired setup is CONFIRMED when, within CRT_CONFIRM_WINDOW bars, a
candle closes through the setup candle's extreme in the trade
direction (SELL: down candle closing below the setup low), void if
the setup's stop is reached first.
"""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import pytest

import alerts
import config
from watcher import Watcher

T0 = 1_700_000_000


@pytest.fixture
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(alerts, "send_alert",
                        lambda text: box.append(text) or True)
    return box


def _bar(dt, o, h, l, c):
    return {"t": T0 + dt, "o": o, "h": h, "l": l, "c": c, "n": None}


def _setup(w, side="SELL"):
    if side == "SELL":
        entry, sl, tp = 101.0, 105.0, 95.0    # tp_dist 6
    else:
        entry, sl, tp = 101.0, 95.0, 107.0    # tp_dist 6
    rec = {"side": side, "epoch": T0, "entry": entry, "sl": sl,
           "tp": tp, "score": 4, "conditions": {}, "atr": 0.6,
           "rr": 1.5, "outcome": "open", "points": None,
           "closed_epoch": None, "duration_sec": None,
           "tier": "setup", "confirmed": False, "setup_epoch": None}
    w.signal_history.append(rec)
    sig = {"side": side, "epoch": T0, "entry": entry, "sl": sl,
           "tp": tp, "score": 4, "conditions": {}, "atr": 0.6}
    candle = {"t": T0, "o": 100.5, "h": 102.0, "l": 100.0,
              "c": 101.0, "n": None}
    w._arm_confirmation(sig, candle, rec)
    return rec


def test_sell_confirms_on_displacement(sent):
    w = Watcher()
    rec = _setup(w)
    # bar 1: rises, no void (h < sl), no displacement
    w._process_confirmations(_bar(60, 101.5, 102.5, 100.5, 102.0))
    assert len(w.signal_history) == 1 and w._pending_confirms
    # bar 2: down candle closing below the setup low (100)
    w._process_confirmations(_bar(120, 101.8, 102.2, 99.0, 99.5))
    assert not w._pending_confirms
    assert len(w.signal_history) == 2
    conf = w.signal_history[-1]
    assert conf["tier"] == "confirmed"
    assert conf["entry"] == 99.5
    assert conf["sl"] == 105.0            # setup stop, unchanged
    assert conf["tp"] == 99.5 - 6.0       # setup TP distance preserved
    assert conf["setup_epoch"] == T0
    assert rec["confirmed"] is True
    assert len(sent) == 1 and "CONFIRMED SELL" in sent[0]


def test_void_when_stop_reached_first(sent):
    w = Watcher()
    rec = _setup(w)
    w._process_confirmations(_bar(60, 103.0, 105.5, 102.5, 104.0))
    assert not w._pending_confirms
    assert len(w.signal_history) == 1
    assert rec["confirmed"] is False
    assert sent == []


def test_void_beats_displacement_on_same_bar(sent):
    w = Watcher()
    _setup(w)
    # h reaches the stop AND the close displaces — the stop wins,
    # exactly as the backtest scored it
    w._process_confirmations(_bar(60, 104.0, 106.0, 98.0, 99.0))
    assert len(w.signal_history) == 1
    assert sent == []


def test_expires_after_window(sent):
    w = Watcher()
    _setup(w)
    for i in range(1, config.CRT_CONFIRM_WINDOW):
        w._process_confirmations(_bar(60 * i, 101.0, 102.0, 100.5, 101.5))
        assert w._pending_confirms
    w._process_confirmations(
        _bar(60 * config.CRT_CONFIRM_WINDOW, 101.0, 102.0, 100.5, 101.5))
    assert not w._pending_confirms
    assert len(w.signal_history) == 1
    assert sent == []


def test_down_close_above_setup_low_does_not_confirm(sent):
    w = Watcher()
    _setup(w)
    # bearish candle, but it closes at 100.5 — above the setup low
    w._process_confirmations(_bar(60, 101.5, 101.8, 100.2, 100.5))
    assert len(w.signal_history) == 1 and w._pending_confirms


def test_up_candle_below_setup_low_does_not_confirm(sent):
    w = Watcher()
    _setup(w)
    # closes below the setup low but it is an UP candle (opened lower)
    # — displacement requires a bearish close, not just a low print
    w._process_confirmations(_bar(60, 99.2, 99.9, 99.0, 99.8))
    assert len(w.signal_history) == 1 and w._pending_confirms


def test_buy_confirms_on_upward_displacement(sent):
    w = Watcher()
    rec = _setup(w, side="BUY")   # setup high 102, sl 95, tp_dist 6
    w._process_confirmations(_bar(60, 101.0, 103.0, 100.5, 102.8))
    conf = w.signal_history[-1]
    assert conf["tier"] == "confirmed"
    assert conf["entry"] == 102.8
    assert conf["sl"] == 95.0
    assert conf["tp"] == 102.8 + 6.0
    assert rec["confirmed"] is True
    assert "CONFIRMED BUY" in sent[0]


def test_same_bar_processed_once(sent):
    w = Watcher()
    _setup(w)
    bar = _bar(60, 101.5, 102.5, 100.5, 102.0)
    w._process_confirmations(bar)
    w._process_confirmations(bar)  # duplicate close must not double-count
    assert w._pending_confirms[0]["bars_seen"] == 1


def test_confirmed_record_tracks_outcome(sent):
    w = Watcher()
    _setup(w)
    w._process_confirmations(_bar(60, 101.8, 102.2, 99.0, 99.5))
    conf = w.signal_history[-1]
    w._track_outcomes(T0 + 600, 93.0)   # through the confirmed TP
    assert conf["outcome"] == "tp"
    assert conf["points"] == 6.0        # entry - tp, to the level


def test_snapshot_stats_split_by_tier(sent):
    w = Watcher()
    rec = _setup(w)
    w._process_confirmations(_bar(60, 101.8, 102.2, 99.0, 99.5))
    conf = w.signal_history[-1]
    # close both: setup loses, confirmed wins
    rec["outcome"], rec["points"] = "sl", -4.0
    conf["outcome"], conf["points"] = "tp", 6.0
    snap = w.status_snapshot()
    assert snap["stats"]["signals"] == 1          # setups only
    assert snap["stats"]["hit_rate"] == 0.0
    assert snap["stats"]["total_points"] == -4.0
    cs = snap["stats"]["confirmed"]
    assert cs["signals"] == 1 and cs["tp"] == 1
    assert cs["hit_rate"] == 100.0
    assert cs["total_points"] == 6.0
    assert snap["config"]["confirm_window"] == config.CRT_CONFIRM_WINDOW
    view = snap["history"][0]                     # newest first: confirmed
    assert view["tier"] == "confirmed"
    assert view["setup_time_eat"] is not None
