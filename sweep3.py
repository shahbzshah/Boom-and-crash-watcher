"""Third sweep stage: confirmation entry x stop placement.

Stage 2 showed bearish-displacement confirmation lifts the hit rate
from ~26% to ~47% in BOTH halves — but expectancy worsened because
the stop stayed at the setup level while the entry moved down,
tripling the average loss. The published strategies pair the
confirmation with a stop above the confirmation structure. This
stage tests that pairing, plus a stop ladder on baseline entries to
separate "confirmation helps" from "tighter stop helps".
"""
import time
from datetime import datetime

import backtest
import crt_engine
import sweep
from sweep import show

EAT = sweep.EAT


def displace_entries(candles, s, feats, window=3):
    """Yield (feat, entry_pos) for signals confirmed by a bearish
    displacement close within `window` bars; void if the original
    stop is hit first."""
    for f in feats:
        p, sig = f["pos"], f["sig"]
        for q in range(p + 1, min(p + 1 + window, len(candles))):
            if candles[q]["h"] >= sig["sl"]:
                break
            if (candles[q]["c"] < candles[q]["o"]
                    and candles[q]["c"] < candles[p]["l"]):
                yield f, q
                break


def rebuild(candles, f, entry_pos, sl, tp):
    sig = dict(f["sig"])
    sig["entry"] = candles[entry_pos]["c"]
    sig["sl"] = sl
    sig["tp"] = tp
    res = backtest.resolve_outcome(candles, entry_pos, sig)
    nf = dict(f)
    nf.update(res)
    nf["sig"] = sig
    return nf


def main():
    t0 = time.time()
    candles = sweep.load_candles()
    cfg = backtest.watcher_cfg()
    cfg["symbol"] = "CRASH1000"
    replay = sweep.fast_replay(candles, cfg)
    s = crt_engine.build_series(candles, cfg)
    feats = sweep.with_outcomes(candles,
                                sweep.features_for(candles, s, replay))
    split = candles[len(candles) // 2]["t"]
    print(f"split {datetime.fromtimestamp(split, tz=EAT)} EAT\n",
          flush=True)

    disp = list(displace_entries(candles, s, feats))
    print(f"displacement confirmed {len(disp)} of {len(feats)} setups\n",
          flush=True)

    def variant(name, rows):
        show(name, [r for r in rows if r is not None], split)

    # A: stop above confirmation high + original 5xATR buffer,
    #    TP keeps the original distance
    rows = []
    for f, q in disp:
        sig = f["sig"]
        dist = sig["entry"] - sig["tp"]
        sl = candles[q]["h"] + 5.0 * s["atr"][q]
        rows.append(rebuild(candles, f, q, sl,
                            candles[q]["c"] - dist))
    variant("conf + SL conf-high + 5ATR, TP orig dist", rows)

    # B: stop above setup high + 0.5 ATR (tight), TP orig distance
    rows = []
    for f, q in disp:
        p, sig = f["pos"], f["sig"]
        dist = sig["entry"] - sig["tp"]
        sl = max(candles[r]["h"] for r in range(p - 3, p + 1)) \
            + 0.5 * s["atr"][q]
        rows.append(rebuild(candles, f, q, sl,
                            candles[q]["c"] - dist))
    variant("conf + SL setup-high + 0.5ATR, TP orig dist", rows)

    # C: stop above confirmation high + 1 ATR, TP orig distance
    rows = []
    for f, q in disp:
        sig = f["sig"]
        dist = sig["entry"] - sig["tp"]
        sl = candles[q]["h"] + 1.0 * s["atr"][q]
        rows.append(rebuild(candles, f, q, sl,
                            candles[q]["c"] - dist))
    variant("conf + SL conf-high + 1ATR, TP orig dist", rows)

    # D: stop above confirmation high + 1 ATR, TP = 1.5R
    rows = []
    for f, q in disp:
        entry = candles[q]["c"]
        sl = candles[q]["h"] + 1.0 * s["atr"][q]
        rows.append(rebuild(candles, f, q, sl,
                            entry - 1.5 * (sl - entry)))
    variant("conf + SL conf-high + 1ATR, TP 1.5R", rows)

    # E: stop above confirmation high + 1 ATR, TP = 1R (win-rate max)
    rows = []
    for f, q in disp:
        entry = candles[q]["c"]
        sl = candles[q]["h"] + 1.0 * s["atr"][q]
        rows.append(rebuild(candles, f, q, sl,
                            entry - 1.0 * (sl - entry)))
    variant("conf + SL conf-high + 1ATR, TP 1R", rows)

    # Control: baseline entries with a tighter stop ladder
    # (sl_atr_mult only moves the stop; signal set is unchanged
    # apart from cycle knock-ons, so do full replays)
    for mult in (0.5, 1.0, 2.0, 3.0):
        c2 = dict(cfg)
        c2["sl_atr_mult"] = mult
        rep = sweep.fast_replay(candles, c2)
        s2 = crt_engine.build_series(candles, c2)
        ff = sweep.with_outcomes(
            candles, sweep.features_for(candles, s2, rep))
        show(f"control: baseline entries, SL {mult}xATR", ff, split)
    print(f"\nDONE ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
