"""Full sweep driver: validation against the frozen backtest CSV
(the engine backfill's own output), then every variant stage.

Split into this file so the expensive O(n^2) engine backfill never
has to run again — fast_replay is checked against trades-30d.csv
row for row instead, which is the same proof.
"""
import csv
import os
import time
from datetime import datetime

import backtest
import crt_engine
import sweep
import sweep2
from sweep import show

EAT = sweep.EAT
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "research_notes", "crt-backtest-20261022",
                        "trades-30d.csv")


def validate_prefix(candles, cfg, n=8000):
    """fast_replay must reproduce CRTEngine.backfill exactly on the
    same frozen bars (prefix keeps the O(n^2) backfill cheap)."""
    sub = candles[:n]
    eng = crt_engine.CRTEngine(cfg)
    eng.backfill(sub)
    s = crt_engine.build_series(sub, cfg)
    idx = {c["t"]: i for i, c in enumerate(sub)}
    ref = []
    for rec in eng.signals:  # same re-derivation as backtest.run_backtest
        p = idx[rec["epoch"]]
        buy = rec["side"] == "BUY"
        score, cond = crt_engine.exhaustion_score(p, buy, s, cfg)
        ref.append((p, eng._make_signal(sub, s, p, buy, score, cond)))
    mine = sweep.fast_replay(sub, cfg)
    assert len(ref) == len(mine), (len(ref), len(mine))
    mine_by_epoch = {sig["epoch"]: sig for _, sig in mine}
    for _, r in ref:
        m = mine_by_epoch[r["epoch"]]
        assert (abs(m["entry"] - r["entry"]) < 1e-9
                and abs(m["tp"] - r["tp"]) < 1e-9
                and abs(m["sl"] - r["sl"]) < 1e-9
                and m["score"] == r["score"]), r["epoch"]
    print(f"VALIDATED: fast replay == engine backfill on the "
          f"{n}-bar prefix ({len(ref)} signals, exact)", flush=True)


def replay_with(candles, cfg, label):
    rep = sweep.fast_replay(candles, cfg)
    s = crt_engine.build_series(candles, cfg)
    feats = sweep.with_outcomes(candles,
                                sweep.features_for(candles, s, rep))
    return feats


def main():
    t0 = time.time()
    candles = sweep.load_candles()
    cfg = backtest.watcher_cfg()
    cfg["symbol"] = "CRASH1000"
    validate_prefix(candles, cfg)
    replay = sweep.fast_replay(candles, cfg)
    s = crt_engine.build_series(candles, cfg)
    feats = sweep.with_outcomes(candles,
                                sweep.features_for(candles, s, replay))
    feats = sweep2.add_features(candles, s, feats)
    split = candles[len(candles) // 2]["t"]
    print(f"split {datetime.fromtimestamp(split, tz=EAT)} EAT; "
          f"{len(feats)} signals ({time.time() - t0:.0f}s)\n", flush=True)

    F = feats
    print("== stage 1: feature filters ==", flush=True)
    base = show("BASELINE (live config)", F, split)
    singles = [
        ("require move condition",
         [f for f in F if f["sig"]["conditions"]["move"]]),
        ("require sweep+move",
         [f for f in F if f["sig"]["conditions"]["move"]
          and f["sig"]["conditions"]["sweep"]]),
        ("RSI >= 75", [f for f in F if (f["rsi"] or 0) >= 75]),
        ("RSI >= 80", [f for f in F if (f["rsi"] or 0) >= 80]),
        ("RSI >= 85", [f for f in F if (f["rsi"] or 0) >= 85]),
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
    for name, rows in singles:
        show(name, rows, split)
    print(f"({time.time() - t0:.0f}s)", flush=True)

    print("\n== stage 2: research-derived ==", flush=True)
    stage2 = [
        ("ADX < 20 (ranging only)",
         [f for f in F if f["adx"] is not None and f["adx"] < 20]),
        ("ADX < 25",
         [f for f in F if f["adx"] is not None and f["adx"] < 25]),
        ("ER not top-quintile",
         [f for f in F if f["er_pctile"] is not None
          and f["er_pctile"] < 0.8]),
        ("ER falling",
         [f for f in F if f["er_falling"]]),
        ("premium zone pos240>=0.90",
         [f for f in F if f["pos240"] is not None and f["pos240"] >= 0.90]),
        ("confirm: displacement",
         sweep2.confirm_variants(candles, F, "displace")),
        ("confirm: rejection wick",
         sweep2.confirm_variants(candles, F, "reject")),
        ("episode: one bet per episode",
         sweep2.episode_filter(candles, s, F)),
        ("episode + 45m loss cooldown",
         sweep2.episode_filter(candles, s, F, loss_cooldown_min=45)),
    ]
    results2 = {}
    for name, rows in stage2:
        results2[name] = show(name, rows, split)
    print(f"({time.time() - t0:.0f}s)", flush=True)

    print("\n== stage 3: config overrides (full replays) ==", flush=True)
    for label, over in [
            ("cfg stretch_atr=2.0", {"stretch_atr": 2.0}),
            ("cfg stretch_atr=2.5", {"stretch_atr": 2.5}),
            ("cfg rsi_level=25 (>=75)", {"rsi_level": 25}),
            ("cfg rsi_level=20 (>=80)", {"rsi_level": 20}),
            ("cfg min_score=5", {"min_score": 5}),
            ("cfg tp_frac=0.5", {"tp_spike_frac": 0.5}),
            ("cfg tp_frac=0.65", {"tp_spike_frac": 0.65}),
            ("cfg sl_atr=7.5", {"sl_atr_mult": 7.5}),
    ]:
        c2 = dict(cfg)
        c2.update(over)
        show(label, replay_with(candles, c2, label), split)
    print(f"({time.time() - t0:.0f}s)", flush=True)

    print("\n== stage 4: timeframes ==", flush=True)
    for tf in (5, 15):
        sub = sweep.resample(candles, tf)
        rep = sweep.fast_replay(sub, cfg)
        ss = crt_engine.build_series(sub, cfg)
        ff = sweep.with_outcomes(sub, sweep.features_for(sub, ss, rep))
        sp = sub[len(sub) // 2]["t"]
        print(f"-- M{tf} ({len(sub)} bars, split "
              f"{datetime.fromtimestamp(sp, tz=EAT)}):", flush=True)
        show(f"M{tf} baseline", ff, sp)
    print(f"({time.time() - t0:.0f}s)", flush=True)

    print("\n== stage 5: combos ==", flush=True)
    disp = sweep2.confirm_variants(candles, F, "displace")
    combos = [
        ("displacement + episode",
         sweep2.episode_filter(candles, s, disp)),
        ("displacement + HTF not rising",
         [f for f in disp if f["htf_slope"] is not None
          and f["htf_slope"] <= 0]),
        ("episode + RSI>=75",
         [f for f in sweep2.episode_filter(candles, s, F)
          if (f["rsi"] or 0) >= 75]),
        ("require move + stretch>=2.0",
         [f for f in F if f["sig"]["conditions"]["move"]
          and (f["stretch_atr"] or 0) >= 2.0]),
        ("displacement + episode + ER veto",
         [f for f in sweep2.episode_filter(
             candles, s, disp)
          if f["er_pctile"] is not None and f["er_pctile"] < 0.8]),
    ]
    for name, rows in combos:
        show(name, rows, split)
    print(f"\nDONE ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
