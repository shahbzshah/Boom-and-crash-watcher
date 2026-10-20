# Crash 1000 Signal Alert Bot

Watches the Deriv Crash 1000 tick feed, runs **your CRT Pre-Reversal
Engine** on every closed candle, and sends a Telegram message with
entry / SL / TP the moment a drift looks exhausted.
It never trades — signals only.

## The strategy (`crt_engine.py`)

A faithful port of `CRT_PreReversal_Engine.mq5` (v8.10) — the source you
sent — with every input default preserved (`CRT_*` in `.env`). In short:
a signal needs **4 of 5 exhaustion conditions** (CRT sweep of the last
3 bars' extreme, ≥3 consecutive drift candles, RSI extreme, ≥1.5 ATR
stretch from EMA20, ≥6 ATR move over 30 bars), gated by the spike-cycle
filter (never on the spike candle itself, ≥2 drift bars after the last
spike, max 1 signal per cycle, 4-bar cooldown). SL sits 5 ATR beyond the
extreme; TP is 0.8 × the average recent spike (or 2R when no spike was
found). Mode is `crash` — SELL signals only, like the indicator's AUTO
mode on a Crash symbol.

It runs on the bot's candles (`CANDLE_MINUTES`, default **M1** — set it
to match the timeframe of your MT5 chart). At startup it downloads the
last 1000 candles from Deriv and replays them silently, so the engine's
~252-bar warmup and cycle state are in place immediately instead of
after hours of live ticks.

Still in `ctx` for context or future rules: the market-structure block
(trend / AMD phase / sweeps from `structure.py`) and the VIDYA /
Volumatic blocks — the structure phase/trend ride along in each CRT
alert. The old Volumatic default rule in `signals.py` is now an inert
extra hook (the CRT engine is the strategy; the hook exists only if you
want additional per-tick conditions later).

## Setup (on your Windows PC)

1. Copy this folder to your PC.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill it in:
   - `TELEGRAM_BOT_TOKEN` — from @BotFather. **Never paste it in chat.**
   - `TELEGRAM_CHAT_ID` — message your bot first, then open
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
     copy the `chat.id`. (Or ask @userinfobot.)
   - If the token was ever pasted into a chat, log, or screenshot, revoke
     it in @BotFather (`/revoke`) and put the new one in `.env` — treat
     an exposed token as compromised.
4. The strategy is already encoded (`crt_engine.py`) with your MQL
   defaults — only touch the `CRT_*` values in `.env` if your MT5 chart
   runs different inputs.
5. Run: `python run.py` — leave it running while you trade.

## Feed resilience

Deriv renamed the synthetic symbols when it moved to its current API:
Crash 1000 is `CRASH1000` there, while the older clusters still call it
`R_1000`. The bot probes both, so it works on either generation.

Deriv also publishes several WebSocket endpoints behind one CDN, and they
fail independently — a cluster can answer `ping` while refusing every
tick subscription. The bot therefore does not trust any single one:

- **Endpoint failover** — `WS_ENDPOINTS` (comma separated) is tried in
  order. Deriv's public market-data gateway
  (`wss://api.derivws.com/trading/v1/options/ws/public`, no
  authentication needed) is listed first because it is the most
  reliable; the legacy clusters follow as backup.
- **Symbol negotiation** — after connecting, the bot probes the
  candidate symbols with a throwaway tick subscription and keeps the
  first one the endpoint can actually stream. `SYMBOL` is tried first,
  then `SYMBOL_FALLBACKS` (`R_1000` — the legacy code for the *same*
  instrument, not a different one).
- **Short-lived connections** — the public gateway closes a connection
  after 120 seconds. The bot treats that as routine: it reconnects to the
  endpoint that just worked, without sending a message.
- **Status messages** — with `NOTIFY_FEED_STATUS=1` the bot tells
  Telegram when the feed comes up and when it stays unreachable (at most
  every `NOTIFY_REPEAT_SEC`, 15 minutes by default).
  `HEARTBEAT_SEC` (default `0`, off) adds a periodic "bot is alive"
  message with price, trend and the current CRT score.

## The rule context (`ctx`)

Every candle close refreshes a context dict that the CRT alert and the
optional `signals.py` hook can read:

- Spike block: `ticks_since_spike`, `last_spike_price`,
  `last_spike_epoch`, `current_price` (tick-level tracker).

Helpers provided:
- `detect_spike(prev_price, price)` — True when one tick jumps up by more
  than `SPIKE_PCT` (default 0.5%). Tune it in `.env`.
- `drawdown_from_high(ticks, lookback)` — how far price has fallen from the
  highest tick in the last `lookback` ticks, as a fraction (e.g. 0.02 = 2%).

Your rule also sees the market-structure block in `ctx` (see `structure.py`):
- `trend`: "uptrend" | "downtrend" | "ranging" (swings + BOS/CHoCH)
- `phase`: "accumulation" | "manipulation" | "distribution" | None
- `phase_direction`: "bullish" | "bearish" | None
- `last_sweep`: {"side": "high"|"low", "level", "extreme", "epoch"}
- `swing_high` / `swing_low`, `range_high` / `range_low`, `atr`, `last_break`
- `vidya` — standard Variable Index Dynamic Average of the 1m closes
  (period 9, tuneable via `VIDYA_PERIOD`); `vidya_vol` — volume-modulated
  variant (tick counts as volume), for VIDYA-style strategies
- `vol_trend` (+1 bullish / -1 bearish / None), `vol_trend_bars` (candles
  since the last flip, 0 = just flipped), `vol_vidya`, `vol_upper`,
  `vol_lower` — the Volumatic VIDYA trend engine (see `volumatic.py`);
  tune with `VOL_ATR_PERIOD` / `VOL_ATR_MULT`

## Alert behaviour

- CRT alerts fire on the **close of the signal candle** (the MQL
  indicator notifies on the just-closed bar, i == 1 — same here).
- Spacing is governed by the strategy itself: max 1 signal per spike
  cycle and a 4-candle cooldown. No extra bot-level cooldown is applied
  to CRT alerts.
- Every CRT alert shows: side, entry price, SL, TP, the 5-condition
  score breakdown, the structure trend/phase, and the time (EAT).
- The optional `signals.py` hook keeps its own behaviour (arm/disarm per
  tick-spike cycle + `ALERT_COOLDOWN_SEC` safety net), but it ships OFF.

## Files

- `run.py` — main loop: connects to Deriv, subscribes to the crash
  ticks, reconnects automatically if the feed drops.
  `python run.py --test-alert` sends one Telegram test message and exits.
- `watcher.py` — tick buffer + spike tracking; rebuilds candles on each
  tick, seeds history at startup, and runs the CRT engine on every
  candle close. Owns the endpoint failover loop and the status messages.
- `deriv_feed.py` — Deriv WebSocket helpers: endpoint URL building,
  symbol probing/negotiation, candle fetching.
- `crt_engine.py` — **your strategy**: the CRT Pre-Reversal Engine port
  (exhaustion score, spike-cycle filter, SL/TP risk engine).
- `structure.py` — market-structure engine: candles from ticks, fractal
  swings, trend (BOS/CHoCH), liquidity sweeps, AMD phase.
- `signals.py` — optional extra per-tick rule hook (ships OFF).
- `telegram_alerts.py` — sends the Telegram message.
- `config.py` — reads `.env`.
- `test_*.py` — offline test suites (62 tests).
- `alert_bot.log` — log file (created on first run).
