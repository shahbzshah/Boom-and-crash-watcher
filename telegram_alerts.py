"""Sends alert messages through the user's own Telegram bot."""
import json
import logging
import urllib.error
import urllib.request

import config

log = logging.getLogger("crash_alert.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"


def _clean(detail):
    """Never let the bot token leak into an error string."""
    token = config.TELEGRAM_BOT_TOKEN
    text = str(detail)
    if token and token in text:
        text = text.replace(token, "***")
    return text


def send_alert_detailed(text):
    """Send `text` to the configured chat. Returns (ok, detail) where
    detail is Telegram's own error description on failure — the thing
    that tells you whether the token is wrong, the chat id is wrong,
    or the user never started the bot."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("Telegram not configured — alert dropped: %s", text[:80])
        return False, "Telegram is not configured on the server."
    payload = json.dumps(
        {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _API.format(token=config.TELEGRAM_BOT_TOKEN),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            ok = bool(body.get("ok"))
            if not ok:
                log.error("Telegram API error: %s", body)
                return False, _clean(body.get("description")
                                     or "Telegram rejected the message.")
            return True, "sent"
    except urllib.error.HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            body = json.loads(exc.read().decode("utf-8"))
            if body.get("description"):
                detail = body["description"]
        except Exception:
            pass
        log.error("Telegram send failed: %s", detail)
        return False, _clean(detail)
    except Exception as exc:  # network errors
        log.error("Telegram send failed: %s", exc)
        return False, _clean(f"Network error: {exc}")


def send_alert(text):
    """Send `text` to the configured chat. Returns True on success."""
    ok, _ = send_alert_detailed(text)
    return ok


def format_alert(symbol, reason, current_price, ticks_since_spike, epoch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eat = datetime.fromtimestamp(epoch, tz=ZoneInfo("Africa/Nairobi"))
    eat_time = eat.strftime("%Y-%m-%d %H:%M:%S")
    since = "n/a" if ticks_since_spike is None else str(ticks_since_spike)
    return (
        f"🚨 <b>{symbol} — crash signal</b>\n"
        f"Reason: {reason}\n"
        f"Price: {current_price}\n"
        f"Ticks since last spike: {since}\n"
        f"Time: {eat_time} EAT"
    )


def format_status(icon, title, detail):
    """Feed-status message (connect / drop / heartbeat)."""
    return f"{icon} <b>Crash alert bot — {title}</b>\n{detail}"


_COND_LABELS = (("sweep", "sweep"), ("consecutive", "drift"),
                ("rsi", "RSI"), ("stretch", "stretch"), ("move", "move"))


def format_crt_alert(signal, symbol, tf_label, ctx=None):
    """CRT Pre-Reversal alert, mirroring the MQL Notify() layout
    (side, price, SL, TP) plus the exhaustion-score breakdown."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eat = datetime.fromtimestamp(signal["epoch"],
                                 tz=ZoneInfo("Africa/Nairobi"))
    eat_time = eat.strftime("%Y-%m-%d %H:%M:%S")
    cond = signal.get("conditions") or {}
    parts = [f"{label} {'✓' if cond.get(key) else '✗'}"
             for key, label in _COND_LABELS]
    lines = [
        f"🚨 <b>[CRT] PRE-REVERSAL {signal['side']}</b> — {symbol} ({tf_label})",
        f"Price: {signal['entry']:.2f} | SL: {signal['sl']:.2f} | "
        f"TP: {signal['tp']:.2f}",
        f"Score: {signal['score']}/5 ({' · '.join(parts)})",
    ]
    if ctx:
        phase = ctx.get("phase")
        phase_txt = phase if phase else "—"
        if ctx.get("phase_direction"):
            phase_txt += f" ({ctx['phase_direction']})"
        lines.append(f"Context: trend={ctx.get('trend')} · "
                     f"phase={phase_txt}")
    lines.append(f"Time: {eat_time} EAT")
    return "\n".join(lines)


def format_ema_retest_alert(record, symbol, tf_label):
    """EMA-retest shadow-tier alert. Mirrors format_crt_alert's layout,
    but the honesty note is the point: this tier is backtested, not
    proven, and must not be traded off during the shadow period."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eat = datetime.fromtimestamp(record["epoch"],
                                 tz=ZoneInfo("Africa/Nairobi"))
    eat_time = eat.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🔬 <b>[EMA-RETEST · SHADOW] {record['side']}</b> — "
        f"{symbol} ({tf_label})",
        f"Price: {record['entry']:.2f} | SL: {record['sl']:.2f} | "
        f"TP: {record['tp']:.2f}",
        "EMA6 retest in a downtrend (close < EMA91 < EMA365) · "
        "stop at EMA91 · target 1.5R.",
        "30-day backtest +2.80 expectancy on 24 trades — shadow tier, "
        "not proven, do not trade off it.",
        f"Time: {eat_time} EAT",
    ]
    return "\n".join(lines)


def format_confirmed_alert(record, symbol, tf_label):
    """Confirmation-tier alert: a CRT setup that price itself confirmed
    by displacing through the setup candle's extreme within the
    confirmation window. Mirrors format_crt_alert's layout."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eat = ZoneInfo("Africa/Nairobi")

    def ts(epoch):
        return (datetime.fromtimestamp(epoch, tz=eat)
                .strftime("%Y-%m-%d %H:%M:%S"))

    through = "low" if record["side"] == "SELL" else "high"
    lines = [
        f"🎯 <b>[CRT] CONFIRMED {record['side']}</b> — {symbol} "
        f"({tf_label})",
        f"Price: {record['entry']:.2f} | SL: {record['sl']:.2f} | "
        f"TP: {record['tp']:.2f}",
        f"Displacement confirmed — price closed through the setup "
        f"{through}.",
        f"Setup: {ts(record['setup_epoch'])} EAT · "
        f"Confirmed: {ts(record['epoch'])} EAT",
        "Backtest (30 days, M1): confirmed setups reached TP first "
        "~47% of the time vs ~26% for all setups.",
    ]
    return "\n".join(lines)
