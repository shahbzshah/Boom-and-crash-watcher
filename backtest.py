"""Backtest the CRT Pre-Reversal engine over Deriv candle history.

Fetches M1 candles for the configured symbol from Deriv's public
gateway, replays them through the SAME CRTEngine the live bot runs
(engine.backfill uses the identical evaluation path as live candle
closes — indicator series are causal, so a full-series build gives
the same values at every bar as the live prefix builds did), then
resolves each fired signal's TP/SL outcome on the candles that
followed it.

Outcome rules (stated so the numbers can be trusted):
  * Entry is the signal candle's close; outcomes are read from the
    NEXT candle onward, like the live bot's tick tracker.
  * Candles are not ticks: if one candle's range covers BOTH the TP
    and the SL, the trade is scored as an SL (worst case first).
  * No spread/slippage exists on Deriv synthetic tick prices for this
    level-based accounting; none is modelled.
  * Signals still open at the end of the data are reported as open
    and excluded from the win rate.

Usage:
    python backtest.py [--days 30] [--symbol CRASH1000]
                       [--report report.md] [--csv trades.csv]
"""
import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import crt_engine
import deriv_feed

EAT = ZoneInfo("Africa/Nairobi")


def watcher_cfg():
    """The CRT config exactly as watcher.Watcher builds it."""
    return {
        "asset_mode": config.CRT_ASSET_MODE,
        "symbol": config.SYMBOL,
        "min_score": config.CRT_MIN_SCORE,
        "range_bars": config.CRT_RANGE_BARS,
        "min_consecutive": config.CRT_MIN_CONSECUTIVE,
        "rsi_period": config.CRT_RSI_PERIOD,
        "rsi_level": config.CRT_RSI_LEVEL,
        "stretch_ema": config.CRT_STRETCH_EMA,
        "stretch_atr": config.CRT_STRETCH_ATR,
        "drop_lookback": config.CRT_DROP_LOOKBACK,
        "drop_atr": config.CRT_DROP_ATR,
        "spike_atr_mult": config.CRT_SPIKE_ATR_MULT,
        "min_drift_bars": config.CRT_MIN_DRIFT_BARS,
        "max_signals_per_cycle": config.CRT_MAX_SIGNALS_PER_CYCLE,
        "cooldown_bars": config.CRT_COOLDOWN_BARS,
        "spike_scan_depth": config.CRT_SPIKE_SCAN_DEPTH,
        "sl_atr_mult": config.CRT_SL_ATR_MULT,
        "tp_mode": config.CRT_TP_MODE,
        "tp_spike_frac": config.CRT_TP_SPIKE_FRAC,
        "rr": config.CRT_RR,
    }


async def fetch_history(symbol, days):
    """Page backwards through ticks_history candles, `days` back."""
    import websockets

    target = int(time.time()) - days * 86400
    by_epoch = {}
    endpoints = deriv_feed.parse_endpoints(config.WS_ENDPOINTS)
    url = deriv_feed.build_ws_url(endpoints[0], config.DERIV_APP_ID)
    async with websockets.connect(url, open_timeout=20) as ws:
        end = "latest"
        req = 9000
        while True:
            req += 1
            await ws.send(json.dumps({
                "ticks_history": symbol, "style": "candles",
                "granularity": config.CANDLE_MINUTES * 60,
                "count": 5000, "end": end, "adjust_start_time": 1,
                "req_id": req,
            }))
            batch = None
            while batch is None:
                msg = json.loads(await ws.recv())
                if msg.get("req_id") == req:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message"))
                    batch = msg.get("candles") or []
            if not batch:
                break
            prev_oldest = min(by_epoch) if by_epoch else None
            for c in batch:
                by_epoch[int(c["epoch"])] = {
                    "t": int(c["epoch"]), "o": float(c["open"]),
                    "h": float(c["high"]), "l": float(c["low"]),
                    "c": float(c["close"]), "n": None,
                }
            oldest = min(by_epoch)
            # Deriv caps candle responses at 1000 per request, so a
            # full-size batch just means "keep paging"; stop at the
            # target depth, on a short final page, or if the window
            # stops moving.
            if (oldest <= target or len(batch) < 1000
                    or oldest == prev_oldest):
                break
            end = oldest - 60
    candles = [by_epoch[t] for t in sorted(by_epoch)]
    return [c for c in candles if c["t"] >= target - 86400]


