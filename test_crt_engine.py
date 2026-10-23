"""Offline tests for crt_engine.py — the CRT Pre-Reversal port.

Run:  python test_crt_engine.py
"""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import crt_engine
import structure
from crt_engine import CRTEngine, build_series, cycle_allows, exhaustion_score

CFG_CRASH = {"asset_mode": "crash"}
T0 = 1_700_000_000


def drift_candles(n, start=1000.0, step=2.0, t0=T0):
    """Steady one-way drift. ATR is a constant 3.0 for |step| = 2."""
    out, price = [], start
    for i in range(n):
        o, c = price, price + step
        price = c
        out.append({"t": t0 + i * 60, "o": o, "h": max(o, c) + 0.5,
                    "l": min(o, c) - 0.5, "c": c, "n": 30})
    return out


def next_drift_candle(candles, step=2.0):
    o = candles[-1]["c"]
    c = o + step
    return {"t": candles[-1]["t"] + 60, "o": o, "h": max(o, c) + 0.5,
            "l": min(o, c) - 0.5, "c": c, "n": 30}


# ---------------------------------------------------------------------------
# Indicator sanity
# ---------------------------------------------------------------------------

def test_indicators():
    closes = [100.0] * 50
    ema = crt_engine.ema_series(closes, 20)
    assert ema[-1] == 100.0, ema[-1]
    up = [float(i) for i in range(60)]
    assert crt_engine.rsi_series(up, 14)[-1] == 100.0
    down = [float(100 - i) for i in range(60)]
    assert crt_engine.rsi_series(down, 14)[-1] == 0.0
    candles = drift_candles(60)
    atr = crt_engine.atr_series(candles, 14)
    assert abs(atr[-1] - 3.0) < 1e-9, atr[-1]
    print("PASS indicators: EMA/RSI/ATR series sane")


# ---------------------------------------------------------------------------
# Exhaustion score
# ---------------------------------------------------------------------------

def test_score_full_and_gated():
    candles = drift_candles(300, step=2.0)  # up-drift -> SELL setup
    eng = CRTEngine(CFG_CRASH)
    s = build_series(candles, eng.cfg)
    p = len(candles) - 1
    score, cond = exhaustion_score(p, False, s, eng.cfg)
    assert score == 5 and all(cond.values()), (score, cond)

    # Knock out RSI + stretch -> score 3 (< min 4, must not signal)
    s2 = dict(s)
    s2["rsi"] = list(s["rsi"]); s2["rsi"][p] = 50.0
    s2["ema"] = list(s["ema"]); s2["ema"][p] = s["c"][p]
    score2, cond2 = exhaustion_score(p, False, s2, eng.cfg)
    assert score2 == 3 and not cond2["rsi"] and not cond2["stretch"], \
        (score2, cond2)

    # Knock out only stretch -> score 4 (exactly the MQL minimum)
    s3 = dict(s)
    s3["ema"] = list(s["ema"]); s3["ema"][p] = s["c"][p]
    score3, _ = exhaustion_score(p, False, s3, eng.cfg)
    assert score3 == 4, score3
    print("PASS score: full house = 5, gated variants = 3 and 4")


# ---------------------------------------------------------------------------
# Cycle filter (pure)
# ---------------------------------------------------------------------------

def test_cycle_allows():
    n = 120
    s = {"o": [100.0] * n, "h": [100.1] * n, "l": [99.9] * n,
         "c": [100.0] * n, "rsi": [50.0] * n, "ema": [100.0] * n,
         "atr": [1.0] * n}
    cfg = dict(crt_engine.DEFAULTS)
    p = 110
    assert cycle_allows(p, False, s, [], cfg) is True
    # candidate bar IS the spike candle -> blocked
    s["c"][p] = 90.0
    assert cycle_allows(p, False, s, [], cfg) is False
    s["c"][p] = 100.0
    # signal 2 bars ago -> cooldown blocks
    assert cycle_allows(p, False, s, [108], cfg) is False
    # signal 10 bars ago, same cycle -> max 1 per cycle blocks
    assert cycle_allows(p, False, s, [100], cfg) is False
    # ...but a spike at 105 starts a new cycle -> allowed again
    s["c"][105] = 90.0
    assert cycle_allows(p, False, s, [100], cfg) is True
    # spike too recent (108): only 1 drift bar between -> blocked
    s["c"][105] = 100.0
    s["c"][108] = 90.0
    assert cycle_allows(p, False, s, [], cfg) is False
    print("PASS cycle filter: spike/cooldown/per-cycle/min-drift gates")


# ---------------------------------------------------------------------------
# Engine end-to-end scenarios
# ---------------------------------------------------------------------------

