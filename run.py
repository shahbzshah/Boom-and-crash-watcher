"""Entry point: python run.py"""
import asyncio
import logging
import sys

import config
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
    asyncio.run(Watcher().run())


if __name__ == "__main__":
    main()
