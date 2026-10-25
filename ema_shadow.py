"""EMA-retest SHADOW alert tier (Dancan's proposal, backtested 2026-10-24).

A separate, clearly labelled tier that runs alongside the CRT engine
without touching it: it watches closed M1 candles for an EMA6 retest
inside a loose downtrend and fires SELL-only entries. It is a SHADOW
tier — alerts only, judged after 30 live days (drop it if live
expectancy is <= 0 on >= 20 trades), never a proven signal and never
a replacement for CRT or the confirmed tier.

The rule below is a faithful port of exactly ONE variant from the
offline backtest (~/workspace/research_notes/ema-retest-20261024/
ema_retest.py): downtrend def C, trigger 2 (EMA6 retest), SL = EMA91,
TP = 1.5R, spike exclusion on. No other variant is implemented here.

Backtest reference (frozen 44,000 M1 candles, 2026-09-21 -> 2026-10-22):
24 closed trades, 41.7% hit, +67.2 pts, expectancy +2.80/trade,
passing BOTH split halves (+3.17 / +2.54) — but n=24 and best-of-144,
so the live number will almost certainly shrink.

Porting notes (read before "optimising" this file):
  * The backtest precomputes EMA/ATR series over the whole dataset;
    this module updates them incrementally bar by bar. The formulas
    are causal (SMA-seeded EMA, Wilder ATR), so incremental values
    are IDENTICAL to the series values — see test_ema_shadow.py's
    parity test, which must keep passing.
  * Bars are 0-indexed in the order fed. Bars below index 365 are
    never evaluated (the backtest starts at START=365: EMA365 is
    first defined at index 364 and the pullback test needs the prior
    bar). Feed order must be chronological and gap-free; the watcher
    feeds each closed candle exactly once, and reseeds reset + refill
    the engine (see Watcher._seed_history).
  * One trade at a time: while a trade is open, bars are consumed by
    the outcome walk (SL checked before TP on a bar, as in the
    backtest) and no new setup can start. Scanning resumes on the
    bar AFTER the trade closes.
  * Outcome tracking of the ledger record is NOT this module's job:
    Watcher._track_outcomes closes records per tick. This module only
    gates its own scanning. Known tiny divergence, documented
    deliberately: the backtest resolves a same-bar TP/SL ambiguity to
    SL, while the live tick tracker closes on whichever level the
    first tick touches — so a live record's outcome can differ from
    the backtest's on bars that touch both levels.
"""

import crt_engine

# Best variant only: def C, trigger 2, sl=ema91, tp=1.5R, spike_excl=1.
START_INDEX = 365       # first evaluable bar (EMA365 defined at 364)
ATR_PERIOD = 14
SPIKE_MULT = 3.0        # down-spike: (open - close) >= 3 x prior ATR
TP_RR = 1.5             # TP = entry - 1.5 x (SL - entry)

SHADOW_JUDGMENT = ("SHADOW — judged after 30 live days; "
                   "drop if live expectancy <= 0 on >= 20 trades")


def is_down_spike(open_, close, prev_atr):
    """A down-spike bar, mirroring the backtest (and CRT's spike gate)."""
    return (prev_atr is not None and prev_atr > 0
            and open_ - close >= SPIKE_MULT * prev_atr)


def downtrend_c(close, ema91, ema365):
    """Loose downtrend (def C), checked at the entry bar."""
    return (ema91 is not None and ema365 is not None
            and close < ema91 and ema91 < ema365)


def ema_levels(entry, ema91):
    """(SL, TP) for an entry, or None when the stop is invalid.

    SL is the EMA91 level at entry; it must sit above the entry for a
    SELL. (For def C this cannot happen — downtrend already requires
    close < ema91 — but the guard stays, exactly as in the backtest.)
    """
    if ema91 is None or ema91 <= entry:
        return None
    risk = ema91 - entry
    return ema91, entry - TP_RR * risk


