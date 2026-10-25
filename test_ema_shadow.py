"""Tests for the EMA-retest shadow tier (Dancan proposal, backtested
2026-10-24): downtrend def C, EMA6-retest trigger, SL = EMA91,
TP = 1.5R, spike exclusion, one trade at a time.

The live module (ema_shadow.py) must reproduce the offline backtest
(ema_retest.py) exactly — see test_parity_with_backtest, which feeds
the frozen 44,000-candle dataset through the live engine and compares
every entry against the backtest's best variant.
"""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import pytest

import alerts
import crt_engine
import ema_shadow
from ema_shadow import EMAShadowEngine, downtrend_c, ema_levels, is_down_spike
from watcher import Watcher

T0 = 1_700_000_000


def _bar(i, o, h, l, c):
    return {"t": T0 + i * 60, "o": o, "h": h, "l": l, "c": c, "n": None}


# -- pure helpers --------------------------------------------------------

def test_downtrend_c():
    assert downtrend_c(100.0, 101.0, 102.0) is True
    assert downtrend_c(103.0, 101.0, 102.0) is False  # close above EMA91
    assert downtrend_c(100.0, 103.0, 102.0) is False  # EMA91 above EMA365
    assert downtrend_c(100.0, 100.0, 102.0) is False  # close == EMA91
    assert downtrend_c(100.0, None, 102.0) is False
    assert downtrend_c(100.0, 101.0, None) is False


def test_is_down_spike():
    assert is_down_spike(100.0, 95.0, 1.0) is True   # body 5 >= 3x ATR
    assert is_down_spike(100.0, 98.0, 1.0) is False  # body 2 < 3x ATR
    assert is_down_spike(100.0, 95.0, None) is False
    assert is_down_spike(100.0, 95.0, 0.0) is False


def test_ema_levels():
    assert ema_levels(100.0, 105.0) == (105.0, 92.5)  # 1.5R below entry
    assert ema_levels(100.0, 100.0) is None
    assert ema_levels(100.0, 99.0) is None
    assert ema_levels(100.0, None) is None


# -- synthetic declining series ------------------------------------------
#
# Closes decline linearly, so short EMAs sit below long EMAs and the
# close sits below EMA91: downtrend def C holds throughout. Bars are
# built AFTER the closes list is final, with highs/lows set relative
# to the EMA series computed from those same closes — highs/lows do
# not feed the EMAs, so there is no circularity.

def _declining_closes(n, start=6000.0, step=0.5):
    return [start - step * i for i in range(n)]


def _bars_from_closes(closes, poke=None, over=None):
    """poke: {index: extra_high_above_ema6}; over: {index: close}.

    Default bars drift down gently (open = prev close, small range).
    `over` replaces a bar's close (EMAs must be recomputed by the
    caller over the final closes before building bars)."""
    emas6 = crt_engine.ema_series(closes, 6)
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c + 0.5
        h, l = max(o, c) + 0.1, min(o, c) - 0.1
        if poke and i in poke:
            h = emas6[i] + poke[i]
        bars.append(_bar(i, o, h, l, c))
    return bars, emas6


def _feed_all(engine, bars):
    out = []
    for b in bars:
        e = engine.feed(b)
        if e is not None:
            out.append((b, e))
    return out


def test_flat_market_never_fires():
    bars = [_bar(i, 6000.0, 6000.0, 6000.0, 6000.0) for i in range(500)]
    assert _feed_all(EMAShadowEngine(), bars) == []


def test_ema6_retest_entry_geometry():
    closes = _declining_closes(420)
    bars, emas6 = _bars_from_closes(closes, poke={400: 1.0})
    # Poke bar 400: high above EMA6, close below EMA91 -> pullback.
    bars[400]["l"] = bars[400]["c"] - 0.1
    # Entry bar 401: close below the pullback low, small body.
    bars[401]["c"] = bars[400]["l"] - 1.0
    bars[401]["o"] = bars[401]["c"] + 1.0
    bars[401]["h"] = bars[401]["c"] + 1.2
    bars[401]["l"] = bars[401]["c"] - 0.1
    entries = _feed_all(EMAShadowEngine(), bars)
    assert len(entries) == 1
    bar, e = entries[0]
    assert bar["t"] == bars[401]["t"]
    # Expected SL comes from the FINAL bars' closes (bar 401's close
    # was modified after the bars were built).
    closes_final = [b["c"] for b in bars]
    ema91 = crt_engine.ema_series(closes_final, 91)
    assert e["entry"] == pytest.approx(bars[401]["c"])
    assert e["sl"] == pytest.approx(ema91[401])
    assert e["tp"] == pytest.approx(
        e["entry"] - 1.5 * (e["sl"] - e["entry"]))


