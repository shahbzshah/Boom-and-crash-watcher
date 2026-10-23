"""Sends alert messages through ntfy push notifications.

Free, no account, no BotFather: the bot POSTs the alert to a private
topic on the ntfy server and every phone subscribed to that topic gets
a push. Topic privacy comes from the topic name being unguessable
(think of it as a password in the URL), so use a long random one.

Same interface as telegram_alerts so the rest of the bot does not
care which backend carries the message.
"""
import json
import logging
import re
import urllib.error
import urllib.request

import config

log = logging.getLogger("crash_alert.ntfy")


def _strip_html(text):
    """ntfy phones render plain text — drop Telegram's HTML tags."""
    return re.sub(r"</?[^>]+>", "", str(text))


def send_alert_detailed(text):
    """POST `text` to the configured ntfy topic. Returns (ok, detail)
    like telegram_alerts.send_alert_detailed."""
    topic = config.NTFY_TOPIC
    if not topic:
        log.error("ntfy not configured — alert dropped: %s", text[:80])
        return False, "ntfy is not configured on the server."
    server = (config.NTFY_SERVER or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    body = _strip_html(text).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Title": "Crash 1000 alert",
            "Tags": "chart_with_downwards_trend",
            "Priority": "4",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True, "sent"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = f"HTTP {exc.code}"
        log.error("ntfy send failed: %s", detail)
        return False, f"ntfy rejected the message: {detail[:200]}"
    except Exception as exc:  # network errors
        log.error("ntfy send failed: %s", exc)
        return False, f"Network error: {exc}"


def send_alert(text):
    """Send `text` to the configured topic. Returns True on success."""
    ok, _ = send_alert_detailed(text)
    return ok
