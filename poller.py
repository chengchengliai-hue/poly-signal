import time
import json
import logging
import statistics
import urllib.request
from collections import OrderedDict
from config import (DATA_API_KEY, MIN_TRADE_USDC, CLOB_URL, CHAIN_ID,
                    TEST_HEAVY_MIN_USDC, TEST_HEAVY_MEDIAN_MULTIPLE,
                    TEST_HEAVY_MIN_ORDERS, TEST_HEAVY_MAX_ORDERS,
                    SIGNAL_CONFIRM_DELAY_SECONDS)

DATA_API = "https://data-api.polymarket.com"
POLL_INTERVAL = 10  # seconds
RECENT_TRADE_CACHE_SIZE = 100_000

log = logging.getLogger("poller")
_clob_read_client = None
_recent_trade_hashes = OrderedDict()
_pending_trades = OrderedDict()


def _remember_trade(tx_hash: str) -> bool:
    """Return True once per recent transaction without growing SQLite."""
    if tx_hash in _recent_trade_hashes:
        _recent_trade_hashes.move_to_end(tx_hash)
        return False
    _recent_trade_hashes[tx_hash] = None
    if len(_recent_trade_hashes) > RECENT_TRADE_CACHE_SIZE:
        _recent_trade_hashes.popitem(last=False)
    return True


def _is_recent_trade(tx_hash: str) -> bool:
    return tx_hash in _recent_trade_hashes


def _to_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return {}


def _to_float(value, default=0):
    try:
        if isinstance(value, dict):
            for key in ("price", "mid", "midpoint", "value"):
                if key in value:
                    return float(value[key])
        return float(value)
    except (TypeError, ValueError):
        return default


def get_clob_read_client():
    """Public CLOB SDK client for market/price reads. Falls back to HTTP if SDK is unavailable."""
    global _clob_read_client
    if _clob_read_client is None:
        try:
            from py_clob_client_v2.client import ClobClient
            _clob_read_client = ClobClient(CLOB_URL, chain_id=CHAIN_ID)
        except Exception as e:
            log.debug(f"CLOB SDK read client unavailable: {e}")
            _clob_read_client = False
    return _clob_read_client or None


