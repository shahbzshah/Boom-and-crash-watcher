"""Tick watcher: streams Deriv ticks, tracks spikes, evaluates your rule."""
import asyncio
import contextlib
import json
import logging
import time
from collections import deque

import config
import crt_engine
import deriv_feed
import health
import signals
import structure
import alerts
import vidya
import volumatic

log = logging.getLogger("crash_alert.watcher")

def _quiet_connection_drops(loop, context):
    """Drop the noisy asyncio report when the feed dies without a close
    frame. Deriv's public gateway cuts connections every 120 seconds and
    the websockets library logs a shielded-future traceback for it; the
    reconnect logic below already handles the disconnect."""
    if "exception in shielded future" in context.get("message", ""):
        return
    loop.default_exception_handler(context)


class Watcher:
    def __init__(self):
        self.ticks = deque(maxlen=config.TICK_BUFFER)  # (epoch, price)
        self.symbol = config.SYMBOL
        self.ctx = {
            "ticks_since_spike": None,
            "last_spike_price": None,
            "last_spike_epoch": None,
            "current_price": None,
            # market-structure block (filled in as candles close)
            "trend": "ranging",          # uptrend | downtrend | ranging
            "phase": None,               # accumulation | manipulation | distribution
            "phase_direction": None,     # bullish | bearish | None
            "last_sweep": None,          # {side, level, extreme, index, epoch}
            "swing_high": None,
            "swing_low": None,
            "range_high": None,
            "range_low": None,
            "atr": None,
            "last_break": None,          # {type: BOS|CHoCH, direction, ...}
            "candles_closed": 0,
            # VIDYA block (filled in as candles close)
            "vidya": None,               # standard Variable Index Dynamic Average
            "vidya_vol": None,           # volume-modulated ("Volumatic") variant
            # Volumatic VIDYA trend engine (reconstruction of the public spec)
            "vol_trend": None,           # +1 bullish | -1 bearish | None undecided
            "vol_trend_bars": None,      # candles since the last trend flip
                                         # (0 = flipped on the just-closed candle)
            "vol_vidya": None,           # VIDYA line value (last closed candle)
            "vol_upper": None,           # upper ATR band (last closed candle)
            "vol_lower": None,           # lower ATR band (last closed candle)
            # CRT Pre-Reversal Engine block (see crt_engine.py)
            "crt_buy_score": None,       # exhaustion score, buy side (0-5)
            "crt_sell_score": None,      # exhaustion score, sell side (0-5)
            "crt_last_signal": None,     # last CRT signal dict, if any
        }
        self.candles = structure.CandleBuilder(minutes=config.CANDLE_MINUTES,
                                              keep=config.CANDLE_KEEP)
        self.struct_cfg = {
            "swing_left": config.SWING_LEFT,
            "swing_right": config.SWING_RIGHT,
            "trend_lookback": config.TREND_LOOKBACK,
            "sweep_lookback": config.SWEEP_LOOKBACK,
            "sweep_exclude": config.SWEEP_EXCLUDE,
            "sweep_scan": config.SWEEP_SCAN,
            "accum_bars": config.ACCUM_BARS,
            "accum_atr_mult": config.ACCUM_ATR_MULT,
            "manip_bars": config.MANIP_BARS,
            "dist_bars": config.DIST_BARS,
        }
        self._last_logged_phase = None
        self.armed = True
        self.last_alert_ts = 0.0
        self._connected = False
        self._down = True
        # Dashboard state (see status_snapshot): process start, the
        # endpoint currently in use, feed drop/recovery times, and the
        # fired-signal ledger with TP/SL outcomes tracked per tick.
        self.started_at = time.time()
        self._endpoint = None
        self.last_connect_epoch = None
        self.last_drop_epoch = None
        self.signal_history = deque(maxlen=100)
        # Confirmation tier: fired setups waiting to see whether price
        # displaces through the setup candle's extreme within
        # CRT_CONFIRM_WINDOW bars (see _process_confirmations).
        self._pending_confirms = []
        self._last_confirm_bar = None
        self._last_status_ts = 0.0
        self._last_notify_ts = 0.0
        self._live_req_id = None
        # CRT Pre-Reversal Engine — Megah's actual strategy (the .mq5
        # source he sent). Evaluated on every closed candle; its own
        # cycle filter governs alert spacing, so CRT alerts bypass the
        # tick-level arm/cooldown used by the custom-rule hook.
        self.crt_cfg = {
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
        self.engine = crt_engine.CRTEngine(self.crt_cfg)

    def _on_tick(self, epoch, price):
        prev = self.ticks[-1][1] if self.ticks else None
        self.ticks.append((epoch, price))
        self.ctx["current_price"] = price
        self._track_outcomes(epoch, price)

        # Spike tracking: a fresh spike starts a new cycle and re-arms alerts.
        if prev is not None and signals.detect_spike(prev, price):
            self.ctx["last_spike_price"] = price
            self.ctx["last_spike_epoch"] = epoch
            self.ctx["ticks_since_spike"] = 0
            if not self.armed:
                log.info("New spike detected — alerts re-armed.")
            self.armed = True
            return

        if self.ctx["last_spike_epoch"] is not None:
            self.ctx["ticks_since_spike"] = signals.ticks_since(
                self.ticks, self.ctx["last_spike_epoch"]
            )

        # Market structure + CRT: refresh on candle close.
        closed = self.candles.on_tick(epoch, price)
        if closed is not None:
            self._on_candle_closed()

        # Evaluate the user's custom rule (extra hook; the CRT engine
        # above is the live strategy).
        try:
            fire, reason = signals.crash_imminent(list(self.ticks), dict(self.ctx))
        except Exception as exc:
            log.error("Signal rule raised: %s", exc)
            return

        if not fire:
            return

        now = time.time()
        if not self.armed:
            log.info("Signal fired but disarmed (already alerted this cycle).")
            return
        if now - self.last_alert_ts < config.ALERT_COOLDOWN_SEC:
            log.info("Signal fired but inside cooldown — skipped.")
            return

        text = alerts.format_alert(
            self.symbol, reason, price, self.ctx["ticks_since_spike"], epoch
        )
        if alerts.send_alert(text):
            log.warning("ALERT SENT: %s", reason)
            self.armed = False  # one alert per cycle
            self.last_alert_ts = now
        else:
            log.error("Alert failed to send — staying armed to retry next signal.")

    # -- signal outcome tracking (dashboard ledger) ---------------------

    def _track_outcomes(self, epoch, price):
        """Close out open dashboard signal records whose TP or SL the
        price has reached. Points are measured to the TP/SL level
        itself (clean numbers, and the level is what the alert
        promised), positive when the trade made money."""
        for rec in self.signal_history:
            if rec["outcome"] != "open":
                continue
            if rec["side"] == "SELL":
                if price <= rec["tp"]:
                    rec["outcome"] = "tp"
                    rec["points"] = round(rec["entry"] - rec["tp"], 2)
                elif price >= rec["sl"]:
                    rec["outcome"] = "sl"
                    rec["points"] = round(rec["entry"] - rec["sl"], 2)
                else:
                    continue
            else:  # BUY
                if price >= rec["tp"]:
                    rec["outcome"] = "tp"
                    rec["points"] = round(rec["tp"] - rec["entry"], 2)
                elif price <= rec["sl"]:
                    rec["outcome"] = "sl"
                    rec["points"] = round(rec["sl"] - rec["entry"], 2)
                else:
                    continue
            rec["closed_epoch"] = epoch
            rec["duration_sec"] = epoch - rec["epoch"]
            log.info("Signal outcome: %s %s at %s (%s pts).",
                     rec["side"], rec["outcome"].upper(),
                     rec["entry"], rec["points"])

    # -- candle-close pipeline -------------------------------------------

    def _refresh_context(self):
        """Recompute structure / VIDYA / Volumatic blocks from the
        closed candles and merge them into ctx (no alerts)."""
        block = structure.analyze(self.candles.closed, self.struct_cfg)
        self.ctx.update(block)
        closes = [c["c"] for c in self.candles.closed]
        vols = [c.get("n") for c in self.candles.closed]
        v = vidya.vidya(closes, period=config.VIDYA_PERIOD)
        vv = vidya.volumatic_vidya(closes, vols, period=config.VIDYA_PERIOD)
        self.ctx["vidya"] = v[-1] if v else None
        self.ctx["vidya_vol"] = vv[-1] if vv else None
        vseries = volumatic.volumatic(
            self.candles.closed,
            cmo_period=config.VIDYA_PERIOD,
            atr_period=config.VOL_ATR_PERIOD,
            atr_mult=config.VOL_ATR_MULT,
        )
        if vseries:
            last = vseries[-1]
            self.ctx["vol_trend"] = last["trend"]
            self.ctx["vol_trend_bars"] = last["bars_since_flip"]
            self.ctx["vol_vidya"] = last["vidya"]
            self.ctx["vol_upper"] = last["upper"]
            self.ctx["vol_lower"] = last["lower"]
        key = (block["trend"], block["phase"], block["phase_direction"])
        if key != self._last_logged_phase:
            self._last_logged_phase = key
            sweep = block["last_sweep"]
            sweep_txt = (f"sweep-{sweep['side']}@{sweep['level']}"
                         if sweep else "no-sweep")
            log.info("Structure: trend=%s phase=%s dir=%s %s",
                     block["trend"], block["phase"],
                     block["phase_direction"], sweep_txt)

    # -- confirmation tier -------------------------------------------------

    def _arm_confirmation(self, sig, candle, record):
        """Watch a freshly fired setup for displacement confirmation
        over the next CRT_CONFIRM_WINDOW closed candles."""
        self._pending_confirms.append({
            "side": sig["side"],
            "setup_epoch": sig["epoch"],
            "setup_low": candle["l"], "setup_high": candle["h"],
            "sl": sig["sl"],
            "tp_dist": abs(sig["tp"] - sig["entry"]),
            "score": sig["score"], "conditions": sig["conditions"],
            "atr": sig["atr"],
            "record": record,
            "bars_seen": 0,
        })

    def _process_confirmations(self, bar):
        """Check pending setups against the just-closed candle.

        Mirrors the backtested rule exactly: a setup is void if price
        reaches its stop first; it is CONFIRMED when a candle closes
        through the setup candle's extreme in the trade direction
        (SELL: down candle closing below the setup low); otherwise it
        expires after CRT_CONFIRM_WINDOW bars.
        """
        if bar["t"] == self._last_confirm_bar:
            return
        self._last_confirm_bar = bar["t"]
        still = []
        for p in self._pending_confirms:
            p["bars_seen"] += 1
            if p["side"] == "SELL":
                void = bar["h"] >= p["sl"]
                hit = (not void and bar["c"] < bar["o"]
                       and bar["c"] < p["setup_low"])
            else:  # BUY
                void = bar["l"] <= p["sl"]
                hit = (not void and bar["c"] > bar["o"]
                       and bar["c"] > p["setup_high"])
            if void:
                log.info("Confirmation void: %s setup at %s reached "
                         "its stop before confirming.",
                         p["side"], p["setup_epoch"])
                continue
            if hit:
                self._fire_confirmed(p, bar)
                continue
            if p["bars_seen"] < config.CRT_CONFIRM_WINDOW:
                still.append(p)
            else:
                log.info("Confirmation expired: %s setup at %s never "
                         "displaced within %d bars.",
                         p["side"], p["setup_epoch"],
                         config.CRT_CONFIRM_WINDOW)
        self._pending_confirms = still

    def _fire_confirmed(self, p, bar):
        """Record + alert a confirmed setup. Entry is the confirmation
        candle's close; the stop stays at the setup's stop and the
        target keeps the setup's TP distance — the exact geometry the
        2026-10-22 backtest measured at ~47% TP-first."""
        entry = bar["c"]
        if p["side"] == "SELL":
            tp = entry - p["tp_dist"]
        else:
            tp = entry + p["tp_dist"]
        risk = abs(entry - p["sl"])
        rec = {
            "side": p["side"], "epoch": bar["t"],
            "entry": entry, "sl": p["sl"], "tp": tp,
            "score": p["score"], "conditions": p["conditions"],
            "atr": p["atr"],
            "rr": (p["tp_dist"] / risk) if risk else None,
            "outcome": "open", "points": None,
            "closed_epoch": None, "duration_sec": None,
            "tier": "confirmed", "confirmed": True,
            "setup_epoch": p["setup_epoch"],
        }
        self.signal_history.append(rec)
        p["record"]["confirmed"] = True
        log.warning("CRT CONFIRMED: %s setup %s displaced at %s "
                    "(entry %s, SL %s, TP %s).",
                    p["side"], p["setup_epoch"], bar["t"],
                    entry, p["sl"], tp)
        text = alerts.format_confirmed_alert(
            rec, self.symbol, f"M{config.CANDLE_MINUTES}")
        if alerts.send_alert(text):
            log.warning("CRT CONFIRMED ALERT SENT: %s entry %s",
                        p["side"], entry)
        else:
            log.error("CRT confirmed alert failed to send (%s).",
                      p["side"])

    def _on_candle_closed(self):
        """A candle just closed: refresh ctx, check pending setups for
        confirmation, then run the CRT engine and send any signal it
        fires straight to Telegram."""
        self._refresh_context()
        if self.candles.closed:
            self._process_confirmations(self.candles.closed[-1])
        try:
            fired = self.engine.on_new_close(self.candles.closed)
        except Exception as exc:
            log.error("CRT engine raised: %s", exc)
            return
        diag = self.engine.last_diag
        self.ctx["crt_buy_score"] = diag.get("buy_score")
        self.ctx["crt_sell_score"] = diag.get("sell_score")
        for sig in fired:
            self.ctx["crt_last_signal"] = sig
            risk = abs(sig["entry"] - sig["sl"])
            rec = {
                "side": sig["side"], "epoch": sig["epoch"],
                "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                "score": sig["score"], "conditions": sig["conditions"],
                "atr": sig["atr"],
                "rr": (abs(sig["tp"] - sig["entry"]) / risk) if risk else None,
                "outcome": "open", "points": None,
                "closed_epoch": None, "duration_sec": None,
                "tier": "setup", "confirmed": False,
                "setup_epoch": None,
            }
            self.signal_history.append(rec)
            if self.candles.closed:
                self._arm_confirmation(sig, self.candles.closed[-1], rec)
            text = alerts.format_crt_alert(
                sig, self.symbol, f"M{config.CANDLE_MINUTES}", self.ctx)
            if alerts.send_alert(text):
                log.warning("CRT ALERT SENT: %s score %s entry %s",
                            sig["side"], sig["score"], sig["entry"])
            else:
                log.error("CRT alert failed to send (%s).", sig["side"])

    # -- dashboard snapshot ------------------------------------------------

    @staticmethod
    def _endpoint_label(endpoint):
        if not endpoint:
            return None
        for needle, label in (("api.derivws.com", "public gateway"),
                              ("green.derivws.com", "green cluster"),
                              ("blue.derivws.com", "blue cluster"),
                              ("red.derivws.com", "red cluster"),
                              ("binaryws", "binaryws (legacy)"),
                              ("derivws", "derivws (legacy)")):
            if needle in endpoint:
                return label
        return endpoint

    def status_snapshot(self):
        """Everything the Crash Watch dashboard renders, as one dict.

        All values are the bot's real state — scores come from the
        engine's own scorer, outcomes from the tracked ledger.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        eat = ZoneInfo("Africa/Nairobi")

        def full(epoch):
            return (datetime.fromtimestamp(epoch, tz=eat)
                    .strftime("%Y-%m-%d %H:%M:%S") if epoch else None)

        def hm(epoch):
            return (datetime.fromtimestamp(epoch, tz=eat)
                    .strftime("%H:%M") if epoch else None)

        def day(epoch):
            return (datetime.fromtimestamp(epoch, tz=eat)
                    .strftime("%Y-%m-%d") if epoch else None)

        closed = self.candles.closed
        detail = crt_engine.score_detail(closed, self.engine.cfg,
                                         buy=False)
        cfg = self.engine.cfg
        price = self.ctx.get("current_price")

        change_1h = None
        if price is not None and len(closed) > 60:
            ref = closed[-61]["c"]
            if ref:
                change_1h = {"abs": round(price - ref, 2),
                             "pct": round((price - ref) / ref * 100, 3)}

        cond = detail["conditions"]
        rsi_v = detail["rsi"]
        stretch_v = detail["stretch_atr"]
        move_v = detail["move_atr"]
        score_rows = [
            {"key": "sweep", "label": "CRT sweep", "met": cond["sweep"],
             "value": "met" if cond["sweep"] else "waiting"},
            {"key": "consecutive", "label": "Drift candles",
             "met": cond["consecutive"],
             "value": f"×{detail['consecutive']}"},
            {"key": "rsi", "label": "RSI extreme", "met": cond["rsi"],
             "value": f"{rsi_v:.1f}" if rsi_v is not None else "—"},
            {"key": "stretch", "label": "Stretch from EMA",
             "met": cond["stretch"],
             "value": (f"{stretch_v:.1f} ATR"
                       if stretch_v is not None else "—")},
            {"key": "move", "label": "Move size", "met": cond["move"],
             "value": (f"{move_v:.1f} of {cfg['drop_atr']:.1f} ATR"
                       if move_v is not None else "—")},
        ]

        def record_view(rec):
            out = dict(rec)
            out["time_eat"] = hm(rec["epoch"])
            out["date_eat"] = day(rec["epoch"])
            out["when_eat"] = full(rec["epoch"])
            if rec.get("setup_epoch"):
                out["setup_time_eat"] = hm(rec["setup_epoch"])
            return out

        history = [record_view(r) for r in reversed(self.signal_history)]

        def tier_stats(recs):
            closed_r = [r for r in recs if r["outcome"] in ("tp", "sl")]
            tp_c = sum(1 for r in closed_r if r["outcome"] == "tp")
            return {
                "signals": len(recs),
                "tp": tp_c,
                "sl": len(closed_r) - tp_c,
                "open": sum(1 for r in recs if r["outcome"] == "open"),
                "hit_rate": (round(tp_c / len(closed_r) * 100, 1)
                             if closed_r else None),
                "total_points": round(sum(r["points"] or 0
                                          for r in closed_r), 1),
            }

        setup_recs = [r for r in self.signal_history
                      if r.get("tier") != "confirmed"]
        conf_recs = [r for r in self.signal_history
                     if r.get("tier") == "confirmed"]
        stats = tier_stats(setup_recs)
        rr_vals = [r["rr"] for r in setup_recs
                   if r["outcome"] in ("tp", "sl") and r["rr"]]
        stats["avg_rr"] = (round(sum(rr_vals) / len(rr_vals), 2)
                           if rr_vals else None)
        stats["confirmed"] = tier_stats(conf_recs)

        ping = health.last_ping()
        now = int(time.time())
        return {
            "now": now,
            "now_eat": full(now),
            "symbol": self.symbol,
            "connected": self._connected,
            "endpoint": self._endpoint,
            "endpoint_label": self._endpoint_label(self._endpoint),
            "endpoint_url": (deriv_feed.build_ws_url(
                self._endpoint, config.DERIV_APP_ID)
                if self._endpoint else None),
            "price": price,
            "change_1h": change_1h,
            "trend": self.ctx.get("trend"),
            "phase": self.ctx.get("phase"),
            "phase_direction": self.ctx.get("phase_direction"),
            "candles_closed": len(closed),
            "buffer_keep": config.CANDLE_KEEP,
            "rsi": rsi_v,
            "ema": detail["ema"],
            "atr": detail["atr"] if detail["atr"] is not None
                   else self.ctx.get("atr"),
            "liquidity": (self.ctx.get("range_high")
                          or self.ctx.get("swing_high")),
            "candles": [{"t": c["t"], "o": c["o"], "h": c["h"],
                         "l": c["l"], "c": c["c"]}
                        for c in closed[-40:]],
            "score": {"value": detail["score"], "min": cfg["min_score"],
                      "conditions": score_rows},
            "last_signal": history[0] if history else None,
            "history": history,
            "stats": stats,
            "config": {
                "timeframe": f"M{config.CANDLE_MINUTES}",
                "min_score": cfg["min_score"],
                "rsi_period": cfg["rsi_period"],
                "rsi_level": cfg["rsi_level"],
                "stretch_ema": cfg["stretch_ema"],
                "stretch_atr": cfg["stretch_atr"],
                "drop_lookback": cfg["drop_lookback"],
                "drop_atr": cfg["drop_atr"],
                "sl_atr": cfg["sl_atr_mult"],
                "tp_mode": cfg["tp_mode"],
                "tp_frac": cfg["tp_spike_frac"],
                "rr": cfg["rr"],
                "range_bars": cfg["range_bars"],
                "min_consecutive": cfg["min_consecutive"],
                "confirm_window": config.CRT_CONFIRM_WINDOW,
            },
            "delivery": {"telegram": bool(config.TELEGRAM_BOT_TOKEN
                                          and config.TELEGRAM_CHAT_ID)},
            "system": {
                "started_eat": full(int(self.started_at)),
                "uptime_sec": max(0, now - int(self.started_at)),
                "last_connect_eat": full(self.last_connect_epoch),
                "last_drop_eat": full(self.last_drop_epoch),
                "last_connect_epoch": self.last_connect_epoch,
                "last_drop_epoch": self.last_drop_epoch,
                "host": "Render · Frankfurt",
                "last_ping_ago_sec": (now - ping) if ping else None,
            },
        }

    # -- Deriv history seeding --------------------------------------------

    @staticmethod
    def _history_to_candles(payload):
        """Convert a Deriv ticks_history candles payload to our dicts."""
        out = []
        for c in payload or []:
            out.append({
                "t": int(c["epoch"]),
                "o": float(c["open"]), "h": float(c["high"]),
                "l": float(c["low"]), "c": float(c["close"]),
                "n": None,
            })
        return out

    async def _seed_history(self, ws):
        """Fetch recent candles so the CRT engine starts with its full
        ~252-bar warmup instead of waiting hours. Best-effort: on any
        failure the bot simply accumulates candles live."""
        try:
            payload = await deriv_feed.fetch_candles(
                ws, self.symbol, config.HISTORY_CANDLES,
                config.CANDLE_MINUTES)
            if not payload:
                log.warning("History seed: no candles returned — "
                            "continuing live-only.")
                return
            candles = self._history_to_candles(payload)
            if len(candles) > 1:
                candles = candles[:-1]  # last one is still forming
            self.candles.seed(candles)
            self.engine.reset()
            self.engine.backfill(self.candles.closed)
            self._refresh_context()
            log.info("History seeded: %d closed candles, %d past CRT "
                     "signal(s) in the current window.",
                     len(self.candles.closed), len(self.engine.signals))
        except Exception as exc:
            log.warning("History seed failed (%s) — continuing live-only.",
                        exc)

    # -- feed status -------------------------------------------------------

    def _notify(self, icon, title, detail, repeat=False):
        """Send a feed-status message to Telegram.

        Routine reconnects stay quiet; a message is sent when the feed
        comes back up, when it goes down, and then at most every
        `NOTIFY_REPEAT_SEC` while it stays down.
        """
        if not config.NOTIFY_FEED_STATUS:
            return
        now = time.time()
        if now - self._last_status_ts < 60:
            return
        if repeat and now - self._last_notify_ts < config.NOTIFY_REPEAT_SEC:
            return
        self._last_status_ts = now
        self._last_notify_ts = now
        text = alerts.format_status(icon, title, detail)
        if alerts.send_alert(text):
            log.info("Status sent: %s", title)
        else:
            log.error("Status message failed to send (%s).", title)

    def _heartbeat(self):
        """Periodic liveness report while the feed is up."""
        ctx = self.ctx
        detail = (f"Symbol: {self.symbol}\n"
                  f"Price: {ctx.get('current_price')}\n"
                  f"Candles closed: {ctx.get('candles_closed')}\n"
                  f"Trend: {ctx.get('trend')} | phase: {ctx.get('phase')}\n"
                  f"CRT score: {ctx.get('crt_sell_score')}/5")
        log.info("Heartbeat: symbol=%s price=%s candles=%s trend=%s "
                 "crt_sell=%s", self.symbol, ctx.get("current_price"),
                 ctx.get("candles_closed"), ctx.get("trend"),
                 ctx.get("crt_sell_score"))
        if config.HEARTBEAT_SEC > 0:
            self._notify("💓", "Bot alive", detail)

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(config.HEARTBEAT_SEC)
            self._heartbeat()

    # -- connection --------------------------------------------------------

    async def _open_session(self, ws, candidates):
        """Negotiate a streamable symbol, seed history, subscribe to ticks.

        Returns the symbol that was selected.
        """
        streamable, rejected = await deriv_feed.probe_symbols(ws, candidates)
        if not streamable:
            raise RuntimeError(
                "no streamable symbol (tried %s; rejected: %s)"
                % (", ".join(candidates), ", ".join(rejected) or "none"))
        symbol = streamable[0]
        if symbol != config.SYMBOL:
            log.warning("%s is not streamable on this endpoint — using %s "
                        "instead (set SYMBOL_FALLBACKS to allow this).",
                        config.SYMBOL, symbol)
        self.symbol = symbol
        self.crt_cfg["symbol"] = symbol
        # The engine copied the config at build time — keep its copy in
        # step too (symbol only matters in CRT auto mode, but stale
        # state is stale state).
        self.engine.cfg["symbol"] = symbol
        await self._seed_history(ws)
        self._live_req_id = await deriv_feed.subscribe_ticks(ws, symbol)
        log.info("Subscribed to %s ticks.", symbol)
        return symbol

    async def _stream(self, ws):
        """Consume the tick stream until the connection drops."""
        beat = None
        if config.HEARTBEAT_SEC > 0:
            beat = asyncio.create_task(self._heartbeat_loop())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("msg_type") == "tick" and "tick" in msg:
                    # ignore frames left over from a probe subscription
                    req_id = msg.get("req_id")
                    if req_id is not None and req_id != self._live_req_id:
                        continue
                    t = msg["tick"]
                    if t.get("symbol") == self.symbol:
                        self._on_tick(int(t["epoch"]), float(t["quote"]))
                elif "error" in msg:
                    log.error("Deriv error: %s",
                              msg["error"].get("message"))
        finally:
            if beat is not None:
                beat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beat

    async def run(self):
        import websockets

        asyncio.get_running_loop().set_exception_handler(
            _quiet_connection_drops)

        endpoints = deriv_feed.parse_endpoints(config.WS_ENDPOINTS)
        if not endpoints:
            log.error("WS_ENDPOINTS is empty — nothing to connect to.")
            return
        candidates = deriv_feed.symbol_candidates(
            config.SYMBOL, config.SYMBOL_FALLBACKS)

        backoff = 5
        idx = 0
        preferred = None
        failures = []
        while True:
            if idx % len(endpoints) == 0:
                failures = []
            endpoint = endpoints[idx % len(endpoints)]
            url = deriv_feed.build_ws_url(endpoint, config.DERIV_APP_ID)
            reason = "closed by Deriv"
            live = False
            try:
                log.info("Connecting to Deriv feed (%s) via %s ...",
                         config.SYMBOL, endpoint)
                async with websockets.connect(url, open_timeout=20) as ws:
                    symbol = await self._open_session(ws, candidates)
                    live = True
                    self._connected = True
                    self._endpoint = endpoint
                    self.last_connect_epoch = int(time.time())
                    preferred = idx % len(endpoints)
                    failures = []
                    if self._down:
                        self._down = False
                        self._notify("✅", "Feed connected",
                                     f"Symbol: {symbol}\nEndpoint: {endpoint}\n"
                                     f"Strategy: CRT Pre-Reversal (crash mode)")
                    log.info("Live on %s (%s).", symbol, endpoint)
                    backoff = 5
                    await self._stream(ws)
            except Exception as exc:
                reason = str(exc) or type(exc).__name__
                if live:
                    # A working feed that closes is routine — the public
                    # gateway cuts the connection every 120 seconds.
                    log.info("Feed closed (%s) — reconnecting.", reason)
                else:
                    log.error("Feed error (%s).", reason)
                    failures.append(f"• {endpoint}\n  {reason}")
                    self._down = True
                    preferred = None
            finally:
                if self._connected:
                    self._connected = False
                    self.last_drop_epoch = int(time.time())
            if failures:
                if (idx + 1) % len(endpoints) == 0:
                    self._notify("⚠️", "Feed unreachable",
                                 "Tried every endpoint:\n"
                                 + "\n".join(failures)
                                 + f"\nRetrying in {backoff}s", repeat=True)
                else:
                    self._notify("⚠️", "Feed unreachable",
                                 f"Endpoint: {endpoint}\nReason: {reason}\n"
                                 f"Retrying in {backoff}s", repeat=True)
            # stay on the endpoint that worked, otherwise rotate
            idx = preferred if preferred is not None else idx + 1
            # A feed that was live and simply closed (the public
            # gateway cuts every ~120s) reconnects almost immediately;
            # the backoff ladder is for feeds that never came up.
            await asyncio.sleep(1 if live else backoff)
            backoff = min(backoff * 2, config.RECONNECT_MAX_SEC)