def test_pullback_void_on_close_above_ema91():
    closes = _declining_closes(420)
    # Void bar 401 must close at/above EMA91 *after* its own close is
    # counted in the EMA — iterate to a fixed point (k = 2/92).
    for _ in range(5):
        ema91 = crt_engine.ema_series(closes, 91)
        if closes[401] >= ema91[401]:
            break
        closes[401] = ema91[401] + 10.0
    bars, _ = _bars_from_closes(closes, poke={400: 1.0})
    bars[400]["l"] = bars[400]["c"] - 0.1
    assert _feed_all(EMAShadowEngine(), bars) == []


def test_spike_bar_entry_excluded_then_recovery():
    closes = _declining_closes(420)
    bars, emas6 = _bars_from_closes(closes, poke={400: 1.0, 405: 1.0})
    bars[400]["l"] = bars[400]["c"] - 0.1
    # Entry bar 401: below the pullback low but a down-spike body.
    bars[401]["c"] = bars[400]["l"] - 1.0
    bars[401]["o"] = bars[401]["c"] + 5.0  # body 5 >> 3x ATR (~0.7)
    bars[401]["h"] = bars[401]["o"] + 0.1
    bars[401]["l"] = bars[401]["c"] - 0.1
    # Second setup after the void: poke 405, entry 406, small body.
    bars[405]["l"] = bars[405]["c"] - 0.1
    bars[406]["c"] = bars[405]["l"] - 1.0
    bars[406]["o"] = bars[406]["c"] + 1.0
    bars[406]["h"] = bars[406]["c"] + 1.2
    bars[406]["l"] = bars[406]["c"] - 0.1
    entries = _feed_all(EMAShadowEngine(), bars)
    assert len(entries) == 1
    assert entries[0][0]["t"] == bars[406]["t"]


def test_sequential_one_trade_at_a_time():
    closes = _declining_closes(430)
    bars, emas6 = _bars_from_closes(closes, poke={400: 1.0, 405: 1.0,
                                                  412: 1.0})
    bars[400]["l"] = bars[400]["c"] - 0.1
    bars[401]["c"] = bars[400]["l"] - 1.0
    bars[401]["o"] = bars[401]["c"] + 1.0
    bars[401]["h"] = bars[401]["c"] + 1.2
    bars[401]["l"] = bars[401]["c"] - 0.1
    entries = []
    eng = EMAShadowEngine()
    for b in bars[:402]:
        e = eng.feed(b)
        if e is not None:
            entries.append((b, e))
    assert len(entries) == 1
    sl, tp = entries[0][1]["sl"], entries[0][1]["tp"]
    # Bars 402-410: price stays between SL and TP (trade open), with
    # a second pullback pattern at 405/406 that must NOT fire.
    for i in range(402, 411):
        c = (sl + tp) / 2.0
        bars[i] = _bar(i, c + 0.2, c + 0.4, c - 0.4, c)
    bars[405]["h"] = emas6[405] + 1.0
    bars[406]["c"] = bars[405]["l"] - 1.0
    bars[406]["l"] = bars[406]["c"] - 0.1
    for b in bars[402:411]:
        assert eng.feed(b) is None
    # Bar 411 closes the trade at TP; scanning resumes at 412.
    bars[411] = _bar(411, tp + 2.0, sl - 1.0, tp - 1.0, tp - 0.5)
    assert eng.feed(bars[411]) is None
    # Third setup after the close: poke 412, entry 413.
    bars[412]["l"] = bars[412]["c"] - 0.1
    bars[413]["c"] = bars[412]["l"] - 1.0
    bars[413]["o"] = bars[413]["c"] + 1.0
    bars[413]["h"] = bars[413]["c"] + 1.2
    bars[413]["l"] = bars[413]["c"] - 0.1
    assert eng.feed(bars[412]) is None
    e = eng.feed(bars[413])
    assert e is not None and e["entry"] == pytest.approx(bars[413]["c"])


# -- watcher integration ---------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(alerts, "send_alert",
                        lambda text: box.append(text) or True)
    return box


def test_seeded_bars_produce_no_alerts_or_records(sent):
    closes = _declining_closes(500)
    bars, _ = _bars_from_closes(closes, poke={400: 1.0})
    bars[400]["l"] = bars[400]["c"] - 0.1
    bars[401]["c"] = bars[400]["l"] - 1.0
    bars[401]["o"] = bars[401]["c"] + 1.0
    bars[401]["h"] = bars[401]["c"] + 1.2
    bars[401]["l"] = bars[401]["c"] - 0.1
    w = Watcher()
    w.candles.seed(bars)
    w.ema_shadow.reset()
    suppressed = w.ema_shadow.feed_many(w.candles.closed)
    assert suppressed >= 1  # the seed really contained an entry
    assert len(w.signal_history) == 0
    assert sent == []


