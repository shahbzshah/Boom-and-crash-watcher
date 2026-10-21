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
import signals
import structure
import telegram_alerts
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

        text = telegram_alerts.format_alert(
            self.symbol, reason, price, self.ctx["ticks_since_spike"], epoch
        )
        if telegram_alerts.send_alert(text):
            log.warning("ALERT SENT: %s", reason)
            self.armed = False  # one alert per cycle
            self.last_alert_ts = now
        else:
            log.error("Alert failed to send — staying armed to retry next signal.")

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

    def _on_candle_closed(self):
        """A candle just closed: refresh ctx, then run the CRT engine
        and send any signal it fires straight to Telegram."""
        self._refresh_context()
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
            text = telegram_alerts.format_crt_alert(
                sig, self.symbol, f"M{config.CANDLE_MINUTES}", self.ctx)
            if telegram_alerts.send_alert(text):
                log.warning("CRT ALERT SENT: %s score %s entry %s",
                            sig["side"], sig["score"], sig["entry"])
            else:
                log.error("CRT alert failed to send (%s).", sig["side"])

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
        text = telegram_alerts.format_status(icon, title, detail)
        if telegram_alerts.send_alert(text):
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
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, config.RECONNECT_MAX_SEC)