def fetch_trades(limit=1000):
    """Fetch recent trades from Data API"""
    url = f"{DATA_API}/trades?limit={limit}&apiKey={DATA_API_KEY}&_t={int(time.time()*1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_wallet_activity(addr: str, limit: int = 100):
    url = f"{DATA_API}/activity?user={addr}&limit={limit}&apiKey={DATA_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def check_new_wallet(addr: str, activities=None):
    """Check if wallet is new (<5 activities, <48h old)"""
    if activities is None:
        try:
            activities = fetch_wallet_activity(addr)
        except Exception:
            return False, 0
    n = len(activities)
    if n == 0:
        return True, 0
    if n >= 100:
        return False, 0
    timestamps = []
    for activity in activities:
        try:
            timestamp = float(activity.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            timestamps.append(timestamp)
    if not timestamps:
        return False, 0
    earliest = min(timestamps)
    age_hours = (time.time() - earliest) / 3600
    return n < 5 and age_hours < 48, age_hours


def test_heavy_profile(activities: list, current_trade: dict,
                       current_notional: float) -> dict:
    """Detect a large BUY following 3-50 prior, much smaller BUY orders."""
    result = {
        "qualifies": False,
        "order_count": 0,
        "median_usdc": 0,
        "multiple": 0,
    }
    if current_notional <= TEST_HEAVY_MIN_USDC or len(activities) >= 100:
        return result

    current_hash = current_trade.get("transactionHash", "")
    try:
        current_ts = int(float(current_trade.get("timestamp") or 0))
    except (TypeError, ValueError):
        current_ts = 0

    grouped = {}
    for activity in activities:
        if str(activity.get("type", "TRADE")).upper() != "TRADE":
            continue
        if str(activity.get("side", "")).upper() != "BUY":
            continue
        tx_hash = activity.get("transactionHash", "")
        if not tx_hash or tx_hash == current_hash:
            continue
        try:
            activity_ts = int(float(activity.get("timestamp") or 0))
        except (TypeError, ValueError):
            continue
        if current_ts and activity_ts > current_ts:
            continue

        key = (
            tx_hash,
            activity.get("conditionId", ""),
            activity.get("asset", ""),
            "BUY",
        )
        amount = _to_float(activity.get("usdcSize"))
        if amount <= 0:
            amount = (_to_float(activity.get("price")) *
                      _to_float(activity.get("size")))
        if amount > 0:
            grouped[key] = grouped.get(key, 0) + amount

    amounts = list(grouped.values())
    result["order_count"] = len(amounts)
    if not (TEST_HEAVY_MIN_ORDERS <= len(amounts) <= TEST_HEAVY_MAX_ORDERS):
        return result

    median_usdc = statistics.median(amounts)
    if median_usdc <= 0:
        return result
    multiple = current_notional / median_usdc
    result.update({
        "qualifies": multiple >= TEST_HEAVY_MEDIAN_MULTIPLE,
        "median_usdc": median_usdc,
        "multiple": multiple,
    })
    return result


def _has_rapid_round_trip(activities: list, trade: dict) -> bool:
    """True when post-signal SELL volume closes at least 90% of matching BUYs."""
    try:
        signal_ts = int(float(trade.get("timestamp") or 0))
    except (TypeError, ValueError):
        return False
    condition_id = trade.get("conditionId", "")
    asset = str(trade.get("asset", ""))
    buy_size = 0.0
    sell_size = 0.0
    for activity in activities:
        if str(activity.get("type", "TRADE")).upper() != "TRADE":
            continue
        if condition_id and activity.get("conditionId", "") != condition_id:
            continue
        if asset and str(activity.get("asset", "")) != asset:
            continue
        try:
            activity_ts = int(float(activity.get("timestamp") or 0))
            size = float(activity.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if activity_ts < signal_ts or size <= 0:
            continue
        side = str(activity.get("side", "")).upper()
        if side == "BUY":
            buy_size += size
        elif side == "SELL":
            sell_size += size
    return buy_size > 0 and sell_size >= buy_size * 0.90


def _schedule_confirmation(trade: dict, notional: float):
    tx_hash = trade.get("transactionHash", "")
    if not tx_hash or tx_hash in _pending_trades:
        return
    _pending_trades[tx_hash] = {
        "trade": trade,
        "notional": notional,
        "ready_at": time.time() + SIGNAL_CONFIRM_DELAY_SECONDS,
    }


def _confirm_pending_trade(tx_hash: str, pending: dict, callback) -> bool:
    trade = pending["trade"]
    wallet = trade.get("proxyWallet", "")
    try:
        activities = fetch_wallet_activity(wallet)
    except Exception as e:
        pending["ready_at"] = time.time() + POLL_INTERVAL
        log.warning(f"confirmation retry: {wallet[:10]}... error={e}")
        return False

    if _has_rapid_round_trip(activities, trade):
        log.info(
            f"rapid round-trip filtered: wallet={wallet[:10]}... "
            f"tx={tx_hash[:14]}... delay={SIGNAL_CONFIRM_DELAY_SECONDS}s")
        _pending_trades.pop(tx_hash, None)
        _remember_trade(tx_hash)
        return True

    is_new, age_hours = check_new_wallet(wallet, activities)
    wallet_signal_tag = ""
    if not is_new:
        profile = test_heavy_profile(activities, trade, pending["notional"])
        if not profile["qualifies"]:
            _pending_trades.pop(tx_hash, None)
            _remember_trade(tx_hash)
            return True
        wallet_signal_tag = "小额测试重仓"

    direction = direction_label(trade.get("outcome", ""), "BUY")
    completed = callback(
        trade, wallet, pending["notional"], age_hours, direction,
        wallet_signal_tag)
    if completed is False:
        pending["ready_at"] = time.time() + POLL_INTERVAL
        return False
    _pending_trades.pop(tx_hash, None)
    _remember_trade(tx_hash)
    return True


def _process_due_confirmations(callback):
    now = time.time()
    for tx_hash, pending in list(_pending_trades.items()):
        if pending["ready_at"] > now:
            continue
        try:
            _confirm_pending_trade(tx_hash, pending, callback)
        except Exception as e:
            pending["ready_at"] = time.time() + POLL_INTERVAL
            log.error(
                f"confirmation retry: tx={tx_hash[:14]}... error={e}")


def has_active_position(addr: str, condition_id: str) -> bool:
    """Check if wallet still holds this position"""
    url = f"{DATA_API}/positions?user={addr}&apiKey={DATA_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            positions = json.loads(resp.read())
    except Exception:
        return False
    for p in positions:
        if p.get("condition_id") == condition_id and p.get("size", "0") != "0":
            return True
    return False


def resolve_clob_tokens(condition_id: str) -> dict:
    """Resolve condition_id → CLOB token IDs via CLOB /markets endpoint.
    Returns dict with keys 'YES'/'NO' mapping to token_id, or empty dict."""
    client = get_clob_read_client()
    if client:
        try:
            data = _to_dict(client.get_market(condition_id))
            result = {}
            for t in data.get("tokens", []):
                token = _to_dict(t)
                outcome = token.get("outcome", "")
                token_id = token.get("token_id") or token.get("tokenId")
                if outcome and token_id:
                    result[outcome.upper()] = token_id
            if result:
                return result
        except Exception as e:
            log.debug(f"CLOB SDK resolve tokens failed: {e}")

    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        result = {}
        for t in data.get("tokens", []):
            outcome = t.get("outcome", "")
            if outcome:
                result[outcome.upper()] = t["token_id"]
        return result
    except Exception:
        return {}


def fetch_fpmm_price(token_id: str):
    """Get FPMM price from CLOB /markets endpoint.
       If not found, try resolving via condition_id from Data API."""
    client = get_clob_read_client()
    if client:
        for method in ("get_midpoint", "get_last_trade_price"):
            try:
                price = _to_float(getattr(client, method)(token_id))
                if 0 < price < 1:
                    return price
            except Exception as e:
                log.debug(f"CLOB SDK {method} failed: {e}")

    url = f"https://clob.polymarket.com/markets"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            markets = json.loads(resp.read())
        for m in markets.get("data", []):
            for t in m.get("tokens", []):
                if t["token_id"] == token_id:
                    return float(t["price"])
    except Exception:
        pass
    return 0


def fetch_best_price(token_id: str, side: str) -> float:
    """Return execution price: a BUY crosses the best ask, a SELL crosses the best bid."""
    normalized_side = side.strip().upper()
    if normalized_side not in ("BUY", "SELL"):
        raise ValueError(f"unsupported side: {side}")
    # CLOB /price side names the resting order side, so a taker crosses it.
    book_side = "SELL" if normalized_side == "BUY" else "BUY"

    client = get_clob_read_client()
    if client:
        try:
            price = _to_float(client.get_price(token_id, book_side))
            if 0 < price <= 1:
                return price
        except Exception as e:
            log.debug(f"CLOB SDK best {normalized_side} price failed: {e}")

    url = (
        f"{CLOB_URL}/price?token_id={token_id}"
        f"&side={book_side}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            price = _to_float(json.loads(resp.read()))
        if 0 < price <= 1:
            return price
    except Exception as e:
        log.debug(f"CLOB HTTP best {normalized_side} price failed: {e}")
    return 0


def fetch_fpmm_by_condition(condition_id: str, outcome: str) -> float:
    """Get FPMM price via condition_id (more reliable than token ID lookup)."""
    client = get_clob_read_client()
    if client:
        try:
            data = _to_dict(client.get_market(condition_id))
            for t in data.get("tokens", []):
                token = _to_dict(t)
                if token.get("outcome", "").lower() == outcome.lower():
                    price = _to_float(token.get("price"))
                    if 0 < price < 1:
                        return price
                    token_id = token.get("token_id") or token.get("tokenId")
                    if token_id:
                        return fetch_fpmm_price(token_id)
        except Exception as e:
            log.debug(f"CLOB SDK condition price failed: {e}")

    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        for t in data.get("tokens", []):
            if t.get("outcome", "").lower() == outcome.lower():
                return float(t["price"])
    except Exception:
        pass
    return 0


def fetch_market_snapshot(condition_id: str) -> dict:
    client = get_clob_read_client()
    if client:
        try:
            data = _to_dict(client.get_market(condition_id))
            if data:
                data["_source"] = "clob_sdk"
                return data
        except Exception as e:
            log.debug(f"CLOB SDK market snapshot failed: {e}")

    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        data["_source"] = "clob_http"
        return data
    except Exception:
        return {}


def direction_label(outcome: str, side: str) -> str:
    if side.upper() != "BUY":
        return "sell"
    normalized = outcome.strip().upper()
    if normalized == "YES":
        return "bullish"
    if normalized == "NO":
        return "bearish"
    return "selected_outcome"


def _process_polled_trade(t: dict, callback) -> bool:
    """Process one trade; return True only when it is safe to deduplicate."""
    tx_hash = t.get("transactionHash", "")
    if not tx_hash:
        return True
    if _is_recent_trade(tx_hash):
        return True
    if tx_hash in _pending_trades:
        return True

    wallet = t.get("proxyWallet", "")
    if not wallet:
        _remember_trade(tx_hash)
        return True

    side = t.get("side", "").upper()
    if side == "SELL":
        completed = callback(t, wallet, 0, 0, "")
        if completed is not False:
            _remember_trade(tx_hash)
            return True
        return False

    if side != "BUY":
        _remember_trade(tx_hash)
        return True

    try:
        notional = (float(t.get("price", 0) or 0) *
                    float(t.get("size", 0) or 0))
    except (ValueError, TypeError):
        _remember_trade(tx_hash)
        return True
    if notional < MIN_TRADE_USDC:
        _remember_trade(tx_hash)
        return True

    try:
        activities = fetch_wallet_activity(wallet)
    except Exception as e:
        log.warning(f"wallet activity retry: {wallet[:10]}... error={e}")
        return False

    is_new, _ = check_new_wallet(wallet, activities)
    if not is_new:
        profile = test_heavy_profile(activities, t, notional)
        if not profile["qualifies"]:
            _remember_trade(tx_hash)
            return True
        log.info(
            f"test-heavy wallet: {wallet[:10]}... "
            f"orders={profile['order_count']} "
            f"median=${profile['median_usdc']:.2f} "
            f"current=${notional:.2f} "
            f"multiple={profile['multiple']:.1f}x")

    _schedule_confirmation(t, notional)
    return True


def poll_trades(callback):
    """
    Main polling loop. Calls callback(trade, wallet, notional, age_hours, direction)
    for qualifying BUY trades and ALL SELL trades (exit detection).
    """
    count = 0; last_hour_log = time.time()
    while True:
        try:
            _process_due_confirmations(callback)
            trades = fetch_trades()

            for t in trades:
                count += 1
                try:
                    _process_polled_trade(t, callback)
                except Exception as e:
                    tx_hash = t.get("transactionHash", "")
                    log.error(
                        f"trade processing retry: tx={tx_hash[:14]}... error={e}")

        except Exception as e:
            log.error(f"poll error: {e}")
        time.sleep(POLL_INTERVAL)
