"""Market-structure engine for the Crash 1000 alert bot.

Builds candles from ticks, then derives the three blocks Megah asked for:

1. Trend bias   — "uptrend" | "downtrend" | "ranging"
   (fractal swings -> HH/HL sequence, refined by BOS/CHoCH walk)
2. AMD phase     — "accumulation" | "manipulation" | "distribution" | None
   accumulation : price compressed in a tight range (range < k * ATR)
   manipulation : a liquidity sweep fired recently, no expansion after it yet
   distribution : a BOS after the sweep, in the opposite direction of the sweep
3. Liquidity sweeps — wick beyond a recent swing level with the close back
   inside (classic stop-hunt). Each sweep: side, level, extreme, time.

All functions are pure (no I/O, no clock reads) so they are unit-testable.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Tick -> candles
# ---------------------------------------------------------------------------


class CandleBuilder:
    """Aggregates ticks into fixed-length candles.

    Call on_tick(epoch, price) for every tick. Returns the candle that just
    closed (dict with t/o/h/l/c/n, where n = tick count in that candle),
    or None when the current candle is still forming. `closed` holds the
    most recent finished candles.
    """

    def __init__(self, minutes=1, keep=400):
        self.tf = int(minutes) * 60
        self.keep = int(keep)
        self.closed: list[dict] = []
        self.cur: dict | None = None

    def on_tick(self, epoch, price):
        bucket = (int(epoch) // self.tf) * self.tf
        if self.cur is None or self.cur["t"] != bucket:
            finished = self.cur
            if finished is not None:
                self.closed.append(finished)
                if len(self.closed) > self.keep:
                    self.closed = self.closed[-self.keep:]
            self.cur = {"t": bucket, "o": price, "h": price,
                        "l": price, "c": price, "n": 1}
            return finished
        self.cur["h"] = max(self.cur["h"], price)
        self.cur["l"] = min(self.cur["l"], price)
        self.cur["c"] = price
        self.cur["n"] += 1
        return None

    def seed(self, candles):
        """Load historical candles (oldest first) as the closed list.

        Each item needs t/o/h/l/c ("n" optional). Duplicated timestamps
        keep the last copy; the result is trimmed to `keep` candles and
        any forming candle is cleared (live ticks rebuild it)."""
        by_t = {}
        for c in candles:
            by_t[int(c["t"])] = {
                "t": int(c["t"]),
                "o": float(c["o"]), "h": float(c["h"]),
                "l": float(c["l"]), "c": float(c["c"]),
                "n": c.get("n"),
            }
        self.closed = [by_t[t] for t in sorted(by_t)][-self.keep:]
        self.cur = None


# ---------------------------------------------------------------------------
# Indicator primitives (hand-rolled, no dependencies)
# ---------------------------------------------------------------------------


def atr(candles, period=14):
    """Wilder ATR over candle dicts."""
    n = len(candles)
    if n < period or period <= 0:
        return None
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c["h"] - c["l"])
        else:
            pc = candles[i - 1]["c"]
            trs.append(max(c["h"] - c["l"],
                           abs(c["h"] - pc),
                           abs(c["l"] - pc)))
    a = sum(trs[:period]) / period
    for t in trs[period:]:
        a = (a * (period - 1) + t) / period
    return a


# ---------------------------------------------------------------------------
# Swings + trend (BOS / CHoCH)
# ---------------------------------------------------------------------------


def fractal_swings(candles, left=3, right=3, lookback=120):
    """Fractal swing points. Bar i is a swing high when high[i] is the
    unique max of high[i-left .. i+right] (mirror for lows)."""
    n = len(candles)
    if n < left + right + 1:
        return []
    out = []
    start = max(0, n - lookback)
    for i in range(max(start, left), n - right):
        h, l = candles[i]["h"], candles[i]["l"]
        wh = [candles[j]["h"] for j in range(i - left, i + right + 1)]
        wl = [candles[j]["l"] for j in range(i - left, i + right + 1)]
        if h >= max(wh) and wh.count(h) == 1:
            out.append({"type": "high", "price": h, "index": i,
                        "epoch": candles[i]["t"]})
        if l <= min(wl) and wl.count(l) == 1:
            out.append({"type": "low", "price": l, "index": i,
                        "epoch": candles[i]["t"]})
    out.sort(key=lambda s: s["index"])
    return out


def detect_trend(candles, left=3, right=3, lookback=120):
    """Market-structure trend.

    Returns {"trend": "uptrend"|"downtrend"|"ranging",
             "swings": [...], "last_break": {...}|None,
             "swing_high": float|None, "swing_low": float|None}
    """
    n = len(candles)
    empty = {"trend": "ranging", "swings": [], "last_break": None,
             "swing_high": None, "swing_low": None}
    if n < left + right + 3:
        return empty

    closes = [c["c"] for c in candles]
    swings = fractal_swings(candles, left, right, lookback)
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]

    def seq_trend():
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[-1]["price"] > highs[-2]["price"]
            hl = lows[-1]["price"] > lows[-2]["price"]
            lh = highs[-1]["price"] < highs[-2]["price"]
            ll = lows[-1]["price"] < lows[-2]["price"]
            if hh and hl:
                return "uptrend"
            if lh and ll:
                return "downtrend"
        return "ranging"

    trend = seq_trend()
    last_break = None
    start = max(1, n - lookback)
    for i in range(start, n):
        ref_h = next((s for s in reversed(highs) if s["index"] < i), None)
        ref_l = next((s for s in reversed(lows) if s["index"] < i), None)
        c_now, c_prev = closes[i], closes[i - 1]
        direction = None
        if ref_h is not None and c_now > ref_h["price"] >= c_prev:
            direction = "bullish"
        elif ref_l is not None and c_now < ref_l["price"] <= c_prev:
            direction = "bearish"
        if direction is None:
            continue
        if trend == "ranging":
            kind = "BOS"
        else:
            with_trend = (direction == "bullish") == (trend == "uptrend")
            kind = "BOS" if with_trend else "CHoCH"
        last_break = {"type": kind, "direction": direction, "index": i,
                      "price": c_now, "epoch": candles[i]["t"]}
        trend = "uptrend" if direction == "bullish" else "downtrend"

    return {
        "trend": trend,
        "swings": swings,
        "last_break": last_break,
        "swing_high": highs[-1]["price"] if highs else None,
        "swing_low": lows[-1]["price"] if lows else None,
    }


# ---------------------------------------------------------------------------
# Liquidity sweeps
# ---------------------------------------------------------------------------


def detect_sweeps(candles, lookback=20, exclude_last=5, scan=5):
    """Find stop-hunt sweeps in the last `scan` candles.

    Reference levels = highest high / lowest low over `lookback` candles,
    excluding the most recent `exclude_last` (the level must pre-date the
    bars being tested). A sweep: wick beyond the level, close back inside.
    """
    n = len(candles)
    if n <= exclude_last:
        return []
    ref = candles[max(0, n - exclude_last - lookback): n - exclude_last]
    if not ref:
        return []
    sh = max(c["h"] for c in ref)
    sl = min(c["l"] for c in ref)
    out = []
    for k in range(max(0, n - scan), n):
        c = candles[k]
        if c["h"] > sh and c["c"] < sh:
            out.append({"side": "high", "level": sh, "extreme": c["h"],
                        "index": k, "epoch": c["t"]})
        if c["l"] < sl and c["c"] > sl:
            out.append({"side": "low", "level": sl, "extreme": c["l"],
                        "index": k, "epoch": c["t"]})
    return out


# ---------------------------------------------------------------------------
# AMD phase: accumulation -> manipulation -> distribution
# ---------------------------------------------------------------------------


def classify_phase(candles, sweeps, last_break, atr_value,
                   accum_bars=60, accum_atr_mult=2.5,
                   manip_bars=10, dist_bars=15):
    """Classify the current Smart-Money cycle phase.

    Priority (most recent event wins):
      distribution : a BOS after the last sweep, opposite to the sweep side
      manipulation : a sweep within `manip_bars` candles, no BOS after it
      accumulation : last `accum_bars` candles compressed
                     (range <= accum_atr_mult * ATR)
      None         : no clear phase

    Returns {"phase", "phase_direction", "range_high", "range_low",
             "last_sweep"}.
    phase_direction is the anticipated distribution direction:
      manipulation after a sell-side ("low") sweep -> "bullish"
      manipulation after a buy-side ("high") sweep -> "bearish"
      distribution -> the BOS direction; accumulation -> None.
    """
    n = len(candles)
    result = {"phase": None, "phase_direction": None,
              "range_high": None, "range_low": None,
              "last_sweep": sweeps[-1] if sweeps else None}

    last_sweep = result["last_sweep"]
    if last_sweep is not None:
        since_sweep = (n - 1) - last_sweep["index"]
        bos_after = (last_break is not None
                     and last_break["index"] > last_sweep["index"]
                     and last_break["type"] == "BOS")
        expected = "bearish" if last_sweep["side"] == "high" else "bullish"
        if bos_after and last_break["direction"] == expected:
            if (n - 1) - last_break["index"] <= dist_bars:
                result["phase"] = "distribution"
                result["phase_direction"] = last_break["direction"]
                return result
        if since_sweep <= manip_bars:
            result["phase"] = "manipulation"
            result["phase_direction"] = expected
            return result

    if n >= accum_bars and atr_value and atr_value > 0:
        window = candles[-accum_bars:]
        rh = max(c["h"] for c in window)
        rl = min(c["l"] for c in window)
        if (rh - rl) <= accum_atr_mult * atr_value:
            result["phase"] = "accumulation"
            result["range_high"] = rh
            result["range_low"] = rl
    return result


# ---------------------------------------------------------------------------
# Combined analysis -> feeds the signal rule's ctx
# ---------------------------------------------------------------------------


def analyze(candles, cfg):
    """Full structure analysis over closed candles. Returns the dict that
    is merged into the signal rule's `ctx`."""
    n = len(candles)
    if n < 20:
        return {"trend": "ranging", "phase": None, "phase_direction": None,
                "last_sweep": None, "swing_high": None, "swing_low": None,
                "range_high": None, "range_low": None, "atr": None,
                "last_break": None, "candles_closed": n}

    t = detect_trend(candles, cfg["swing_left"], cfg["swing_right"],
                     cfg["trend_lookback"])
    sweeps = detect_sweeps(candles, cfg["sweep_lookback"],
                           cfg["sweep_exclude"], cfg["sweep_scan"])
    a = atr(candles, 14)
    phase = classify_phase(candles, sweeps, t["last_break"], a,
                           cfg["accum_bars"], cfg["accum_atr_mult"],
                           cfg["manip_bars"], cfg["dist_bars"])
    return {
        "trend": t["trend"],
        "phase": phase["phase"],
        "phase_direction": phase["phase_direction"],
        "last_sweep": phase["last_sweep"],
        "swing_high": t["swing_high"],
        "swing_low": t["swing_low"],
        "range_high": phase["range_high"],
        "range_low": phase["range_low"],
        "atr": round(a, 5) if a else None,
        "last_break": t["last_break"],
        "candles_closed": n,
    }
