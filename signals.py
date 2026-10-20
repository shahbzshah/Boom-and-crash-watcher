"""YOUR SIGNAL RULE LIVES HERE.

`crash_imminent(ticks, ctx)` is called on every new tick.
Return (True, "reason") when a crash is almost about to happen,
otherwise (False, "").

Arguments:
    ticks: list of (epoch, price) tuples, oldest first, newest last.
           Up to TICK_BUFFER entries (default 5000).
    ctx:   dict maintained by the watcher, always containing:
             Spike block
             - ticks_since_spike : int or None (None = no spike seen yet)
             - last_spike_price  : float or None
             - last_spike_epoch  : int or None
             - current_price     : float
             Structure block (refreshes on every candle close)
             - trend           : "uptrend" | "downtrend" | "ranging"
             - phase           : "accumulation" | "manipulation"
                                 | "distribution" | None
             - phase_direction : "bullish" | "bearish" | None
             - last_sweep      : {"side": "high"|"low", "level", "extreme",
                                  "index", "epoch"} | None
             - swing_high / swing_low : recent reference levels
             - range_high / range_low : accumulation boundaries (when in
                                        accumulation)
             - atr             : current ATR(14) of the candles
             - last_break      : {"type": "BOS"|"CHoCH",
                                  "direction": "bullish"|"bearish", ...}
             - candles_closed  : number of finished candles analysed
             VIDYA block (refreshes on every candle close)
             - vidya           : standard Variable Index Dynamic Average of the
                                 1m closes (period = VIDYA_PERIOD, default 9)
             - vidya_vol       : volume-modulated variant ("Volumatic"
                                 reconstruction: smoothing scaled by relative
                                 tick-count per candle); identical to `vidya`
                                 when volume data is flat/absent
             Volumatic block (refreshes on every candle close) — faithful
             reconstruction of the PUBLIC Volumatic VIDYA indicator spec
             (MQL5 Market product 139869, free): CMO-weighted VIDYA line with
             ATR bands; trend flips bullish when a close breaks above the
             upper band, bearish when a close breaks below the lower band.
             See volumatic.py for the full derivation and its assumptions.
             - vol_trend       : +1 bullish | -1 bearish | None (undecided)
             - vol_trend_bars  : candles since the last flip (0 = the flip
                                 happened on the just-closed candle)
             - vol_vidya / vol_upper / vol_lower : line and band values
             CRT block (refreshes on every candle close)
             - crt_buy_score / crt_sell_score : CRT exhaustion scores (0-5)
             - crt_last_signal : last CRT signal dict
                                 {"side", "entry", "sl", "tp", "score", ...}

Helpers below are yours to use inside your rule.
"""
from config import SPIKE_PCT


def detect_spike(prev_price, price, pct=SPIKE_PCT):
    """True when a single tick jumps UP by more than `pct` (fraction)."""
    if not prev_price or prev_price <= 0:
        return False
    return (price / prev_price - 1.0) >= pct


def drawdown_from_high(ticks, lookback=300):
    """Fraction price has fallen from the highest tick in the last
    `lookback` ticks. e.g. 0.02 means price is 2% below the recent high."""
    window = ticks[-lookback:]
    if not window:
        return 0.0
    high = max(p for _, p in window)
    last = window[-1][1]
    if high <= 0:
        return 0.0
    return (high - last) / high


def ticks_since(ticks, epoch):
    """How many ticks in the buffer are newer than `epoch`."""
    return sum(1 for e, _ in ticks if e > epoch)


# ---------------------------------------------------------------------------
# >>> EXTRA CUSTOM RULE (optional) <<<
# The live strategy is the CRT Pre-Reversal Engine in crt_engine.py — a
# faithful port of the CRT_PreReversal_Engine.mq5 source Megah sent. It
# is evaluated on every closed candle and sends its own Telegram alerts
# (with SL/TP), so this per-tick hook defaults to OFF to avoid double
# alerts. Use it only for extra conditions you want on top of CRT.
# (The earlier Volumatic-VIDYA reconstruction that lived here was a
# placeholder built while the .ex5 was unreadable; the VIDYA/Volumatic
# values are still computed into ctx above if you ever want them.)
# ---------------------------------------------------------------------------
def crash_imminent(ticks, ctx):
    """Extra per-tick rule, evaluated after the CRT engine. Return
    (True, "reason") to send an additional alert, else (False, "")."""
    return False, ""
