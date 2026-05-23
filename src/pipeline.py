"""
PolySignal V1 — News-driven Polymarket signal & execution pipeline.

Architecture:
  News Stream (Twitter/Telegram/RSS)
    → Market Matcher (keyword search cached markets)
    → Claude Classifier (bullish/bearish/neutral)
    → Edge Detector (Claude vs market price + Kelly sizing)
    → Executor (Polymarket CLOB + Telegram alert)
"""

import asyncio
import logging
import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel

from src.config import (
    DEEPSEEK_API_KEY, TG_BOT_TOKEN, TG_CHAT_ID,
    POLYMARKET_API_KEY, MAX_BET_USD, EDGE_THRESHOLD,
)
from src.markets import fetch_niche_markets, Market, get_market_price
from src.classifier import classify
from src.edge import detect_edge, Signal
from src.positions import open_position, update_all_prices, get_pnl_summary, format_positions_table
from src.executor import execute_trade, log_trade, send_telegram
from src.news_stream import NewsStream, NewsEvent

log = logging.getLogger(__name__)
console = Console()

# Stats
stats = {
    "news_processed": 0,
    "signals_found": 0,
    "trades_executed": 0,
    "start_time": time.time(),
    "last_signal": None,
}


async def run_pipeline(dry_run: bool = True):
    """Main event-driven pipeline."""
    console.print(Panel.fit(
        "[bold green]PolySignal V1[/]\n"
        f"DeepSeek: {'✓' if DEEPSEEK_API_KEY else '✗'}  "
        f"Telegram: {'✓' if TG_BOT_TOKEN else '✗'}  "
        f"Polymarket: {'✓' if POLYMARKET_API_KEY else '✗ (dry_run)'}\n"
        f"Max bet: ${MAX_BET_USD}  Edge threshold: {EDGE_THRESHOLD}",
        title="Pipeline Status"
    ))

    # Preload niche markets (refreshed every 5 min)
    markets: list[Market] = []
    markets_updated = 0.0

    # Start news stream
    stream = NewsStream()
    stream_task = asyncio.create_task(stream.start())

    # Start market refresher
    async def refresh_markets():
        nonlocal markets, markets_updated
        while True:
            markets = await fetch_niche_markets()
            markets_updated = time.time()
            log.info(f"Markets refreshed: {len(markets)} niche markets")
            await asyncio.sleep(300)  # every 5 min

    market_task = asyncio.create_task(refresh_markets())

    # Midnight P&L updater
    async def midnight_pnl_update():
        import asyncio as aio
        while True:
            now = datetime.now()
            # Wait until 00:05
            next_run = now.replace(hour=0, minute=5, second=0, microsecond=0)
            if next_run <= now:
                next_run = next_run.replace(day=now.day + 1)
            wait_sec = (next_run - now).total_seconds()
            await aio.sleep(wait_sec)
            updated = await update_all_prices()
            summary = get_pnl_summary()
            log.info(f'PNL update: {updated} positions, total PNL ${summary["total_pnl"]}')
            if summary['open_positions'] > 0:
                positions = get_all_positions()
                table = format_positions_table([p for p in positions if p.status == 'open'])
                await send_telegram(table)

    pnl_task = asyncio.create_task(midnight_pnl_update())

    # Main loop: process news events
    while True:
        try:
            event: NewsEvent = await stream.next_event()
            stats["news_processed"] += 1

            if len(markets) == 0:
                continue

            # Match headline to markets (keyword overlap)
            headline_lower = event.headline.lower()
            matched = [
                m for m in markets
                if any(word in m.question.lower() or word in m.slug.lower()
                       for word in headline_lower.split() if len(word) > 3)
            ]
            if not matched:
                continue

            # Take top 3 best matches
            for market in matched[:3]:
                # Claude classification
                classification = classify(
                    headline=event.headline,
                    question=market.question,
                    yes_price=market.yes_price,
                    source=event.source,
                )

                if classification.direction == "neutral":
                    continue

                # Edge detection
                signal = detect_edge(
                    headline=event.headline,
                    classification=classification,
                    market=market,
                    source=event.source,
                )

                if signal is None:
                    continue

                stats["signals_found"] += 1
                stats["last_signal"] = signal

                # Open follow position ( fixed)
                pos = open_position(
                    question=signal.market.question,
                    direction=signal.direction,
                    entry_price=signal.market.yes_price if signal.direction == 'BUY_YES' else (1.0 - signal.market.yes_price),
                    headline=signal.headline,
                    source=signal.source,
                )

                # Display
                display_signal(signal, pos)

                # Execute
                result = {"status": "dry_run"}
                if not dry_run and POLYMARKET_API_KEY:
                    result = execute_trade(signal)
                    if result.get("status") in ("submitted", "filled"):
                        stats["trades_executed"] += 1

                # Log
                log_trade(signal, result)

                # Telegram alert
                tg_msg = build_telegram_message(signal, result)
                await send_telegram(tg_msg)

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            await asyncio.sleep(1)


def display_signal(signal: Signal, pos=None):
    """Print signal to console."""
    color = "green" if signal.direction == "BUY_YES" else "red"
    direction_str = "📈 BUY YES" if signal.direction == "BUY_YES" else "📉 BUY NO"

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("k", style="dim")
    table.add_column("v")
    table.add_row("Market", signal.market.question)
    table.add_row("Direction", f"[bold {color}]{direction_str}[/]")
    table.add_row("Edge", f"{signal.edge:.3f} (materiality: {signal.claude_materiality:.2f})")
    table.add_row("Bet", f"$10 (固定跟单) → {pos.shares:.0f} shares" if pos else f"${signal.bet_amount_usd}")
    table.add_row("News", signal.headline[:150])
    table.add_row("Source", signal.source)
    table.add_row("Reasoning", signal.reasoning)
    console.print(table)
    console.print("─" * 60)


def build_telegram_message(signal: Signal, result: dict) -> str:
    status_emoji = {"submitted": "🟢", "filled": "🟢", "dry_run": "🧪", "failed": "⚠️"}
    status_text = {"submitted": "已开仓", "filled": "已开仓", "dry_run": "模拟跟单", "failed": "失败"}
    emoji = status_emoji.get(result.get("status", "dry_run"), "🧪")
    status_cn = status_text.get(result.get("status", "dry_run"), "未知")

    direction_emoji = "📈" if signal.direction == "BUY_YES" else "📉"
    direction_cn = "买YES看多" if signal.direction == "BUY_YES" else "买NO看空"
    return (
        f"{emoji} 跟单信号 {direction_emoji}\n\n"
        f"市场：{signal.market.question}\n"
        f"方向：{direction_cn}\n"
        f"跟单：$10 @ ${signal.market_price:.2f}\n"
        f"信号强度：{signal.claude_materiality:.2f}（偏差{signal.edge:.3f}）\n"
        f"新闻：{signal.headline[:200]}\n"
        f"来源：{signal.source}\n"
        f"理由：{signal.reasoning}\n"
        f"时间：{datetime.utcnow().strftime('%m/%d %H:%M')}"
    )
