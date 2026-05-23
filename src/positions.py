"""跟单模块：每信号固定$10市价买入，凌晨更新收益，SQLite记录"""
import sqlite3
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import httpx

from src import config

FIXED_BET_USD = 10.0


@dataclass
class Position:
    id: int
    market_question: str
    direction: str            # "BUY_YES" or "BUY_NO"
    entry_price: float        # price at signal time
    entry_time: str
    bet_usd: float
    shares: float             # number of shares bought
    current_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    status: str = "open"      # open / closed


def init_positions_db():
    conn = sqlite3.connect("data/positions.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_question TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_time TEXT NOT NULL,
            bet_usd REAL NOT NULL DEFAULT 10.0,
            shares REAL NOT NULL,
            current_price REAL,
            pnl_usd REAL,
            status TEXT NOT NULL DEFAULT 'open',
            headline TEXT,
            source TEXT
        )
    """)
    conn.commit()
    conn.close()


def open_position(question: str, direction: str, entry_price: float, headline: str = "", source: str = "") -> Position:
    """Open a new $10 position at market price."""
    init_positions_db()

    bet_usd = FIXED_BET_USD
    shares = bet_usd / entry_price if entry_price > 0 else 0

    conn = sqlite3.connect("data/positions.db")
    cur = conn.execute("""
        INSERT INTO positions (market_question, direction, entry_price, entry_time, bet_usd, shares, headline, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        question, direction, entry_price,
        datetime.now(timezone.utc).isoformat(),
        bet_usd, round(shares, 4), headline, source,
    ))
    conn.commit()
    pos_id = cur.lastrowid
    conn.close()

    return Position(
        id=pos_id, market_question=question, direction=direction,
        entry_price=entry_price, entry_time=datetime.now(timezone.utc).isoformat(),
        bet_usd=bet_usd, shares=round(shares, 4), status="open",
    )


async def update_all_prices():
    """Update current prices for all open positions."""
    conn = sqlite3.connect("data/positions.db")
    rows = conn.execute("SELECT id, market_question, direction, entry_price, shares FROM positions WHERE status = 'open'").fetchall()
    conn.close()

    updated = 0
    for pos_id, question, direction, entry_price, shares in rows:
        price = await _fetch_current_price(question)
        if price is not None:
            if direction == "BUY_YES":
                pnl = (price - entry_price) * shares
            else:
                pnl = (entry_price - price) * shares

            conn = sqlite3.connect("data/positions.db")
            conn.execute("UPDATE positions SET current_price = ?, pnl_usd = ? WHERE id = ?",
                         (round(price, 4), round(pnl, 4), pos_id))
            conn.commit()
            conn.close()
            updated += 1

    return updated


async def _fetch_current_price(question: str) -> Optional[float]:
    """Fetch current YES price from Gamma API by question keyword match."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{config.GAMMA_BASE}/events", params={"limit": 50, "closed": "false"})
            if resp.status_code != 200:
                return None
            events = resp.json()
            for evt in events:
                for mkt in evt.get("markets", []):
                    if question[:30].lower() in mkt.get("question", "").lower():
                        prices = mkt.get("outcomePrices", "")
                        if isinstance(prices, str) and prices:
                            import json
                            try:
                                prices = json.loads(prices)
                            except Exception:
                                continue
                        if isinstance(prices, list) and len(prices) >= 2:
                            return float(prices[0])
    except Exception:
        pass
    return None


def get_all_positions() -> list[Position]:
    conn = sqlite3.connect("data/positions.db")
    rows = conn.execute("""
        SELECT id, market_question, direction, entry_price, entry_time, bet_usd, shares, current_price, pnl_usd, status, headline
        FROM positions ORDER BY id DESC
    """).fetchall()
    conn.close()

    return [
        Position(
            id=r[0], market_question=r[1], direction=r[2],
            entry_price=r[3], entry_time=r[4], bet_usd=r[5],
            shares=r[6], current_price=r[7], pnl_usd=r[8],
            status=r[9],
        ) for r in rows
    ]


def get_pnl_summary() -> dict:
    positions = get_all_positions()
    open_positions = [p for p in positions if p.status == "open"]
    total_pnl = sum(p.pnl_usd or 0 for p in open_positions)
    total_invested = sum(p.bet_usd for p in open_positions)

    return {
        "total_positions": len(positions),
        "open_positions": len(open_positions),
        "total_invested": round(total_invested, 2),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(total_pnl / total_invested * 100, 2) if total_invested > 0 else 0,
    }


def format_positions_table(positions: list[Position]) -> str:
    """Format positions as pretty table for Telegram."""
    lines = ["📊 跟单日报 | 持仓一览\n"]
    total_pnl = 0
    for p in positions:
        if p.status != "open":
            continue
        pnl_str = f"+${p.pnl_usd:.2f}" if (p.pnl_usd or 0) >= 0 else f"-${abs(p.pnl_usd):.2f}"
        dir_str = "📈YES" if p.direction == "BUY_YES" else "📉NO"
        lines.append(
            f"{dir_str} | 入场${p.entry_price:.2f} → 现${p.current_price or '?'} | {pnl_str}\n"
            f"  {p.market_question[:60]}"
        )
        total_pnl += p.pnl_usd or 0
    lines.append(f"\n💰 总盈亏: {'+$' if total_pnl >= 0 else '-$'}{abs(total_pnl):.2f}")
    return "\n".join(lines)