def resolve_outcome(candles, p, sig):
    """Walk candles after position p; return the outcome record."""
    sell = sig["side"] == "SELL"
    for q in range(p + 1, len(candles)):
        c = candles[q]
        if sell:
            sl_hit = c["h"] >= sig["sl"]
            tp_hit = c["l"] <= sig["tp"]
            if sl_hit:  # worst case first when both are inside one bar
                return {"outcome": "sl", "exit_epoch": c["t"],
                        "points": round(sig["entry"] - sig["sl"], 2),
                        "duration_min": q - p}
            if tp_hit:
                return {"outcome": "tp", "exit_epoch": c["t"],
                        "points": round(sig["entry"] - sig["tp"], 2),
                        "duration_min": q - p}
        else:
            sl_hit = c["l"] <= sig["sl"]
            tp_hit = c["h"] >= sig["tp"]
            if sl_hit:
                return {"outcome": "sl", "exit_epoch": c["t"],
                        "points": round(sig["sl"] - sig["entry"], 2),
                        "duration_min": q - p}
            if tp_hit:
                return {"outcome": "tp", "exit_epoch": c["t"],
                        "points": round(sig["tp"] - sig["entry"], 2),
                        "duration_min": q - p}
    return {"outcome": "open", "exit_epoch": None,
            "points": None, "duration_min": None}


def run_backtest(candles, cfg):
    engine = crt_engine.CRTEngine(cfg)
    engine.backfill(candles)
    s = crt_engine.build_series(candles, engine.cfg)
    idx = {c["t"]: i for i, c in enumerate(candles)}
    trades = []
    for rec in engine.signals:
        p = idx[rec["epoch"]]
        buy = rec["side"] == "BUY"
        score, cond = crt_engine.exhaustion_score(p, buy, s, engine.cfg)
        sig = engine._make_signal(candles, s, p, buy, score, cond)
        out = resolve_outcome(candles, p, sig)
        risk = abs(sig["entry"] - sig["sl"])
        # Did a real drop (>= 20 pts within the next 60 bars) follow?
        lows = [c["l"] for c in candles[p + 1:p + 61]]
        drop60 = (sig["entry"] - min(lows)) if (lows and not buy) else None
        trades.append({
            "epoch": sig["epoch"], "side": sig["side"],
            "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
            "score": sig["score"], "conditions": sig["conditions"],
            "rr_planned": (abs(sig["tp"] - sig["entry"]) / risk
                           if risk else None),
            "drop60": round(drop60, 1) if drop60 is not None else None,
            **out,
        })
    trades.sort(key=lambda t: t["epoch"])
    return trades


