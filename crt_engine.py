"""CRT Pre-Reversal Engine — faithful Python port of Megah's
CRT_PreReversal_Engine.mq5 (v8.10, Mindset Trader).

The MQL5 indicator works on series arrays (index 0 = forming bar,
1 = last closed bar, growing index = older bars). This port uses
oldest-first candle lists; a candidate at position p plays the role of
MQL bar i, and MQL bar i+k is position p-k here. All defaults match the
MQL input defaults exactly.

Logic (per closed candle, per allowed side):
  Exhaustion score — 5 conditions, signal needs score >= min_score (4):
    1. CRT sweep      : bar breaks the extreme of the previous
                        `range_bars` bars (low for buy / high for sell)
    2. Consecutive    : >= `min_consecutive` candles in a row in the
                        drift direction ending at the signal bar
    3. RSI extreme    : RSI <= rsi_level (buy) / >= 100-level (sell)
    4. Stretch        : close is >= stretch_atr x ATR away from EMA
                        (below for buy / above for sell)
    5. Move size      : total drop/rise over the last `drop_lookback`
                        bars >= drop_atr x ATR
  Spike-cycle filter (all must pass):
    - the signal bar itself is not a spike candle
      (body >= spike_atr_mult x previous bar's ATR, in the spike
      direction for that side: up-spikes gate buys, down-spikes sells)
    - at least `min_drift_bars` non-spike bars between the last spike
      and the signal bar
    - fewer than `max_signals_per_cycle` signals already fired in the
      current cycle (since the last spike), and none within
      `cooldown_bars` bars
  Risk levels:
    SL = signal bar extreme -/+ sl_atr_mult x ATR
    TP = entry +/- avg recent spike body x tp_spike_frac (avg_spike mode,
         up to 5 spikes within scan depth), else entry +/- risk x rr.

Asset modes mirror the MQL enum: "boom" = buy only, "crash" = sell only,
"both" = both, "auto" = detect from the symbol name (BOOM/CRASH).
"""

from __future__ import annotations

DEFAULTS = {
    "asset_mode": "crash",
    "symbol": "R_1000",
    "min_score": 4,
    "range_bars": 3,
    "min_consecutive": 3,
    "rsi_period": 14,
    "rsi_level": 30.0,
    "stretch_ema": 20,
    "stretch_atr": 1.5,
    "drop_lookback": 30,
    "drop_atr": 6.0,
    "spike_atr_mult": 3.0,
    "min_drift_bars": 2,
    "max_signals_per_cycle": 1,
    "cooldown_bars": 4,
    "spike_scan_depth": 200,
    "sl_atr_mult": 5.0,
    "tp_mode": "avg_spike",   # "avg_spike" | "rr"
    "tp_spike_frac": 0.8,
    "rr": 2.0,
}


# ---------------------------------------------------------------------------
# Indicator series (oldest-first, None until warmed up)
# ---------------------------------------------------------------------------


