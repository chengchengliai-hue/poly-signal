"""Fetch niche Polymarket markets with low volume and high signal potential."""
import asyncio
import httpx
from dataclasses import dataclass, field
from typing import Optional

from src import config

@dataclass
class Market:
    id: str
    question: str
    slug: str
    condition_id: str = ""
    yes_price: float = 0.5
    no_price: float = 0.5
    volume: float = 0.0
    liquidity: float = 0.0
    category: str = ""
    end_date: str = ""
    closed: bool = False
    token_ids: dict = field(default_factory=dict)

@dataclass
class MarketSnapshot:
    market: Market
    timestamp: float
    yes_price: float
    volume_24h: float = 0.0


async def fetch_niche_markets(
    max_volume: float = None,
    min_volume: float = None,
    limit: int = 200
) -> list[Market]:
    """Fetch active markets from Gamma API, filter to niche (low volume, high signal)."""
    if max_volume is None:
        max_volume = config.MAX_VOLUME_USD
    if min_volume is None:
        min_volume = config.MIN_VOLUME_USD

    markets = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{config.GAMMA_BASE}/events",
                params={"limit": limit, "closed": "false"}
            )
            events = resp.json() if resp.status_code == 200 else []
        except Exception:
            return markets

        for evt in events:
            if evt.get("closed", True):
                continue
            vol = float(evt.get("volume", 0))
            if vol < min_volume or vol > max_volume:
                continue

            cat = _extract_category(evt)
            if not _is_signal_category(cat):
                continue

            liq = float(evt.get("liquidity", 0))
            for m in evt.get("markets", []):
                token_ids_raw = m.get("clobTokenIds", "")
                if isinstance(token_ids_raw, str) and token_ids_raw:
                    import json
                    try:
                        token_ids_raw = json.loads(token_ids_raw)
                    except Exception:
                        token_ids_raw = []

                markets.append(Market(
                    id=m.get("id", ""),
                    question=m.get("question", ""),
                    slug=evt.get("slug", ""),
                    condition_id=m.get("conditionId", ""),
                    yes_price=0.5,
                    volume=vol,
                    liquidity=liq,
                    category=cat,
                    end_date=evt.get("endDate", ""),
                    closed=False,
                    token_ids={"yes": token_ids_raw[0] if len(token_ids_raw) > 0 else "",
                               "no": token_ids_raw[1] if len(token_ids_raw) > 1 else ""},
                ))

        return markets


def _extract_category(evt: dict) -> str:
    tags = evt.get("tags", [])
    if tags:
        for tag in tags:
            if isinstance(tag, dict):
                return tag.get("label", tag.get("slug", "")).lower()
    return evt.get("slug", "").lower()


def _is_signal_category(cat: str) -> bool:
    cat = cat.lower()
    for sig in config.SIGNAL_CATEGORIES:
        if sig in cat:
            return True
    return False


async def get_market_price(market_id: str) -> Optional[float]:
    """Get current YES price for a market from CLOB."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{config.CLOB_BASE}/markets/{market_id}")
            if resp.status_code == 200:
                data = resp.json()
                prices = data.get("outcomePrices", [])
                if len(prices) >= 2:
                    return float(prices[0])
        except Exception:
            pass
    return None
