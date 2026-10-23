"""Configuration — reads from environment / .env file."""
import os


def _get(name, default, cast=str):
    raw = os.environ.get(name, default)
    try:
        return cast(raw)
    except (ValueError, TypeError):
        return cast(default)


# Optional: load a .env file sitting next to this code, if python-dotenv
# is installed. Not required — plain environment variables work too.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID", "")

# --- Notification backend ---
# Set NTFY_TOPIC to route ALL alerts (setups, confirmed, feed status,
# heartbeats, test alerts) through ntfy push instead of Telegram — see
# alerts.py. Free, no account: the bot POSTs to this private topic on
# the ntfy server and every phone subscribed to it gets a push. The
# topic name is the only secret (unguessable = private); use a long
# random one. Clear it to fall back to Telegram.
NTFY_TOPIC = _get("NTFY_TOPIC", "")
NTFY_SERVER = _get("NTFY_SERVER", "https://ntfy.sh")
SYMBOL = _get("SYMBOL", "CRASH1000")
# Deriv websocket app_id. 1089 is Deriv's public demo app — it works
# out of the box for market data; register your own free app at
# api.deriv.com for your own rate limits and put its id here.
DERIV_APP_ID = _get("DERIV_APP_ID", "1089")

# Dashboard access key: when set, /status.json and /test-alert require
# it (the page itself is just a shell with no data). Set it in the
# host's environment on public deployments — the dashboard shows live
# strategy output, so an open link would publish it.
DASHBOARD_KEY = _get("DASHBOARD_KEY", "")
SPIKE_PCT = _get("SPIKE_PCT", "0.005", float)

# --- Deriv connection ---
# Deriv renamed the synthetic symbols in its current API: Crash 1000 is
# `CRASH1000` there, while the legacy clusters still use `R_1000`. The
# bot probes both, so it works on either generation of the API.
#
# Deriv runs several regional WebSocket clusters behind one CDN and they
# fail independently. The bot tries them in order until one answers.
# The first entry is Deriv's public market-data gateway, which needs no
# authentication and is the most reliable of the lot.
WS_ENDPOINTS = _get(
    "WS_ENDPOINTS",
    "wss://api.derivws.com/trading/v1/options/ws/public,"
    "wss://ws.derivws.com/websockets/v3,"
    "wss://ws.binaryws.com/websockets/v3,"
    "wss://green.derivws.com/websockets/v3,"
    "wss://blue.derivws.com/websockets/v3,"
    "wss://red.derivws.com/websockets/v3",
    str,
)
# Symbols to try when the configured one cannot be streamed. The default
# is the legacy code for the very same instrument, not a different one.
SYMBOL_FALLBACKS = _get("SYMBOL_FALLBACKS", "R_1000", str)
# Seconds between "bot is alive" Telegram messages (0 = off).
HEARTBEAT_SEC = _get("HEARTBEAT_SEC", "0", int)
# Send a Telegram message when the feed connects or drops.
NOTIFY_FEED_STATUS = _get("NOTIFY_FEED_STATUS", "1", int)
# Minimum seconds between two "feed unreachable" reminders.
NOTIFY_REPEAT_SEC = _get("NOTIFY_REPEAT_SEC", "900", int)
# Reconnect backoff ceiling in seconds.
RECONNECT_MAX_SEC = _get("RECONNECT_MAX_SEC", "120", int)
ALERT_COOLDOWN_SEC = _get("ALERT_COOLDOWN_SEC", "300", int)
TICK_BUFFER = _get("TICK_BUFFER", "5000", int)

# --- Market-structure engine ---
CANDLE_MINUTES = _get("CANDLE_MINUTES", "1", int)
CANDLE_KEEP = _get("CANDLE_KEEP", "1000", int)
# Historical candles fetched from Deriv at startup so the CRT engine
# has its ~252-bar warmup immediately (the last, still-forming candle
# of the response is dropped; live ticks rebuild it).
HISTORY_CANDLES = _get("HISTORY_CANDLES", "1000", int)
SWING_LEFT = _get("SWING_LEFT", "3", int)
SWING_RIGHT = _get("SWING_RIGHT", "3", int)
TREND_LOOKBACK = _get("TREND_LOOKBACK", "120", int)
SWEEP_LOOKBACK = _get("SWEEP_LOOKBACK", "20", int)
SWEEP_EXCLUDE = _get("SWEEP_EXCLUDE", "5", int)
SWEEP_SCAN = _get("SWEEP_SCAN", "5", int)
ACCUM_BARS = _get("ACCUM_BARS", "60", int)
ACCUM_ATR_MULT = _get("ACCUM_ATR_MULT", "2.5", float)
MANIP_BARS = _get("MANIP_BARS", "10", int)
DIST_BARS = _get("DIST_BARS", "15", int)

