"""PolySignal CLI — News-driven Polymarket signal pipeline."""
import asyncio
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("cli")


async def main():
    dry_run = "--live" not in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("""
PolySignal V1 — News-driven Polymarket trading signals

Usage:
  python -m src.cli              Dry run (no real trades)
  python -m src.cli --live       Live trading with real orders
  python -m src.cli --help       This help

Configuration: edit .env file
  ANTHROPIC_API_KEY    Required
  TWITTER_BEARER_TOKEN Optional — real-time news
  TELEGRAM_BOT_TOKEN   Optional — channel monitoring
  POLYMARKET_API_KEY   Required for --live mode
        """)
        return

    from src.pipeline import run_pipeline
    await run_pipeline(dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
