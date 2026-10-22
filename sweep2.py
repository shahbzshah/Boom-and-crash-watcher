"""Second sweep stage: the research-derived variants.

Adds, on top of sweep.py's validated baseline features:
  * ADX(14) regime feature + gates
  * Kaufman ER percentile + ER-falling (efficiency veto)
  * premium-zone location (close in top decile of trailing 240 bars)
  * confirmation entries (bearish displacement / rejection wick) —
    these change the entry, so outcomes are re-resolved
  * one-bet-per-episode sequencing + post-loss cooldown

Everything is still judged split-half (see sweep.py docstring).
"""
import bisect
import sys
import time
from datetime import datetime

import backtest
import crt_engine
import sweep
from sweep import show

EAT = sweep.EAT


# ---------------------------------------------------------------------------
# Extra causal series
# ---------------------------------------------------------------------------

def adx_series(candles, period=14):
    n = len(candles)
    tr = [0.0] * n
    pdm = [0.0] * n
    ndm = [0.0] * n
    for i in range(1, n):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        up = h - candles[i - 1]["h"]
        dn = candles[i - 1]["l"] - l
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    def smooth(x):
        out = [None] * n
        if n <= period:
            return out
        acc = sum(x[1:period + 1])
        out[period] = acc
        for i in range(period + 1, n):
            acc = acc - acc / period + x[i]
            out[i] = acc
        return out

    str_, spdm, sndm = smooth(tr), smooth(pdm), smooth(ndm)
    dx = [None] * n
    for i in range(period, n):
        if str_[i]:
            pdi = 100 * spdm[i] / str_[i]
            ndi = 100 * sndm[i] / str_[i]
            dx[i] = (100 * abs(pdi - ndi) / (pdi + ndi)
                     if (pdi + ndi) else 0.0)
    adx = [None] * n
    start = 2 * period
    if n > start:
        acc = sum(v for v in dx[period:start] if v is not None) / period
        adx[start - 1] = acc
        for i in range(start, n):
            if dx[i] is not None:
                acc = (acc * (period - 1) + dx[i]) / period
                adx[i] = acc
    return adx


def add_features(candles, s, feats):
    adx = adx_series(candles)
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    er = [f["er20"] for f in feats]  # placeholder, real ER computed below
    er_full = [None] * len(candles)
    for p in range(20, len(candles)):
        change = abs(closes[p] - closes[p - 20])
        vol = sum(abs(closes[i] - closes[i - 1])
                  for i in range(p - 19, p + 1))
        er_full[p] = (change / vol) if vol else 0.0
    for f in feats:
        p = f["pos"]
        f["adx"] = adx[p]
        past = [v for v in er_full[max(0, p - 1000):p] if v is not None]
        f["er_pctile"] = (sum(1 for v in past if v <= er_full[p])
                          / len(past)) if past and er_full[p] is not None \
            else None
        f["er_falling"] = (er_full[p] is not None and er_full[p - 3]
                           is not None and er_full[p] < er_full[p - 3])
        if p >= 240:
            hi = max(highs[p - 240:p])
            lo = min(lows[p - 240:p])
            f["pos240"] = ((closes[p] - lo) / (hi - lo)
                           if hi > lo else None)
        else:
            f["pos240"] = None
    return feats


# ---------------------------------------------------------------------------
# Confirmation entries (entry changes -> outcomes re-resolved)
# ---------------------------------------------------------------------------

