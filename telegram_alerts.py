"""Sends alert messages through the user's own Telegram bot."""
import json
import logging
import urllib.request

import config

log = logging.getLogger("crash_alert.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(text):
    """Send `text` to the configured chat. Returns True on success."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("Telegram not configured — alert dropped: %s", text[:80])
        return False
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
            return ok
    except Exception as exc:  # network / HTTP errors
        log.error("Telegram send failed: %s", exc)
        return False


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