class EMAShadowEngine:
    """Incremental EMA-retest state machine over closed candles.

    feed(candle) -> entry dict or None. feed_many(candles) feeds
    silently (seed path: no caller-side alerts or records are made
    from its output) while carrying pullback / open-trade state
    forward. reset() drops all state (used on history reseed).
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._count = 0            # bars fed so far (next bar's index)
        self._closes = []          # closes, kept only until EMAs seed
        self._ema = {}             # period -> last EMA value or None
        self._ema_k = {p: 2.0 / (p + 1) for p in (6, 23, 91, 365)}
        self._trs = []             # true ranges, kept only until ATR seeds
        self._atr = None           # last ATR value or None
        self._prev_close = None    # previous bar's close
        self._prev_ema6 = None     # previous bar's EMA6
        self._state = "idle"       # idle | pullback
        self._pb_low = None        # pullback low (lowest low so far)
        self._open = None          # open trade {entry, sl, tp} or None

    # -- indicators (incremental, identical to crt_engine series) -----

    def _update_indicators(self, bar):
        """Advance EMA/ATR state with one closed candle. Returns
        (ema6, ema91, ema365, atr, prev_atr) for this bar."""
        prev_atr = self._atr
        c = bar["c"]
        if self._count < 365:
            self._closes.append(c)
        for p in (6, 23, 91, 365):
            if self._count < p - 1:
                continue
            if self._count == p - 1:
                self._ema[p] = sum(self._closes[:p]) / p
            else:
                k = self._ema_k[p]
                self._ema[p] = c * k + self._ema[p] * (1.0 - k)
        if self._count == 0:
            tr = bar["h"] - bar["l"]
        else:
            pc = self._prev_close
            tr = max(bar["h"] - bar["l"],
                     abs(bar["h"] - pc), abs(bar["l"] - pc))
        if self._count < ATR_PERIOD:
            self._trs.append(tr)
            if self._count == ATR_PERIOD - 1:
                self._atr = sum(self._trs) / ATR_PERIOD
        else:
            self._atr = (self._atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
        return (self._ema.get(6), self._ema.get(91),
                self._ema.get(365), self._atr, prev_atr)

    # -- main step ------------------------------------------------------

    def feed(self, bar):
        """Process one closed candle (dict with t/o/h/l/c). Returns an
        entry dict {epoch, entry, sl, tp, atr} when a shadow entry
        fires on this bar, else None."""
        idx = self._count
        ema6, ema91, ema365, atr, prev_atr = self._update_indicators(bar)
        prev_close, prev_ema6 = self._prev_close, self._prev_ema6
        self._prev_close, self._prev_ema6 = bar["c"], ema6
        self._count += 1
        if idx < START_INDEX:
            return None
        # Open trade: this bar is consumed by the outcome walk.
        if self._open is not None:
            if bar["h"] >= self._open["sl"]:
                self._open = None  # SL first, as in the backtest
            elif bar["l"] <= self._open["tp"]:
                self._open = None
            return None
        # Pullback state machine (trigger 2: EMA6 retest).
        if self._state == "idle":
            if (prev_close is not None and prev_ema6 is not None
                    and prev_close < prev_ema6
                    and bar["h"] >= ema6
                    and ema91 is not None and bar["c"] < ema91):
                self._state = "pullback"
                self._pb_low = bar["l"]
            return None
        # state == "pullback"
        if ema91 is not None and bar["c"] >= ema91:
            self._state = "idle"  # closed above EMA91: pullback void
            return None
        if bar["c"] < self._pb_low:
            entry = bar["c"]
        else:
            self._pb_low = min(self._pb_low, bar["l"])
            return None
        # ---- entry candidate ----
        self._state = "idle"
        if not downtrend_c(entry, ema91, ema365):
            return None
        if is_down_spike(bar["o"], entry, prev_atr):
            return None  # spike-bar entry skipped, setup voided
        levels = ema_levels(entry, ema91)
        if levels is None:
            return None
        sl, tp = levels
        self._open = {"entry": entry, "sl": sl, "tp": tp}
        return {"epoch": bar["t"], "entry": entry, "sl": sl, "tp": tp,
                "atr": atr}

    def feed_many(self, candles):
        """Feed many candles silently (history-seed path). Returns the
        number of entries that fired and were suppressed — the caller
        must NOT alert or record them; only the carried state matters."""
        suppressed = 0
        for bar in candles:
            if self.feed(bar) is not None:
                suppressed += 1
        return suppressed
