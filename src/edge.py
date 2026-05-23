"""Edge detection: Claude score vs market price, with Kelly position sizing."""
from dataclasses import dataclass, field
from typing import Optional
import math

from src.classifier import Classification
from src.markets import Market
from src import config


@dataclass
class Signal:
    market: Market
    direction: str           # "BUY_YES", "BUY_NO"
    claude_materiality: float
    market_price: float
    edge: float              # |classification - market price|
    bet_amount_usd: float
    reasoning: str
    headline: str
    source: str
    news_latency_ms: int = 0
    total_latency_ms: int = 0
    tags: list = field(default_factory=list)


def detect_edge(
    headline: str,
    classification: Classification,
    market: Market,
    source: str = "unknown",
    news_latency_ms: int = 0,
) -> Optional[Signal]:
    """Detect edge: compare Claude's classification against market price."""

    if classification.direction == "neutral":
        return None

    if classification.materiality < config.MATERIALITY_THRESHOLD:
        return None

    # Determine trade direction and expected price
    if classification.direction == "bullish":
        # Bullish → YES is more likely → if market YES < our target, buy YES
        target_price = min(0.5 + classification.materiality * 0.5, 0.95)
        edge = target_price - market.yes_price
        if edge <= config.EDGE_THRESHOLD:
            return None
        direction = "BUY_YES"
        bet_side_price = market.yes_price
    else:
        # Bearish → NO is more likely → if market NO < our target, buy NO
        target_price = min(0.5 + classification.materiality * 0.5, 0.95)
        no_price = 1.0 - market.yes_price
        edge = target_price - no_price
        if edge <= config.EDGE_THRESHOLD:
            return None
        direction = "BUY_NO"
        bet_side_price = no_price

    # Kelly sizing: bet_amount = bankroll * edge / odds
    # Quarter-Kelly for safety
    kelly_fraction = edge / (1.0 - bet_side_price) if bet_side_price < 1.0 else edge
    kelly_fraction *= 0.25  # quarter-Kelly
    bet_amount = min(config.MAX_BET_USD, config.MAX_BET_USD * kelly_fraction)
    bet_amount = max(2.0, bet_amount)  # min $2

    return Signal(
        market=market,
        direction=direction,
        claude_materiality=classification.materiality,
        market_price=market.yes_price,
        edge=edge,
        bet_amount_usd=round(bet_amount, 2),
        reasoning=classification.reasoning,
        headline=headline,
        source=source,
        news_latency_ms=news_latency_ms,
        tags=["high_signal", classification.direction],
    )
