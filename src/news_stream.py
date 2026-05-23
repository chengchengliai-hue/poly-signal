"""Real-time news streams: Twitter filtered stream, Telegram channel monitoring."""
import asyncio
import time
import logging

from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class NewsEvent:
    headline: str
    source: str       # "twitter", "telegram", "rss"
    url: str
    timestamp: float
    latency_ms: int   # ms from publish to capture


class NewsStream:
    """Async news source aggregator."""

    def __init__(self):
        self._queue: asyncio.Queue[NewsEvent] = asyncio.Queue(maxsize=500)
        self._running = False

    async def start(self):
        """Start all news sources."""
        self._running = True
        tasks = []

        if config.TWITTER_BEARER_TOKEN:
            tasks.append(asyncio.create_task(self._twitter_listen()))

        if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHANNEL_IDS:
            tasks.append(asyncio.create_task(self._telegram_listen()))

        if not tasks:
            log.warning("No news sources configured. Set TWITTER_BEARER_TOKEN or TELEGRAM_BOT_TOKEN.")

        tasks.append(asyncio.create_task(self._rss_fallback()))
        log.info(f"NewsStream started with {len(tasks)} sources")
        await asyncio.gather(*tasks, return_exceptions=True)

    async def next_event(self) -> NewsEvent:
        """Get the next news event (blocks until available)."""
        return await self._queue.get()

    def event_ready(self) -> bool:
        return not self._queue.empty()

    async def _twitter_listen(self):
        """Twitter API v2 filtered stream for breaking news accounts."""
        try:
            import tweepy
            client = tweepy.StreamingClient(
                bearer_token=config.TWITTER_BEARER_TOKEN,
                wait_on_rate_limit=True,
            )

            # Follow key news accounts
            from tweepy import StreamRule
            rules = [
                StreamRule("from:Reuters OR from:Bloomberg OR from:CNBC OR from:WSJ"),
                StreamRule("from:Polymarket OR from:Kalshi OR from:PredictIt"),
                StreamRule("from:zerohedge OR from:unusual_whales"),
            ]
            for rule in rules:
                try:
                    client.add_rules(rule)
                except Exception:
                    pass

            class TweetPrinter(tweepy.StreamingClient):
                def __init__(self, bearer_token, queue):
                    super().__init__(bearer_token)
                    self._queue = queue

                def on_data(self, raw_data):
                    import json
                    try:
                        data = json.loads(raw_data)
                        text = data.get("data", {}).get("text", "")
                        if text and len(text) > 20:
                            self._queue.put_nowait(NewsEvent(
                                headline=text[:300],
                                source="twitter",
                                url=f"https://twitter.com/i/status/{data.get('data', {}).get('id', '')}",
                                timestamp=time.time(),
                                latency_ms=0,
                            ))
                    except Exception:
                        pass

            stream = TweetPrinter(config.TWITTER_BEARER_TOKEN, self._queue)
            stream.filter(tweet_fields=["created_at"])
        except ImportError:
            log.warning("tweepy not installed — Twitter stream disabled")
        except Exception as e:
            log.error(f"Twitter stream error: {e}")
            await asyncio.sleep(30)

    async def _telegram_listen(self):
        """Monitor Telegram channels for breaking news."""
        try:
            while self._running:
                for channel_id in config.TELEGRAM_CHANNEL_IDS:
                    try:
                        from httpx import AsyncClient
                        async with AsyncClient(timeout=10) as client:
                            resp = await client.get(
                                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
                                params={"offset": -1, "limit": 5, "timeout": 30}
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                for update in data.get("result", []):
                                    msg = update.get("message", update.get("channel_post", {}))
                                    text = msg.get("text", "")
                                    if text and len(text) > 20:
                                        self._queue.put_nowait(NewsEvent(
                                            headline=text[:300],
                                            source="telegram",
                                            url=f"https://t.me/c/{msg.get('chat', {}).get('id', '')}/{msg.get('message_id', '')}",
                                            timestamp=time.time(),
                                            latency_ms=0,
                                        ))
                    except Exception:
                        pass
                await asyncio.sleep(5)
        except Exception as e:
            log.error(f"Telegram listener error: {e}")

    async def _rss_fallback(self):
        """RSS fallback — scrapes every 60s. Much slower but always available."""
        import feedparser
        feeds = [
            "https://feeds.reuters.com/reuters/worldNews",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        ]
        while self._running:
            for feed_url in feeds:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries[:3]:
                        headline = entry.get("title", "")
                        if headline and len(headline) > 20:
                            self._queue.put_nowait(NewsEvent(
                                headline=headline[:300],
                                source="rss",
                                url=entry.get("link", ""),
                                timestamp=time.time(),
                                latency_ms=0,
                            ))
                except Exception:
                    pass
            await asyncio.sleep(60)

import config