def confirm_variants(candles, feats, mode):
    out = []
    for f in feats:
        p, sig = f["pos"], f["sig"]
        dist_tp = sig["entry"] - sig["tp"]
        if mode == "displace":
            entry_pos = None
            for q in range(p + 1, min(p + 4, len(candles))):
                if candles[q]["h"] >= sig["sl"]:
                    entry_pos = -1  # ran to the stop before confirming
                    break
                if (candles[q]["c"] < candles[q]["o"]
                        and candles[q]["c"] < candles[p]["l"]):
                    entry_pos = q
                    break
            if not entry_pos or entry_pos < 0:
                continue
        elif mode == "reject":
            q = p + 1
            if q >= len(candles):
                continue
            level = max(candles[r]["h"] for r in range(p - 3, p))
            c = candles[q]
            rng = c["h"] - c["l"]
            wick = c["h"] - max(c["o"], c["c"])
            if not (rng > 0 and wick >= 0.5 * rng and c["c"] < level):
                continue
            entry_pos = q
        else:
            raise ValueError(mode)
        entry = candles[entry_pos]["c"]
        new_sig = dict(sig)
        new_sig["entry"] = entry
        new_sig["tp"] = entry - dist_tp
        res = backtest.resolve_outcome(candles, entry_pos, new_sig)
        nf = dict(f)
        nf.update(res)
        nf["sig"] = new_sig
        out.append(nf)
    return out


# ---------------------------------------------------------------------------
# Episode sequencing
# ---------------------------------------------------------------------------

def episode_filter(candles, s, feats, max_bars=60, loss_cooldown_min=0):
    accepted = []
    last = None  # (pos, entry, exit_epoch)
    closes = [c["c"] for c in candles]
    lows = [c["l"] for c in candles]
    for f in feats:
        if last is not None:
            lp, lentry, lexit = last
            p = f["pos"]
            reset = False
            for q in range(lp + 1, p + 1):
                if (s["ema"][q] is not None and closes[q] <= s["ema"][q]) \
                        or (s["rsi"][q] is not None and s["rsi"][q] < 50) \
                        or lows[q] <= lentry - 20:
                    reset = True
                    break
            if not reset and p - lp <= max_bars:
                continue
            if loss_cooldown_min and lexit is not None:
                if f["sig"]["epoch"] - lexit < loss_cooldown_min * 60:
                    continue
        accepted.append(f)
        exit_epoch = None
        if f["outcome"] in ("tp", "sl"):
            # exit bar epoch: entry epoch + duration
            exit_epoch = f["sig"]["epoch"] + (f["duration_min"] or 0) * 60
        last = (f["pos"], f["sig"]["entry"], exit_epoch)
    return accepted


if __name__ == "__main__":
    t0 = time.time()
    candles = sweep.load_candles()
    cfg = backtest.watcher_cfg()
    cfg["symbol"] = "CRASH1000"
    replay = sweep.fast_replay(candles, cfg)
    s = crt_engine.build_series(candles, cfg)
    feats = sweep.with_outcomes(candles,
                                sweep.features_for(candles, s, replay))
    feats = add_features(candles, s, feats)
    split = candles[len(candles) // 2]["t"]
    print(f"{len(feats)} baseline signals; split "
          f"{datetime.fromtimestamp(split, tz=EAT)} EAT\n")

    F = feats
    variants = [
        ("ADX < 20 (ranging only)",
         [f for f in F if f["adx"] is not None and f["adx"] < 20]),
        ("ADX < 25",
         [f for f in F if f["adx"] is not None and f["adx"] < 25]),
        ("ER not top-quintile (veto cleanest)",
         [f for f in F if f["er_pctile"] is not None
          and f["er_pctile"] < 0.8]),
        ("ER falling (efficiency rolling over)",
         [f for f in F if f["er_falling"]]),
        ("premium zone (pos240 >= 0.90)",
         [f for f in F if f["pos240"] is not None and f["pos240"] >= 0.90]),
        ("RSI >= 85", [f for f in F if (f["rsi"] or 0) >= 85]),
        ("confirm: bearish displacement <=3 bars",
         confirm_variants(candles, F, "displace")),
        ("confirm: rejection wick next bar",
         confirm_variants(candles, F, "reject")),
        ("episode: one bet per drift episode",
         episode_filter(candles, s, F)),
        ("episode + 45m loss cooldown",
         episode_filter(candles, s, F, loss_cooldown_min=45)),
    ]
    for name, rows in variants:
        show(name, rows, split)
    print(f"\n({time.time() - t0:.1f}s)")