def test_engine_sell_signal():
    candles = drift_candles(300, step=2.0)
    eng = CRTEngine(CFG_CRASH)
    sigs = eng.on_new_close(candles)
    assert len(sigs) == 1, sigs
    sig = sigs[0]
    last = candles[-1]
    assert sig["side"] == "SELL" and sig["score"] == 5, sig
    assert sig["entry"] == last["c"]
    assert abs(sig["sl"] - (last["h"] + 5 * 3.0)) < 1e-9, sig["sl"]
    # no bearish spikes in a pure up-drift -> TP falls back to RR mode
    assert abs(sig["tp"] - (sig["entry"] - (sig["sl"] - sig["entry"]) * 2.0)) < 1e-9
    print(f"PASS engine SELL: entry {sig['entry']} SL {sig['sl']} TP {sig['tp']}")


def test_engine_no_signal_sideways():
    candles = []
    for i in range(300):
        o, c = (1000.0, 1000.5) if i % 2 == 0 else (1000.5, 1000.0)
        candles.append({"t": T0 + i * 60, "o": o, "h": max(o, c) + 0.2,
                        "l": min(o, c) - 0.2, "c": c, "n": 30})
    eng = CRTEngine(CFG_CRASH)
    assert eng.on_new_close(candles) == []
    print("PASS engine sideways: no signal")


def test_engine_spike_candle_blocked():
    candles = drift_candles(300, step=2.0)
    prev_close = candles[-2]["c"]
    candles[-1] = {"t": candles[-1]["t"], "o": prev_close,
                   "h": prev_close + 0.5, "l": prev_close - 40.5,
                   "c": prev_close - 40.0, "n": 30}  # the crash candle itself
    eng = CRTEngine(CFG_CRASH)
    assert eng.on_new_close(candles) == []
    print("PASS engine: never signals on the spike candle itself")


def test_engine_max_one_per_cycle():
    candles = drift_candles(300, step=2.0)
    eng = CRTEngine(CFG_CRASH)
    assert len(eng.on_new_close(candles)) == 1
    candles.append(next_drift_candle(candles))
    assert eng.on_new_close(candles) == []  # same cycle: blocked
    print("PASS engine: max 1 signal per cycle enforced live")


def test_engine_buy_gating():
    candles = drift_candles(300, start=2000.0, step=-2.0)  # down-drift
    assert CRTEngine(CFG_CRASH).on_new_close(candles) == []  # crash = sell only
    sigs = CRTEngine({"asset_mode": "both"}).on_new_close(candles)
    assert len(sigs) == 1 and sigs[0]["side"] == "BUY", sigs
    last = candles[-1]
    assert abs(sigs[0]["sl"] - (last["l"] - 5 * 3.0)) < 1e-9
    print("PASS engine: BUY blocked in crash mode, fires in both mode")


def test_backfill_and_seed():
    b = structure.CandleBuilder(minutes=1, keep=1000)
    b.seed(drift_candles(300, step=2.0))
    assert len(b.closed) == 300
    eng = CRTEngine(CFG_CRASH)
    eng.backfill(b.closed)
    assert len(eng.signals) == 1, eng.signals  # first eligible bar only
    b.closed.append(next_drift_candle(b.closed))
    assert eng.on_new_close(b.closed) == []    # same cycle continues
    print("PASS backfill: history replay records exactly 1 signal")


def test_history_conversion():
    from watcher import Watcher
    out = Watcher._history_to_candles(
        [{"epoch": T0, "open": "100.5", "high": 101.0,
          "low": 99.5, "close": 100.8}])
    assert out == [{"t": T0, "o": 100.5, "h": 101.0, "l": 99.5,
                    "c": 100.8, "n": None}], out
    print("PASS history conversion: Deriv payload -> candle dicts")


def test_watcher_integration():
    import alerts
    import watcher as watcher_mod

    sent = []
    orig_send = alerts.send_alert
    alerts.send_alert = lambda text: sent.append(text) or True
    try:
        w = watcher_mod.Watcher()
        price = 1000.0
        for i in range(300):
            t = T0 + i * 60
            o, c = price, price + 2.0
            price = c
            w._on_tick(t, o)
            w._on_tick(t + 30, c)
        w._on_tick(T0 + 300 * 60, price)  # closes candle 299
        crt = [m for m in sent if "PRE-REVERSAL" in m]
        assert len(crt) == 1, f"expected 1 CRT alert, got {len(crt)}: {sent}"
        assert "PRE-REVERSAL SELL" in crt[0] and "Score: 5/5" in crt[0], crt[0]
        assert "SL:" in crt[0] and "TP:" in crt[0]
        print("PASS watcher integration: exactly 1 CRT alert from raw ticks")
        print("--- sample CRT alert ---")
        print(crt[0])
    finally:
        alerts.send_alert = orig_send


if __name__ == "__main__":
    test_indicators()
    test_score_full_and_gated()
    test_cycle_allows()
    test_engine_sell_signal()
    test_engine_no_signal_sideways()
    test_engine_spike_candle_blocked()
    test_engine_max_one_per_cycle()
    test_engine_buy_gating()
    test_backfill_and_seed()
    test_history_conversion()
    test_watcher_integration()
    print("ALL CRT ENGINE TESTS PASSED")
