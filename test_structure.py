"""Offline tests for structure.py — crafted market scenarios.

Run:  python test_structure.py
"""
import math
import sys

sys.path.insert(0, ".")

import structure

CFG = {
    "swing_left": 3, "swing_right": 3, "trend_lookback": 120,
    "sweep_lookback": 20, "sweep_exclude": 5, "sweep_scan": 5,
    "accum_bars": 60, "accum_atr_mult": 2.5,
    "manip_bars": 10, "dist_bars": 15,
}


def mk(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def trend_series(up=True):
    """60 candles of ascending (or descending) waves with clear HH/HL."""
    p = []
    for w in range(6):
        start = 600 + 4 * w
        for off in (0, 2, 4, 5, 4, 3, 2, 1, 2, 3):
            p.append(start + off)
    if not up:
        p = [1200 - x for x in p]
    out = []
    for i in range(len(p) - 1):
        o, c = p[i], p[i + 1]
        # tiny index-based padding breaks exact ties deterministically
        out.append(mk(i * 60, o, max(o, c) + 1e-6 * i,
                      min(o, c) - 1e-6 * i, c))
    return out


def accum_series(n=70, phase=0.0):
    """Tight ping-pong range ~599.6..600.4 with slight noise so fractal
    swing highs exist (needed as BOS reference levels)."""
    out = []
    for i in range(n):
        hi = 599.75 if i % 2 == 0 else 600.25
        lo = 600.25 if i % 2 == 0 else 599.75
        o, c = hi, lo
        h = 600.4 + 0.01 * (i % 5) + phase
        l = 599.6 - 0.01 * (i % 7) + phase
        out.append(mk(i * 60, o, h, l, c))
    return out


def test_uptrend():
    r = structure.analyze(trend_series(up=True), CFG)
    assert r["trend"] == "uptrend", r["trend"]
    assert r["last_break"] and r["last_break"]["direction"] == "bullish", r["last_break"]
    print("PASS uptrend:", r["trend"], r["last_break"]["type"],
          r["last_break"]["direction"])


def test_downtrend():
    r = structure.analyze(trend_series(up=False), CFG)
    assert r["trend"] == "downtrend", r["trend"]
    assert r["last_break"] and r["last_break"]["direction"] == "bearish"
    print("PASS downtrend:", r["trend"], r["last_break"]["type"],
          r["last_break"]["direction"])


def test_accumulation():
    r = structure.analyze(accum_series(70), CFG)
    assert r["trend"] == "ranging", r["trend"]
    assert r["phase"] == "accumulation", r["phase"]
    assert r["range_high"] and r["range_low"]
    print("PASS accumulation: range %.2f-%.2f atr %.3f"
          % (r["range_low"], r["range_high"], r["atr"]))


def test_manipulation():
    candles = accum_series(70)
    t = 70 * 60
    # sweep of the lows: wick below range, close back inside
    candles.append(mk(t, 600.0, 600.3, 599.2, 600.1))
    r = structure.analyze(candles, CFG)
    assert r["last_sweep"] and r["last_sweep"]["side"] == "low", r["last_sweep"]
    assert r["phase"] == "manipulation", r["phase"]
    assert r["phase_direction"] == "bullish", r["phase_direction"]
    print("PASS manipulation: sweep-low @%.2f -> %s %s"
          % (r["last_sweep"]["level"], r["phase"], r["phase_direction"]))


def test_distribution():
    candles = accum_series(70)
    t = 70 * 60
    candles.append(mk(t, 600.0, 600.3, 599.2, 600.1))          # sweep lows
    for k in range(1, 4):                                      # bullish expansion
        candles.append(mk(t + k * 60, 600.2, 601.6, 600.1, 601.5))
    r = structure.analyze(candles, CFG)
    assert r["phase"] == "distribution", (r["phase"], r["last_break"])
    assert r["phase_direction"] == "bullish", r["phase_direction"]
    assert r["trend"] == "uptrend", r["trend"]
    print("PASS distribution: BOS %s -> %s %s"
          % (r["last_break"]["type"], r["phase"], r["phase_direction"]))


def test_manipulation_expires():
    candles = accum_series(70)
    t = 70 * 60
    candles.append(mk(t, 600.0, 600.3, 599.2, 600.1))          # sweep lows
    candles += accum_series(15, phase=0.0)                     # ...fizzles out
    for i, c in enumerate(candles[-15:]):
        c["t"] = (71 + i) * 60
    r = structure.analyze(candles, CFG)
    assert r["phase"] == "accumulation", r["phase"]
    print("PASS manipulation expiry -> back to accumulation")


def test_candle_builder():
    b = structure.CandleBuilder(minutes=1, keep=10)
    closed = []
    # 150 ticks, 2s apart = 300s = 5 buckets; price rising
    for k in range(150):
        c = b.on_tick(1_700_000_000 + k * 2, 600.0 + k * 0.1)
        if c:
            closed.append(c)
    assert len(closed) == 5, len(closed)  # 6 buckets touched, last still forming
    assert closed[0]["o"] == 600.0 and closed[0]["c"] > closed[0]["o"]
    assert all(c["h"] >= c["l"] for c in closed)
    print("PASS candle builder: %d candles closed, rising OHLC ok" % len(closed))


def test_watcher_ctx_integration():
    import config
    import watcher as watcher_mod

    config.ALERT_COOLDOWN_SEC = 0
    import telegram_alerts
    telegram_alerts.send_alert = lambda text: True

    seen = {}

    import signals
    orig = signals.crash_imminent
    signals.crash_imminent = lambda ticks, ctx: (
        seen.update(ctx) or (False, ""))

    try:
        w = watcher_mod.Watcher()
        for k in range(900):  # 30 minutes of ticks (M5: 6 candles)
            w._on_tick(1_700_000_000 + k * 2, 600.0 + k * 0.05)
        assert seen["candles_closed"] > 5, seen["candles_closed"]
        assert seen["trend"] in ("uptrend", "downtrend", "ranging")
        assert "phase" in seen and "last_sweep" in seen
        print("PASS watcher ctx: %d candles, trend=%s phase=%s"
              % (seen["candles_closed"], seen["trend"], seen["phase"]))
    finally:
        signals.crash_imminent = orig


if __name__ == "__main__":
    test_uptrend()
    test_downtrend()
    test_accumulation()
    test_manipulation()
    test_distribution()
    test_manipulation_expires()
    test_candle_builder()
    test_watcher_ctx_integration()
    print("ALL STRUCTURE TESTS PASSED")
