"""Tests for volumatic.py — hand-computed expectations, not just smoke."""
import volumatic
from volumatic import volumatic as vol, wilder_atr


def C(o, h, l, c):
    return {"o": o, "h": h, "l": l, "c": c}


def _close(a, b, tol=1e-9):
    return abs(a - b) < tol


# ---------------------------------------------------------------------------
# Band math: fully hand-computed (cmo_period=3 -> sc=0.5, atr_period=2).
# closes [10,11,12,13] -> vidya [10, 10.5, 11.25, 12.125]
# TRs [1.0, 1.0, 1.5, 1.5] -> Wilder ATR(2) [None, 1.0, 1.25, 1.375]
# mult=2 -> bands vidya +/- 2*ATR
# ---------------------------------------------------------------------------
FLAT4 = [C(10, 10.5, 9.5, 10), C(10, 11, 10, 11),
         C(11, 12, 10.5, 12), C(12, 13, 11.5, 13)]


def test_hand_computed_bands():
    out = vol(FLAT4, cmo_period=3, atr_period=2, atr_mult=2.0)
    assert out[0]["upper"] is None and out[0]["lower"] is None  # ATR warming up
    assert _close(out[1]["upper"], 12.5) and _close(out[1]["lower"], 8.5)
    assert _close(out[2]["upper"], 13.75) and _close(out[2]["lower"], 8.75)
    assert _close(out[3]["upper"], 14.875) and _close(out[3]["lower"], 9.375)
    assert _close(out[3]["vidya"], 12.125)


def test_bands_symmetric_around_vidya():
    out = vol(FLAT4, cmo_period=3, atr_period=2, atr_mult=2.0)
    for b in out[1:]:
        assert _close(b["upper"] - b["vidya"], b["vidya"] - b["lower"])
        assert _close(b["upper"] - b["lower"], 2 * 2.0 * (b["upper"] - b["vidya"]) / 2)


def test_flat_market_never_flips():
    flat = [C(100, 100.1, 99.9, 100) for _ in range(30)]
    out = vol(flat, cmo_period=5, atr_period=5, atr_mult=2.0)
    assert all(b["trend"] is None for b in out)
    assert all(b["bars_since_flip"] is None for b in out)


def test_wilder_atr_hand_computed():
    highs = [10.5, 11, 12, 13]
    lows = [9.5, 10, 10.5, 11.5]
    closes = [10, 11, 12, 13]
    atr = wilder_atr(highs, lows, closes, 2)
    assert atr[0] is None
    assert _close(atr[1], 1.0)    # SMA of first 2 TRs
    assert _close(atr[2], 1.25)   # (1.0*1 + 1.5)/2
    assert _close(atr[3], 1.375)  # (1.25*1 + 1.5)/2


# ---------------------------------------------------------------------------
# Crash 1000 shape: flat drift -> spike -> crash.
# Verified behaviour (locked in): bullish flip on the first spike candle
# (close 103 > upper 102.8), bearish flip on the crash candle
# (close 94 < lower 94.85). bars_since_flip is 0 on flip bars.
# ---------------------------------------------------------------------------
def _crash1000_shape():
    candles = [C(100, 100.1, 99.9, 100) for _ in range(14)]
    candles += [C(100, 103.5, 99.8, 103), C(103, 107.5, 102.5, 107),
                C(107, 111, 106.5, 110.5)]
    candles += [C(110.5, 111, 105, 106), C(106, 106.5, 99, 100),
                C(100, 100.5, 93, 94)]
    return candles, vol(candles, cmo_period=5, atr_period=5, atr_mult=2.0)


def test_spike_flips_bullish_then_crash_flips_bearish():
    candles, out = _crash1000_shape()
    flips = [(i, b["trend"]) for i, b in enumerate(out)
             if b["bars_since_flip"] == 0]
    assert flips[0] == (14, 1), flips    # spike candle flips bullish
    assert flips[1] == (19, -1), flips   # crash candle flips bearish
    assert len(flips) == 2


def test_flip_bar_close_is_outside_its_band():
    candles, out = _crash1000_shape()
    for i, b in enumerate(out):
        if b["bars_since_flip"] == 0:
            if b["trend"] == 1:
                assert candles[i]["c"] > b["upper"]
            else:
                assert candles[i]["c"] < b["lower"]


def test_bars_since_flip_counts_up_between_flips():
    _, out = _crash1000_shape()
    # between flip at 14 and flip at 19: 1,2,3,4
    assert [out[i]["bars_since_flip"] for i in range(14, 20)] == [0, 1, 2, 3, 4, 0]
    # trend held across the whole stretch
    assert [out[i]["trend"] for i in range(14, 19)] == [1] * 5
    assert out[19]["trend"] == -1


def test_trend_holds_inside_bands():
    # closes inside the bands never change the trend
    candles = [C(100, 100.1, 99.9, 100) for _ in range(14)]
    candles.append(C(100, 101.5, 99.5, 100.5))  # small pop, inside bands
    out = vol(candles, cmo_period=5, atr_period=5, atr_mult=2.0)
    assert out[-1]["trend"] is None


def test_empty_input():
    assert vol([]) == []


# ---------------------------------------------------------------------------
# Signal rule: crash_imminent fires only on a FRESH bearish flip
# with a spike seen this cycle.
# ---------------------------------------------------------------------------
def _ctx(**kw):
    base = {"vol_trend": -1, "vol_trend_bars": 0, "ticks_since_spike": 850,
            "current_price": 94.0}
    base.update(kw)
    return base


def test_signal_hook_inert_since_crt_took_over():
    # Since 2026-10-20 the live strategy is the CRT Pre-Reversal Engine
    # (crt_engine.py); signals.crash_imminent is an optional extra hook
    # that defaults to OFF, so even a fresh bearish Volumatic flip must
    # NOT fire it (no double alerts). CRT behaviour is covered by
    # test_crt_engine.py.
    import signals
    fire, _ = signals.crash_imminent([], _ctx())
    assert fire is False


def test_signal_silent_when_flip_is_stale():
    import signals
    fire, _ = signals.crash_imminent([], _ctx(vol_trend_bars=2))
    assert fire is False


def test_signal_silent_without_spike():
    import signals
    fire, _ = signals.crash_imminent([], _ctx(ticks_since_spike=None))
    assert fire is False


def test_signal_silent_on_bullish_flip():
    import signals
    fire, _ = signals.crash_imminent([], _ctx(vol_trend=1, vol_trend_bars=0))
    assert fire is False


def test_signal_silent_before_any_flip():
    import signals
    fire, _ = signals.crash_imminent(
        [], _ctx(vol_trend=None, vol_trend_bars=None))
    assert fire is False
