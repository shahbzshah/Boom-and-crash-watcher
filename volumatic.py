"""Volumatic VIDYA trend engine — reconstruction of the public spec.

The compiled `.ex5` strategy Megah sent ("Volumatic_VIDYA__1.ex5", v1.20,
described inside as an "Independent re-implementation") cannot be inspected —
its code section is encrypted. But "Volumatic VIDYA" is a PUBLIC, free
MetaTrader 5 indicator (MQL5 Market product 139869, by Cem Ummak), and its
documented behaviour is:

  1. A VIDYA line using CMO-based dynamic smoothing
     (Tushar Chande's Variable Index Dynamic Average).
  2. An upper and lower ATR band around that line, acting as breakout levels.
  3. A trend state defined by crossovers relative to the bands:
       close breaks above the upper band -> trend bullish (+1)
       close breaks below the lower band -> trend bearish (-1)
     otherwise the previous trend is kept. In bullish mode the lower band
     is the support zone; in bearish mode the upper band is resistance.

Note: despite the name, the public indicator is NOT volume-modulated — the
"volatility-adaptive" part is the ATR bands. (An earlier guess in vidya.py
treated "Volumatic" as volume-weighted; that variant is kept for
compatibility but is not what the public indicator does.)

This module implements exactly the documented behaviour above. Two things
it cannot know without the original inputs:

  * the exact parameter defaults of Megah's copy (CMO/VIDYA length, ATR
    period, ATR multiplier, price type) — all are configurable here;
  * whether his copy adds its own tweaks on top.

Defaults below (CMO 9, ATR 14, x2.0, close price) are standard choices, NOT
confirmed values. Verify against the MT5 chart: the engine's flip candles
should match the indicator's trend flips. A screenshot of the indicator's
Inputs tab closes the gap.

Crash 1000 reading (corrected 2026-10-20, confirmed by the CRT source
Megah sent): Crash indices drift UP slowly and crash DOWN in one violent
leg; Boom indices are the mirror (drift down, spike up). So on Crash
1000 a close breaking the UPPER band is just the up-drift accelerating —
it is the break back BELOW the lower band that marks the crash leg
starting. (An earlier version of this note had the drift backwards.)
"""

from vidya import vidya


def wilder_atr(highs, lows, closes, period):
    """Wilder's ATR. Returns list[float|None]; first `period`-1 entries None."""
    n = len(closes)
    out = [None] * n
    if n == 0 or period <= 0:
        return out
    trs = []
    for i in range(n):
        if i == 0:
            trs.append(highs[0] - lows[0])
        else:
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i - 1]),
                           abs(lows[i] - closes[i - 1])))
    if n < period:
        return out
    atr = sum(trs[:period]) / period
    out[period - 1] = atr
    for i in range(period, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def volumatic(candles, cmo_period=9, atr_period=14, atr_mult=2.0):
    """Volumatic VIDYA engine over closed candles.

    `candles`: list of dicts with "o"/"h"/"l"/"c" (oldest first).
    Returns a list of dicts, one per candle:
        vidya            - CMO-weighted VIDYA of the closes
        upper / lower    - vidya +/- atr_mult * ATR (None until ATR warms up)
        trend            - +1 bullish, -1 bearish, None while undecided
        bars_since_flip  - 0 on the flip bar, counts up after, None if no flip yet
    """
    n = len(candles)
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]

    v = vidya(closes, period=cmo_period) if n else []
    atr = wilder_atr(highs, lows, closes, atr_period)

    out = []
    trend = None
    last_flip = None
    for i in range(n):
        a = atr[i]
        if a is None:
            upper = lower = None
        else:
            upper = v[i] + atr_mult * a
            lower = v[i] - atr_mult * a

        if upper is None:
            new_trend = trend  # undecided until bands exist
        elif closes[i] > upper:
            new_trend = 1
        elif closes[i] < lower:
            new_trend = -1
        else:
            new_trend = trend

        if new_trend is not None and new_trend != trend:
            last_flip = i
            trend = new_trend

        out.append({
            "vidya": v[i] if n else None,
            "upper": upper,
            "lower": lower,
            "trend": trend,
            "bars_since_flip": (i - last_flip) if last_flip is not None else None,
        })
    return out