# --- VIDYA indicator ---
VIDYA_PERIOD = _get("VIDYA_PERIOD", "9", int)

# --- Volumatic VIDYA trend engine (reconstruction of the public spec) ---
# CMO/VIDYA core reuses VIDYA_PERIOD above.
VOL_ATR_PERIOD = _get("VOL_ATR_PERIOD", "14", int)
VOL_ATR_MULT = _get("VOL_ATR_MULT", "2.0", float)

# --- CRT Pre-Reversal Engine (port of CRT_PreReversal_Engine.mq5) ---
# Every default below matches the MQL input default. This bot is for
# Crash 1000, so the mode defaults to "crash" (SELL signals only) —
# "auto" would need CRASH/BOOM in the symbol name, which R_1000 lacks.
CRT_ASSET_MODE = _get("CRT_ASSET_MODE", "crash")
CRT_MIN_SCORE = _get("CRT_MIN_SCORE", "4", int)
CRT_RANGE_BARS = _get("CRT_RANGE_BARS", "3", int)
CRT_MIN_CONSECUTIVE = _get("CRT_MIN_CONSECUTIVE", "3", int)
CRT_RSI_PERIOD = _get("CRT_RSI_PERIOD", "14", int)
CRT_RSI_LEVEL = _get("CRT_RSI_LEVEL", "30.0", float)
CRT_STRETCH_EMA = _get("CRT_STRETCH_EMA", "20", int)
CRT_STRETCH_ATR = _get("CRT_STRETCH_ATR", "1.5", float)
CRT_DROP_LOOKBACK = _get("CRT_DROP_LOOKBACK", "30", int)
CRT_DROP_ATR = _get("CRT_DROP_ATR", "6.0", float)
CRT_SPIKE_ATR_MULT = _get("CRT_SPIKE_ATR_MULT", "3.0", float)
CRT_MIN_DRIFT_BARS = _get("CRT_MIN_DRIFT_BARS", "2", int)
CRT_MAX_SIGNALS_PER_CYCLE = _get("CRT_MAX_SIGNALS_PER_CYCLE", "1", int)
CRT_COOLDOWN_BARS = _get("CRT_COOLDOWN_BARS", "4", int)
CRT_SPIKE_SCAN_DEPTH = _get("CRT_SPIKE_SCAN_DEPTH", "200", int)
CRT_SL_ATR_MULT = _get("CRT_SL_ATR_MULT", "5.0", float)
CRT_TP_MODE = _get("CRT_TP_MODE", "avg_spike")  # avg_spike | rr
CRT_TP_SPIKE_FRAC = _get("CRT_TP_SPIKE_FRAC", "0.8", float)
CRT_RR = _get("CRT_RR", "2.0", float)
# Confirmation tier: a fired setup is CONFIRMED when, within this many
# bars, a candle displaces through the setup candle's extreme in the
# trade direction (down candle closing below the setup low for a SELL).
# Backtest 2026-10-22 (44k M1 candles, split-half): confirmed setups
# reached TP first ~47% of the time vs ~26% for all setups — a
# precision tier for discretionary entries, not an auto-trade recipe.
CRT_CONFIRM_WINDOW = _get("CRT_CONFIRM_WINDOW", "3", int)


def validate():
    problems = []
    # Either a working Telegram setup or an ntfy topic counts as a
    # configured notification backend (see alerts.py for routing).
    if NTFY_TOPIC:
        return problems
    if not TELEGRAM_BOT_TOKEN or "xxxx" in TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is missing — copy .env.example to .env and fill it in.")
    if not TELEGRAM_CHAT_ID:
        problems.append("TELEGRAM_CHAT_ID is missing — message your bot, then read it from getUpdates (see README).")
    if not NTFY_TOPIC:
        problems.append("(or set NTFY_TOPIC for free ntfy push alerts instead of Telegram)")
    return problems
