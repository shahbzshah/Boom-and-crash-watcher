"""Entry point: python run.py"""
import asyncio
import logging
import os
import sys
import time

import config
import health
from health import maybe_start_health_server
from watcher import Watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("alert_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("crash_alert")


def main():
    problems = config.validate()
    if problems:
        for p in problems:
            log.error(p)
        if os.environ.get("PORT"):
            # Hosted web service (Render) before the secrets are set:
            # bind the health port and idle instead of exiting, so
            # the deploy succeeds and the platform's port check
            # passes. Saving the env vars restarts the service and
            # the real run begins. Locally (no PORT) keep the old
            # behaviour: complain and exit.
            health.start(int(os.environ["PORT"]))
            log.error("Missing config — idling on the health "
                      "endpoint until the env vars are set.")
            while True:
                time.sleep(3600)
        log.error("Fix your .env (see .env.example), then run again.")
        sys.exit(1)
    if "--test-alert" in sys.argv:
        # Sends one message and exits — use to verify Telegram setup.
        import telegram_alerts

        ok = telegram_alerts.send_alert(
            "✅ <b>Crash 1000 alert bot</b>\nTelegram wiring works. "
            "Now add your signal rule in signals.py and run without --test-alert."
        )
        log.info("Test alert %s.", "sent" if ok else "FAILED")
        sys.exit(0 if ok else 1)
    log.info("Crash 1000 alert bot starting — signals only, no trading.")
    watcher = Watcher()
    # Render free tier sets PORT; everywhere else this is a no-op.
    maybe_start_health_server(lambda: watcher._connected)
    asyncio.run(watcher.run())


if __name__ == "__main__":
    main()