def summarize(trades):
    closed = [t for t in trades if t["outcome"] in ("tp", "sl")]
    tps = [t for t in closed if t["outcome"] == "tp"]
    sls = [t for t in closed if t["outcome"] == "sl"]
    gross_w = sum(t["points"] for t in tps)
    gross_l = sum(t["points"] for t in sls)
    total = gross_w + gross_l
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted(closed, key=lambda x: x["exit_epoch"] or 0):
        cum += t["points"]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    by_score = {}
    for sc in sorted({t["score"] for t in trades}):
        sub = [t for t in closed if t["score"] == sc]
        if sub:
            by_score[sc] = {
                "n": len(sub),
                "tp": sum(1 for t in sub if t["outcome"] == "tp"),
                "points": round(sum(t["points"] for t in sub), 1),
            }
    crash = [t for t in closed
             if t["drop60"] is not None and t["drop60"] >= 20]
    calm = [t for t in closed
            if t["drop60"] is not None and t["drop60"] < 20]
    days = (trades[-1]["epoch"] - trades[0]["epoch"]) / 86400 if trades else 0
    return {
        "signals": len(trades), "closed": len(closed),
        "open": len(trades) - len(closed),
        "tp": len(tps), "sl": len(sls),
        "hit_rate": (round(len(tps) / len(closed) * 100, 1)
                     if closed else None),
        "total_points": round(total, 1),
        "expectancy": (round(total / len(closed), 2) if closed else None),
        "avg_win": (round(gross_w / len(tps), 2) if tps else None),
        "avg_loss": (round(gross_l / len(sls), 2) if sls else None),
        "profit_factor": (round(gross_w / abs(gross_l), 2)
                          if gross_l else None),
        "max_drawdown": round(max_dd, 1),
        "signals_per_day": round(len(trades) / days, 1) if days else None,
        "by_score": by_score,
        "crash_followed": {
            "n": len(crash),
            "tp": sum(1 for t in crash if t["outcome"] == "tp"),
            "points": round(sum(t["points"] for t in crash), 1),
        },
        "no_crash": {
            "n": len(calm),
            "tp": sum(1 for t in calm if t["outcome"] == "tp"),
            "points": round(sum(t["points"] for t in calm), 1),
        },
    }


def fmt_ts(epoch):
    return datetime.fromtimestamp(epoch, tz=EAT).strftime("%Y-%m-%d %H:%M")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    symbol = args.symbol or "CRASH1000"
    print(f"Fetching {args.days} days of M1 candles for {symbol} ...",
          flush=True)
    candles = await fetch_history(symbol, args.days)
    print(f"Got {len(candles)} candles: "
          f"{fmt_ts(candles[0]['t'])} → {fmt_ts(candles[-1]['t'])} EAT",
          flush=True)

    cfg = watcher_cfg()
    cfg["symbol"] = symbol
    trades = run_backtest(candles, cfg)
    stats = summarize(trades)

    lines = []
    lines.append(f"# CRT backtest — {symbol} M1, "
                 f"{fmt_ts(candles[0]['t'])} → {fmt_ts(candles[-1]['t'])} EAT")
    lines.append("")
    lines.append(f"- Candles replayed: {len(candles)} "
                 f"(engine warmup {crt_engine.CRTEngine(cfg).min_bars} bars)")
    lines.append(f"- Signals: {stats['signals']} "
                 f"({stats['signals_per_day']}/day), "
                 f"closed {stats['closed']}, still open {stats['open']}")
    lines.append(f"- TP {stats['tp']} / SL {stats['sl']} — "
                 f"hit rate {stats['hit_rate']}%")
    lines.append(f"- Total points: {stats['total_points']} "
                 f"(expectancy {stats['expectancy']} pts/signal)")
    lines.append(f"- Avg win {stats['avg_win']} / avg loss "
                 f"{stats['avg_loss']} / profit factor "
                 f"{stats['profit_factor']}")
    lines.append(f"- Max drawdown: {stats['max_drawdown']} pts")
    lines.append(f"- By score: {stats['by_score']}")
    lines.append(f"- When a real drop (>=20 pts in 60 min) followed: "
                 f"{stats['crash_followed']}")
    lines.append(f"- When no drop followed: {stats['no_crash']}")
    report = "\n".join(lines)
    print("\n" + report)
    if args.report:
        with open(args.report, "w") as f:
            f.write(report + "\n")
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["epoch_eat", "side", "entry", "sl", "tp", "score",
                        "outcome", "points", "duration_min", "drop60"])
            for t in trades:
                w.writerow([fmt_ts(t["epoch"]), t["side"],
                            f"{t['entry']:.2f}", f"{t['sl']:.2f}",
                            f"{t['tp']:.2f}", t["score"], t["outcome"],
                            t["points"], t["duration_min"], t["drop60"]])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
