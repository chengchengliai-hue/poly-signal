"""News classifier: bullish/bearish/neutral via DeepSeek API (OpenAI-compatible)."""
import json
import time
from dataclasses import dataclass

import httpx

from src import config

PROMPT = """You are a prediction market analyst. Classify this news against the market question.

## Market
{question}

## Current YES price: {yes_price:.2f} (market thinks {yes_pct:.0%} probability)

## News
{headline}
Source: {source}

## Task
1. Does this news make the market MORE likely to resolve YES (bullish), NO (bearish), or NO IMPACT (neutral)?
2. Rate MATERIALITY — how much should this news move the market price?
   0.1-0.3 = tangentially relevant, minor impact expected
   0.4-0.7 = strong directional signal, likely to move the market meaningfully
   0.8-1.0 = official/definitive confirmation, outcome is near certain

   Important: Even if you're not 100% sure, strong signals should score in the 0.4-0.7 range.
   DO NOT reserve high scores only for definitive proof.

Respond ONLY with valid JSON:
{{"direction": "bullish" | "bearish" | "neutral", "materiality": <0.0-1.0>, "reasoning": "<1 sentence>"}}"""


@dataclass
class Classification:
    direction: str      # "bullish", "bearish", "neutral"
    materiality: float  # 0.0 - 1.0
    reasoning: str
    latency_ms: int
    model: str


def classify(headline: str, question: str, yes_price: float = 0.5, source: str = "unknown") -> Classification:
    start = time.time()

    prompt = PROMPT.format(
        question=question,
        yes_price=yes_price,
        yes_pct=yes_price,
        headline=headline,
        source=source,
    )

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.1,
                },
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        direction = result.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"

        return Classification(
            direction=direction,
            materiality=float(result.get("materiality", 0)),
            reasoning=str(result.get("reasoning", ""))[:200],
            latency_ms=int((time.time() - start) * 1000),
            model=config.DEEPSEEK_MODEL,
        )

    except Exception as e:
        return Classification(
            direction="neutral",
            materiality=0.0,
            reasoning=f"Classification failed: {str(e)[:100]}",
            latency_ms=int((time.time() - start) * 1000),
            model=config.DEEPSEEK_MODEL,
        )
