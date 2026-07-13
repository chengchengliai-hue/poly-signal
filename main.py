#!/usr/bin/env python3
"""Poly Signal v2 — Polymarket smart money monitor + copy trade."""
import logging
import threading
import signal
import sys
import time

from db import init as db_init, gc as db_gc
from poller import poll_trades
from bot import poll_bot
from handler import handle_trade, handle_bot_update
from settle import settlement_loop
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("main")


def gc_loop():
    while True:
        time.sleep(300)
        try:
            db_gc()
        except Exception as e:
            log.error(f"gc error: {e}")


def main():
    db_init()
    log.info("Poly Signal v2 starting...")

    # Background threads
    threading.Thread(target=gc_loop, daemon=True).start()
    threading.Thread(target=settlement_loop, daemon=True).start()
    threading.Thread(target=poll_bot, args=(handle_bot_update,), daemon=True).start()

    # Signal handler for graceful shutdown
    def shutdown(sig, frame):
        log.info("shutting down...")
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Main loop: poll trades
    log.info("starting trade poller...")
    poll_trades(handle_trade)


if __name__ == "__main__":
    main()