def ema_series(closes, period):
    """EMA seeded with the SMA of the first `period` closes (MT5 style)."""
    n = len(closes)
    out = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    out[period - 1] = e
    for i in range(period, n):
        e = closes[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def rsi_series(closes, period=14):
    """Wilder RSI. First value at index `period` (SMA-seeded averages)."""
    n = len(closes)
    out = [None] * n
    if period <= 0 or n <= period:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains[i] = d
        elif d < 0:
            losses[i] = -d
    avg_g = sum(gains[1:period + 1]) / period
    avg_l = sum(losses[1:period + 1]) / period

    def _rsi(ag, al):
        if al == 0.0:
            return 100.0 if ag > 0 else 50.0
        if ag == 0.0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    out[period] = _rsi(avg_g, avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i] = _rsi(avg_g, avg_l)
    return out


def atr_series(candles, period=14):
    """Wilder ATR(14), seeded with the SMA of the first `period` TRs."""
    n = len(candles)
    out = [None] * n
    if period <= 0 or n < period:
        return out
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
    out[period - 1] = a
    for i in range(period, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def build_series(candles, cfg):
    return {
        "o": [c["o"] for c in candles],
        "h": [c["h"] for c in candles],
        "l": [c["l"] for c in candles],
        "c": [c["c"] for c in candles],
        "rsi": rsi_series([c["c"] for c in candles], cfg["rsi_period"]),
        "ema": ema_series([c["c"] for c in candles], cfg["stretch_ema"]),
        "atr": atr_series(candles, 14),
    }


# ---------------------------------------------------------------------------
# Exhaustion score (port of ExhaustionScore)
# ---------------------------------------------------------------------------


def exhaustion_score(p, buy, s, cfg):
    """Score the candle at position p. Returns (score, conditions dict)."""
    O, H, L, C = s["o"], s["h"], s["l"], s["c"]
    rsi, ema, atr = s["rsi"], s["ema"], s["atr"]
    cond = {"sweep": False, "consecutive": False, "rsi": False,
            "stretch": False, "move": False}
    if p < 1 or atr[p] is None:
        return 0, cond

    # 1) CRT sweep: break of the previous `range_bars` bars' extreme.
    if buy:
        crl = L[p - 1]
        for r in range(2, cfg["range_bars"] + 1):
            if p - r >= 0:
                crl = min(crl, L[p - r])
        cond["sweep"] = L[p] < crl
    else:
        crh = H[p - 1]
        for r in range(2, cfg["range_bars"] + 1):
            if p - r >= 0:
                crh = max(crh, H[p - r])
        cond["sweep"] = H[p] > crh

    # 2) Consecutive candles in the drift direction, ending at p.
    cnt = 0
    limit = cfg["min_consecutive"] + 10
    for q in range(p, max(p - limit, -1), -1):
        in_drift = (C[q] < O[q]) if buy else (C[q] > O[q])
        if not in_drift:
            break
        cnt += 1
    cond["consecutive"] = cnt >= cfg["min_consecutive"]

    # 3) RSI extreme.
    if rsi[p] is not None:
        cond["rsi"] = (rsi[p] <= cfg["rsi_level"]) if buy \
            else (rsi[p] >= 100.0 - cfg["rsi_level"])

    # 4) Stretch from EMA, in ATR multiples.
    if ema[p] is not None and atr[p]:
        stretch = (ema[p] - C[p]) if buy else (C[p] - ema[p])
        cond["stretch"] = stretch >= cfg["stretch_atr"] * atr[p]

    # 5) Size of the recent one-way move, in ATR multiples.
    if atr[p]:
        back = cfg["drop_lookback"]
        if buy:
            hh = H[p - 1]
            for q in range(p - 2, p - back - 1, -1):
                if q < 0:
                    break
                hh = max(hh, H[q])
            cond["move"] = (hh - C[p]) >= cfg["drop_atr"] * atr[p]
        else:
            ll = L[p - 1]
            for q in range(p - 2, p - back - 1, -1):
                if q < 0:
                    break
                ll = min(ll, L[q])
            cond["move"] = (C[p] - ll) >= cfg["drop_atr"] * atr[p]

    return sum(1 for v in cond.values() if v), cond


def score_detail(candles, cfg, buy=False):
    """Dashboard detail for the LAST candle of `candles`: the score,
    the five condition booleans, and the numbers behind them.

    The booleans come from exhaustion_score itself, so this view can
    never disagree with the engine; the values are recomputed with the
    same formulas for display only.
    """
    out = {"score": 0,
           "conditions": {"sweep": False, "consecutive": False,
                          "rsi": False, "stretch": False, "move": False},
           "rsi": None, "ema": None, "atr": None, "consecutive": 0,
           "stretch_atr": None, "move_atr": None}
    if not candles:
        return out
    s = build_series(candles, cfg)
    p = len(candles) - 1
    if p < 1:
        return out
    score, cond = exhaustion_score(p, buy, s, cfg)
    out["score"] = score
    out["conditions"] = cond
    out["rsi"] = s["rsi"][p]
    out["ema"] = s["ema"][p]
    out["atr"] = s["atr"][p]
    C, O, H, L = s["c"], s["o"], s["h"], s["l"]
    cnt = 0
    limit = cfg["min_consecutive"] + 10
    for q in range(p, max(p - limit, -1), -1):
        in_drift = (C[q] < O[q]) if buy else (C[q] > O[q])
        if not in_drift:
            break
        cnt += 1
    out["consecutive"] = cnt
    if s["ema"][p] is not None and s["atr"][p]:
        stretch = (s["ema"][p] - C[p]) if buy else (C[p] - s["ema"][p])
        out["stretch_atr"] = stretch / s["atr"][p]
    if s["atr"][p]:
        back = cfg["drop_lookback"]
        if buy:
            hh = H[p - 1]
            for q in range(p - 2, p - back - 1, -1):
                if q < 0:
                    break
                hh = max(hh, H[q])
            out["move_atr"] = (hh - C[p]) / s["atr"][p]
        else:
            ll = L[p - 1]
            for q in range(p - 2, p - back - 1, -1):
                if q < 0:
                    break
                ll = min(ll, L[q])
            out["move_atr"] = (C[p] - ll) / s["atr"][p]
    return out


# ---------------------------------------------------------------------------
# Spike helpers + cycle filter (ports of IsSpike / LastSpikeShift /
# AvgSpikeSize / CycleAllows)
# ---------------------------------------------------------------------------


def is_spike(q, bullish, s, cfg):
    """Candle q is a spike if its body (in the spike direction) is at
    least spike_atr_mult x the PREVIOUS candle's ATR (MQL uses atr[idx+1])."""
    if q < 1:
        return False
    a = s["atr"][q - 1]
    if a is None or a <= 0:
        return False
    body = (s["c"][q] - s["o"][q]) if bullish else (s["o"][q] - s["c"][q])
    return body >= cfg["spike_atr_mult"] * a


def last_spike_pos(p, bullish, s, cfg):
    """Most recent spike position strictly older than p, within scan
    depth; None when no spike is found."""
    lo = max(1, p - cfg["spike_scan_depth"])
    for q in range(p - 1, lo - 1, -1):
        if is_spike(q, bullish, s, cfg):
            return q
    return None


def avg_spike_size(p, bullish, s, cfg):
    """Average body of up to 5 recent spikes older than p (0 = none)."""
    lo = max(1, p - cfg["spike_scan_depth"])
    total, cnt = 0.0, 0
    for q in range(p - 1, lo - 1, -1):
        if cnt >= 5:
            break
        if is_spike(q, bullish, s, cfg):
            total += (s["c"][q] - s["o"][q]) if bullish \
                else (s["o"][q] - s["c"][q])
            cnt += 1
    return total / cnt if cnt else 0.0


def cycle_allows(p, buy, s, signal_positions, cfg):
    """signal_positions: positions of previously recorded signals for
    this side (same candle indexing as s)."""
    if is_spike(p, buy, s, cfg):
        return False
    last = last_spike_pos(p, buy, s, cfg)
    boundary = last if last is not None \
        else max(1, p - cfg["spike_scan_depth"])
    if last is not None and (p - last - 1) < cfg["min_drift_bars"]:
        return False
    signals = 0
    for q in signal_positions:
        if boundary < q < p:
            if (p - q) <= cfg["cooldown_bars"]:
                return False
            signals += 1
    return signals < cfg["max_signals_per_cycle"]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CRTEngine:
    """Stateful engine: remembers fired signals (by candle epoch) so the
    cycle filter works across calls, exactly like the MQL buffers."""

    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.reset()

    def reset(self):
        self.signals = []            # [{"epoch": int, "side": "BUY"|"SELL"}]
        self._last_eval_epoch = None
        self.last_diag = {"buy_score": None, "sell_score": None,
                          "buy_conditions": None, "sell_conditions": None}

    @property
    def min_bars(self):
        """Bars that must be OLDER than a candidate before it is
        evaluated (mirrors the MQL g_min_bars computation)."""
        c = self.cfg
        depth = max(max(c["spike_scan_depth"], c["drop_lookback"]),
                    c["range_bars"] + c["min_consecutive"]) + 5
        return depth + max(c["stretch_ema"], c["rsi_period"] * 3) + 5

    def allowed_sides(self):
        mode = str(self.cfg["asset_mode"]).lower()
        if mode == "boom":
            return [True]
        if mode == "crash":
            return [False]
        if mode == "both":
            return [True, False]
        # auto: detect from symbol name, like the MQL AUTO mode
        sym = str(self.cfg["symbol"]).upper()
        if "BOOM" in sym:
            return [True]
        if "CRASH" in sym:
            return [False]
        return [True, False]

    def _signal_positions(self, candles, buy):
        idx = {c["t"]: i for i, c in enumerate(candles)}
        side = "BUY" if buy else "SELL"
        return [idx[s["epoch"]] for s in self.signals
                if s["side"] == side and s["epoch"] in idx]

    def _evaluate_at(self, candles, s, p, record):
        """Evaluate both allowed sides at position p. Returns the list of
        signal dicts fired (0, 1 or 2). Records them when record=True."""
        out = []
        if p < self.min_bars or p >= len(candles):
            return out
        if s["atr"][p] is None or s["atr"][p] <= 0:
            return out
        for buy in self.allowed_sides():
            score, cond = exhaustion_score(p, buy, s, self.cfg)
            key = "buy" if buy else "sell"
            self.last_diag[f"{key}_score"] = score
            self.last_diag[f"{key}_conditions"] = cond
            positions = self._signal_positions(candles, buy)
            if not cycle_allows(p, buy, s, positions, self.cfg):
                continue
            if score < self.cfg["min_score"]:
                continue
            sig = self._make_signal(candles, s, p, buy, score, cond)
            if record:
                self.signals.append({"epoch": candles[p]["t"],
                                     "side": sig["side"]})
            out.append(sig)
        return out

    def _make_signal(self, candles, s, p, buy, score, cond):
        C, H, L = s["c"], s["h"], s["l"]
        a = s["atr"][p]
        entry = C[p]
        if buy:
            sl = L[p] - self.cfg["sl_atr_mult"] * a
            avg = avg_spike_size(p, True, s, self.cfg)
            tp = entry + avg * self.cfg["tp_spike_frac"] \
                if (self.cfg["tp_mode"] == "avg_spike" and avg > 0) \
                else entry + (entry - sl) * self.cfg["rr"]
            side = "BUY"
        else:
            sl = H[p] + self.cfg["sl_atr_mult"] * a
            avg = avg_spike_size(p, False, s, self.cfg)
            tp = entry - avg * self.cfg["tp_spike_frac"] \
                if (self.cfg["tp_mode"] == "avg_spike" and avg > 0) \
                else entry - (sl - entry) * self.cfg["rr"]
            side = "SELL"
        return {
            "side": side,
            "epoch": candles[p]["t"],
            "bar_index": p,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "score": score,
            "conditions": cond,
            "atr": a,
        }

    def backfill(self, candles):
        """Replay history silently (records signals, alerts nothing) so
        the cycle filter knows where it is — like the MQL full recalc."""
        if len(candles) <= self.min_bars:
            return
        s = build_series(candles, self.cfg)
        for p in range(self.min_bars, len(candles)):
            self._evaluate_at(candles, s, p, record=True)
        self._last_eval_epoch = candles[-1]["t"]

    def on_new_close(self, candles):
        """Evaluate the just-closed (last) candle. Returns fired signals."""
        if not candles:
            return []
        epoch = candles[-1]["t"]
        if epoch == self._last_eval_epoch:
            return []
        self._last_eval_epoch = epoch
        p = len(candles) - 1
        if p < self.min_bars:
            return []
        s = build_series(candles, self.cfg)
        return self._evaluate_at(candles, s, p, record=True)