def test_live_entry_alert_and_record(sent):
    closes = _declining_closes(420)
    bars, _ = _bars_from_closes(closes, poke={400: 1.0})
    bars[400]["l"] = bars[400]["c"] - 0.1
    bars[401]["c"] = bars[400]["l"] - 1.0
    bars[401]["o"] = bars[401]["c"] + 1.0
    bars[401]["h"] = bars[401]["c"] + 1.2
    bars[401]["l"] = bars[401]["c"] - 0.1
    w = Watcher()
    w.candles.seed(bars[:400])  # history without the setup
    w.ema_shadow.reset()
    w.ema_shadow.feed_many(w.candles.closed)
    # Live bars: poke then entry, fed through the real candle path.
    for b in bars[400:402]:
        w.candles.closed.append(b)
        w._on_candle_closed()
    ema_recs = [r for r in w.signal_history
                if r.get("tier") == "ema_retest"]
    assert len(ema_recs) == 1
    assert ema_recs[0]["side"] == "SELL"
    assert ema_recs[0]["outcome"] == "open"
    assert len(sent) == 1
    assert "[EMA-RETEST · SHADOW]" in sent[0]
    assert "do not trade off it" in sent[0]


def test_snapshot_tier_split():
    w = Watcher()

    def rec(tier, outcome, points):
        return {"side": "SELL", "epoch": T0, "entry": 100.0, "sl": 105.0,
                "tp": 92.5, "score": None, "conditions": None,
                "atr": 0.6, "rr": 1.5, "outcome": outcome,
                "points": points, "closed_epoch": None,
                "duration_sec": None, "tier": tier, "confirmed": False,
                "setup_epoch": None}

    w.signal_history.append(rec("setup", "tp", 7.5))
    w.signal_history.append(rec("setup", "sl", -5.0))
    w.signal_history.append(rec("confirmed", "tp", 6.0))
    w.signal_history.append(rec("ema_retest", "tp", 7.5))
    w.signal_history.append(rec("ema_retest", "open", None))
    snap = w.status_snapshot()
    assert snap["stats"]["signals"] == 2  # ema excluded from setup stats
    assert snap["stats"]["confirmed"]["signals"] == 1
    em = snap["stats"]["ema_retest"]
    assert em["signals"] == 2 and em["tp"] == 1 and em["open"] == 1
    assert em["hit_rate"] == 100.0 and em["total_points"] == 7.5
    assert snap["config"]["ema_retest"]["tp"] == "1.5R"
    assert "30 live days" in snap["config"]["ema_retest"]["judgment"]


def test_ema_retest_alert_format():
    rec = {"side": "SELL", "epoch": T0, "entry": 5798.0, "sl": 5820.0,
           "tp": 5765.0}
    text = alerts.format_ema_retest_alert(rec, "CRASH1000", "M1")
    assert "[EMA-RETEST · SHADOW]" in text
    assert "5798.00" in text and "5820.00" in text and "5765.00" in text
    assert "do not trade off it" in text
    assert "score" not in text.lower()


# -- parity with the offline backtest (required) ---------------------------

def test_parity_with_backtest():
    sys.path.insert(0, os.path.expanduser(
        "~/workspace/research_notes/ema-retest-20261024"))
    import ema_retest
    import sweep

    candles = sweep.load_candles()
    d = ema_retest.Data(candles)
    trades, _ = ema_retest.run_variant(d, "C", 2, "ema91", "1.5R", True)

    eng = EMAShadowEngine()
    entries = []
    for bar in candles:
        e = eng.feed(bar)
        if e is not None:
            entries.append(e)
    assert len(entries) == len(trades)
    for e, t in zip(entries, trades):
        assert e["epoch"] == t["entry_epoch"]
        assert e["entry"] == pytest.approx(t["entry"], abs=0.01)
        assert e["sl"] == pytest.approx(t["sl"], abs=0.01)
        assert e["tp"] == pytest.approx(t["tp"], abs=0.01)

    # Resolve outcomes backtest-style (SL first on same-bar ambiguity)
    # and compare aggregates with results.json's best variant.
    by_epoch = {c["t"]: i for i, c in enumerate(candles)}
    closed = []
    for e in entries:
        i = by_epoch[e["epoch"]]
        outcome, points = "open", None
        for q in range(i + 1, len(candles)):
            if candles[q]["h"] >= e["sl"]:
                outcome, points = "sl", round(e["entry"] - e["sl"], 2)
                break
            if candles[q]["l"] <= e["tp"]:
                outcome, points = "tp", round(e["entry"] - e["tp"], 2)
                break
        if outcome != "open":
            closed.append((outcome, points))
    tp = sum(1 for o, _ in closed if o == "tp")
    pts = round(sum(p for _, p in closed), 1)
    assert len(closed) == 24
    assert round(tp / len(closed) * 100, 1) == 41.7
    assert pts == 67.2
