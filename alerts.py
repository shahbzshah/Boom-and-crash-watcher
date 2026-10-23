"""The bot's single alert send path.

Every alert type — legacy signals, CRT setups, confirmed alerts, feed
status, heartbeats, the dashboard test alert — goes through here.
The backend is chosen at send time from the config:

  * NTFY_TOPIC set      -> ntfy push (free app, no BotFather, no token)
  * otherwise           -> Telegram via the user's own bot

So switching delivery needs no code change: set or clear one env var
and every alert type follows, because they all share this path.

Message formatting still lives in telegram_alerts (its format_* helpers
build the text); this module just re-exports them for convenience.
"""
import config
import ntfy_alerts
import telegram_alerts

# Re-exported so call sites keep one import for format + send.
format_alert = telegram_alerts.format_alert
format_status = telegram_alerts.format_status
format_crt_alert = telegram_alerts.format_crt_alert
format_confirmed_alert = telegram_alerts.format_confirmed_alert


def backend_name():
    """Which delivery backend is active right now."""
    if config.NTFY_TOPIC:
        return "ntfy"
    return "telegram"


def send_alert_detailed(text):
    """Send `text` through the active backend. Returns (ok, detail)
    where detail is the backend's own error description on failure."""
    if config.NTFY_TOPIC:
        return ntfy_alerts.send_alert_detailed(text)
    return telegram_alerts.send_alert_detailed(text)


def send_alert(text):
    """Send `text` through the active backend. Returns True on success."""
    ok, _ = send_alert_detailed(text)
    return ok
