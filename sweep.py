"""Win-rate sweep for the CRT engine: fast replay + filter variants.

The live engine's backfill is O(n^2) over 44k bars (it rebuilds an
epoch->index dict per bar), which makes parameter sweeps impractical.
fast_replay below calls the SAME crt_engine functions (exhaustion_score,
cycle_allows, _make_signal) with incrementally maintained signal
positions, and is validated to reproduce backfill's signal set exactly.

Methodology against self-deception:
  * Signals carry only causal features (nothing computed from the
    future). The one future-aware field, drop60, is kept for analysis
    segmentation ONLY and is never used as a filter.
  * Every variant is scored on the first half and the second half of
    the sample separately. A variant is only interesting if it beats
    the baseline in BOTH halves (in-sample fit that fails out-of-sample
    is curve-fitting, not an edge).

Usage:
    python sweep.py --fetch      # (re)download + cache 30d of M1 candles
    python sweep.py              # baseline + single-filter variants
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import backtest
import config
import crt_engine

EAT = ZoneInfo("Africa/Nairobi")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "research_notes", "crt-backtest-20261022",
                     "candles-30d.json")
CACHE = os.path.normpath(CACHE)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_candles():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    candles = asyncio.run(backtest.fetch_history("CRASH1000", 30))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(candles, f)
    return candles


def resample(candles, minutes):
    step = minutes * 60
    out, cur, bucket = [], None, None
    for c in candles:
        b = c["t"] // step
        if b != bucket:
            if cur:
                out.append(cur)
            bucket = b
            cur = {"t": b * step, "o": c["o"], "h": c["h"], "l": c["l"],
                   "c": c["c"], "n": None}
        else:
            cur["h"] = max(cur["h"], c["h"])
            cur["l"] = min(cur["l"], c["l"])
            cur["c"] = c["c"]
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# Fast replay (same engine math, incremental positions)
# ---------------------------------------------------------------------------

def fast_replay(candles, cfg):
    """Returns [(pos, signal_dict)] identical to engine.backfill +
    _make_signal reconstruction."""
    engine = crt_engine.CRTEngine(cfg)
    s = crt_engine.build_series(candles, cfg)
    out = []
    positions = {True: [], False: []}
    for p in range(len(candles)):
        if p < engine.min_bars:
            continue
        if s["atr"][p] is None or s["atr"][p] <= 0:
            continue
        for buy in engine.allowed_sides():
            score, cond = crt_engine.exhaustion_score(p, buy, s, cfg)
            if not crt_engine.cycle_allows(p, buy, s, positions[buy], cfg):
                continue
            if score < cfg["min_score"]:
                continue
            sig = engine._make_signal(candles, s, p, buy, score, cond)
            positions[buy].append(p)
            out.append((p, sig))
    return out


def ema_series(values, period):
    return crt_engine.ema_series(values, period)


def features_for(candles, s, replay):
    """Attach causal features to each replayed signal."""
    closes = [c["c"] for c in candles]
    # Kaufman efficiency ratio, 20 bars
    er = [None] * len(candles)
    for p in range(20, len(candles)):
        change = abs(closes[p] - closes[p - 20])
        vol = sum(abs(closes[i] - closes[i - 1])
                  for i in range(p - 19, p + 1))
        er[p] = (change / vol) if vol else 0.0
    # ATR rolling median (500 bars) for the regime feature
    import bisect as _bisect
    atr_med = [None] * len(candles)
    window = []       # raw values, for eviction
    sorted_w = []     # same values, kept sorted via insort (C-speed)
    for p, a in enumerate(s["atr"]):
        if a is not None:
            window.append(a)
            _bisect.insort(sorted_w, a)
            if len(window) > 500:
                old = window.pop(0)
                del sorted_w[_bisect.bisect_left(sorted_w, old)]
            if len(window) >= 100:
                atr_med[p] = sorted_w[len(sorted_w) // 2]
    # M15 EMA(20) slope from completed bars only (HTF context)
    m15 = resample(candles, 15)
    m15_ema = crt_engine.ema_series([c["c"] for c in m15], 20)
    m15_starts = [c["t"] for c in m15]
    import bisect

    def htf_slope(epoch):
        i = bisect.bisect_right(m15_starts, epoch) - 2  # last completed
        if i < 3 or m15_ema[i] is None or m15_ema[i - 3] is None:
            return None
        return m15_ema[i] - m15_ema[i - 3]

    feats = []
    prev_epoch = None
    for p, sig in replay:
        C, H, L = s["c"], s["h"], s["l"]
        atr_v, ema_v, rsi_v = s["atr"][p], s["ema"][p], s["rsi"][p]
        stretch = ((C[p] - ema_v) / atr_v
                   if ema_v is not None and atr_v else None)
        move = None
        if atr_v:
            ll = L[p - 1]
            for q in range(p - 2, p - 30 - 1, -1):  # drop_lookback = 30
                if q < 0:
                    break
                ll = min(ll, L[q])
            move = (C[p] - ll) / atr_v
        cnt = 0
        for q in range(p, max(p - 13, -1), -1):
            if C[q] > s["o"][q]:
                cnt += 1
            else:
                break
        # big-level sweep: any of last 5 bars broke the prior 100-bar high
        big = False
        if p >= 101:
            ref = max(H[p - 101:p - 5]) if p - 5 > p - 101 else None
            for q in range(p - 4, p + 1):
                prior = max(H[q - 100:q]) if q >= 100 else None
                if prior is not None and H[q] > prior:
                    big = True
                    break
        feats.append({
            "pos": p, "sig": sig,
            "rsi": rsi_v, "stretch_atr": stretch, "move_atr": move,
            "consecutive": cnt, "er20": er[p],
            "atr_ratio": (atr_v / atr_med[p]
                          if atr_v and atr_med[p] else None),
            "htf_slope": htf_slope(sig["epoch"]),
            "sweep_big": big,
            "hour_eat": datetime.fromtimestamp(sig["epoch"], tz=EAT).hour,
            "mins_since_prev": ((sig["epoch"] - prev_epoch) / 60
                                if prev_epoch else None),
        })
        prev_epoch = sig["epoch"]
    return feats


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def with_outcomes(candles, feats):
    for f in feats:
        out = backtest.resolve_outcome(candles, f["pos"], f["sig"])
        f.update(out)
    return feats


def stats(rows):
    closed = [r for r in rows if r["outcome"] in ("tp", "sl")]
    if not closed:
        return {"n": len(rows), "closed": 0, "hit": None, "pts": 0.0,
                "exp": None}
    tp = sum(1 for r in closed if r["outcome"] == "tp")
    pts = sum(r["points"] for r in closed)
    return {"n": len(rows), "closed": len(closed),
            "hit": round(tp / len(closed) * 100, 1),
            "pts": round(pts, 1), "exp": round(pts / len(closed), 2)}


def split_eval(rows, split_epoch):
    a = [r for r in rows if r["sig"]["epoch"] < split_epoch]
    b = [r for r in rows if r["sig"]["epoch"] >= split_epoch]
    return stats(a), stats(b), stats(rows)


def show(name, rows, split_epoch):
    a, b, full = split_eval(rows, split_epoch)
    print(f"{name:34s} H1 n={a['closed']:4d} hit={a['hit']}% exp={a['exp']} "
          f"| H2 n={b['closed']:4d} hit={b['hit']}% exp={b['exp']} "
          f"| FULL n={full['closed']} hit={full['hit']}% "
          f"pts={full['pts']} exp={full['exp']}")
    return a, b, full


if __name__ == "__main__":
    t0 = time.time()
    candles = load_candles()
    print(f"{len(candles)} candles "
          f"({datetime.fromtimestamp(candles[0]['t'], tz=EAT)} → "
          f"{datetime.fromtimestamp(candles[-1]['t'], tz=EAT)} EAT)")
    cfg = backtest.watcher_cfg()
    cfg["symbol"] = "CRASH1000"

    # --- validate fast replay against the engine's own backfill
    eng = crt_engine.CRTEngine(cfg)
    eng.backfill(candles)
    ref = sorted((s["epoch"], s["side"]) for s in eng.signals)
    replay = fast_replay(candles, cfg)
    mine = sorted((sig["epoch"], sig["side"]) for _, sig in replay)
    assert ref == mine, f"replay mismatch: {len(ref)} vs {len(mine)}"
    print(f"fast replay validated: {len(mine)} signals == backfill "
          f"({time.time() - t0:.1f}s)")

    s = crt_engine.build_series(candles, cfg)
    feats = with_outcomes(candles, features_for(candles, s, replay))
    split = candles[len(candles) // 2]["t"]
    print(f"split at {datetime.fromtimestamp(split, tz=EAT)} EAT\n")

    base = show("BASELINE (live config)", feats, split)
    F = feats
    variants = [
        ("require move condition",
         [f for f in F if f["sig"]["conditions"]["move"]]),
        ("require sweep+move",
         [f for f in F if f["sig"]["conditions"]["move"]
          and f["sig"]["conditions"]["sweep"]]),
        ("RSI >= 75", [f for f in F if (f["rsi"] or 0) >= 75]),
        ("RSI >= 80", [f for f in F if (f["rsi"] or 0) >= 80]),
        ("stretch >= 2.0 ATR",
         [f for f in F if (f["stretch_atr"] or 0) >= 2.0]),
        ("stretch >= 2.5 ATR",
         [f for f in F if (f["stretch_atr"] or 0) >= 2.5]),
        ("move >= 8 ATR", [f for f in F if (f["move_atr"] or 0) >= 8]),
        ("ER20 <= 0.30 (choppy)",
         [f for f in F if f["er20"] is not None and f["er20"] <= 0.30]),
        ("ER20 >= 0.50 (clean trend)",
         [f for f in F if f["er20"] is not None and f["er20"] >= 0.50]),
        ("ATR above median",
         [f for f in F if (f["atr_ratio"] or 0) >= 1.0]),
        ("ATR below median",
         [f for f in F if f["atr_ratio"] is not None
          and f["atr_ratio"] < 1.0]),
        ("HTF M15 not rising",
         [f for f in F if f["htf_slope"] is not None
          and f["htf_slope"] <= 0]),
        ("big-level sweep (100-bar)",
         [f for f in F if f["sweep_big"]]),
        ("night only (00-07 EAT)",
         [f for f in F if f["hour_eat"] < 7]),
        ("day only (07-24 EAT)",
         [f for f in F if f["hour_eat"] >= 7]),
    ]
    for name, rows in variants:
        show(name, rows, split)
    print(f"\n({time.time() - t0:.1f}s total)")
