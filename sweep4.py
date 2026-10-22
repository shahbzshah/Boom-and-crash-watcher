"""Fourth sweep stage: (stop x target) geometry grids.

Two families:
  * baseline entries, cfg replays over sl_atr_mult x tp_spike_frac
  * displacement-confirmed entries, SL = conf-high + mult x ATR,
    TP = frac x avg spike size (both measured at the confirmation bar)

Judged split-half as always; a cell only counts if expectancy beats
the live baseline (-0.80 / -0.96) in BOTH halves.
"""
import time
from datetime import datetime

import backtest
import crt_engine
import sweep
import sweep3
from sweep import show

EAT = sweep.EAT


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

    print("== grid A: baseline entries, SL mult x TP frac ==", flush=True)
    for slm in (0.5, 1.0, 2.0):
        for tpf in (0.3, 0.5, 0.8):
            if slm == 2.0 and tpf == 0.8:
                pass
            c2 = dict(cfg)
            c2["sl_atr_mult"] = slm
            c2["tp_spike_frac"] = tpf
            rep = sweep.fast_replay(candles, c2)
            s2 = crt_engine.build_series(candles, c2)
            ff = sweep.with_outcomes(
                candles, sweep.features_for(candles, s2, rep))
            show(f"SL {slm}xATR TP {tpf}xSpike", ff, split)
    print(f"({time.time() - t0:.0f}s)", flush=True)

    print("\n== grid B: displacement entries, SL mult x TP ==",
          flush=True)
    disp = list(sweep3.displace_entries(candles, s, feats))
    print(f"{len(disp)} confirmed setups", flush=True)
    for slm in (0.5, 1.0, 2.0):
        for tp_mode in ("0.5xSpike", "0.8xSpike", "1.5R", "2R"):
            rows = []
            for f, q in disp:
                entry = candles[q]["c"]
                sl = candles[q]["h"] + slm * s["atr"][q]
                if tp_mode.endswith("xSpike"):
                    frac = float(tp_mode[:-6])
                    avg = crt_engine.avg_spike_size(q, False, s, cfg)
                    tp = entry - frac * avg if avg > 0 else None
                else:
                    rr = float(tp_mode[:-1])
                    tp = entry - rr * (sl - entry)
                if tp is None:
                    continue
                rows.append(sweep3.rebuild(candles, f, q, sl, tp))
            show(f"conf SL {slm}xATR TP {tp_mode}", rows, split)
    print(f"\nDONE ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
