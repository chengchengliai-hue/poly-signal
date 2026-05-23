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
  python -m src.cli --backtest   Generate backtest report
  python -m src.cli --csv        Export positions to CSV
  python -m src.cli --help       This help

Configuration: edit .env file
  DEEPSEEK_API_KEY     Required
  TWITTER_BEARER_TOKEN Optional — real-time news
  TELEGRAM_BOT_TOKEN   Optional — channel monitoring
  POLYMARKET_API_KEY   Optional — real trading
        """)
        return

    if "--backtest" in sys.argv:
        from src.analytics import generate_report, format_report, export_csv
        report = generate_report()
        print(format_report(report))
        export_csv()
        print("CSV导出: data/backtest_report.csv")
        return

    if "--csv" in sys.argv:
        from src.analytics import export_csv
        f = export_csv()
        print(f"CSV导出: {f}")
        return

    from src.pipeline import run_pipeline
    await run_pipeline(dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
