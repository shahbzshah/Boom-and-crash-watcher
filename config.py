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
SYMBOL = _get("SYMBOL", "R_1000")
SPIKE_PCT = _get("SPIKE_PCT", "0.005", float)
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


def validate():
    problems = []
    if not TELEGRAM_BOT_TOKEN or "xxxx" in TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is missing — copy .env.example to .env and fill it in.")
    if not TELEGRAM_CHAT_ID:
        problems.append("TELEGRAM_CHAT_ID is missing — message your bot, then read it from getUpdates (see README).")
    return problems
