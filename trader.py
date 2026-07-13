import logging
import time
from config import PRIVATE_KEY, PROXY, CLOB_URL, CHAIN_ID, COPY_TRADE_AMOUNT, COPY_TRADE_BOOST, VIRTUAL_COPY_TRADING
from poller import fetch_best_price, fetch_fpmm_price

log = logging.getLogger("trader")

_client = None


def get_client():
    global _client
    if _client is None:
        from py_clob_client_v2.client import ClobClient
        temp = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID)
        creds = temp.create_or_derive_api_key()
        _client = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID,
                              creds=creds, signature_type=1, funder=PROXY)
        log.info("CLOB client ready, live trading enabled")
    return _client


def copy_trade_buy(token_id: str, condition_id: str, outcome: str, token_type: str,
                    market_slug: str, market_question: str, notional: float, score: int,
                    source: str, fpmm: float = 0) -> dict:
    """
    Copy trade BUY: place FOK market buy via CLOB SDK.
    Accepts pre-computed fpmm to avoid redundant lookups.
    """
    if VIRTUAL_COPY_TRADING and fpmm <= 0:
        fpmm = fetch_best_price(token_id, "BUY")
    elif fpmm <= 0:
        fpmm = fetch_fpmm_price(token_id)
    if fpmm <= 0 or fpmm >= 1:
        log.info(f"FPMM {fpmm:.4f} invalid, skip")
        return None
    if fpmm > 0.95:
        log.info(f"FPMM {fpmm:.4f} > 0.95, risk/reward too poor, skip")
        return None

    amount = notional
    if VIRTUAL_COPY_TRADING:
        shares = amount / fpmm
        log.info(f"virtual BUY: {market_question[:40]}  {outcome}  fpmm={fpmm:.4f}  ${amount} ({shares:.2f} shares)")
        return {
            "success": True,
            "virtual": True,
            "orderID": f"virtual-buy-{int(time.time() * 1000)}",
            "takingAmount": shares,
            "makingAmount": amount,
            "price": fpmm,
            "fillPrice": fpmm,
            "priceSource": "clob_best_ask",
        }

    log.info(f"copy trade BUY: {market_question[:40]}  {outcome}  fpmm={fpmm:.4f}  ${amount}")

    client = get_client()
    try:
        from py_clob_client_v2.clob_types import MarketOrderArgsV2
        args = MarketOrderArgsV2(token_id=token_id, amount=amount, side='BUY')
        resp = client.create_and_post_market_order(args, order_type='FOK')
        log.info(f"order result: {resp}")
        return resp
    except Exception as e:
        log.error(f"order error: {e}")
        return None


def sell_position(token_id: str, shares: float, market_question: str = "") -> dict:
    """
    Sell tracked position: FOK market sell via CLOB SDK.
    """
    if shares <= 0:
        return None
    if VIRTUAL_COPY_TRADING:
        current_price = fetch_best_price(token_id, "SELL")
        if current_price <= 0 or current_price > 1:
            log.warning(
                f"virtual SELL price unavailable: {market_question[:40]} "
                f"token={token_id[:16]}...")
            return None
        exit_value = current_price * float(shares)
        result = {
            "success": True,
            "virtual": True,
            "orderID": f"virtual-sell-{int(time.time() * 1000)}",
            "tokenId": token_id,
            "filledShares": float(shares),
            "fillPrice": current_price,
            "exitValue": exit_value,
            "priceSource": "clob_best_bid",
        }
        log.info(
            f"virtual SELL: {market_question[:40]} {shares:.1f} shares "
            f"market={current_price:.4f} value=${exit_value:.2f}")
        return result

    log.info(f"copy trade SELL: {market_question[:40]}  {shares:.1f} shares")
    client = get_client()
    try:
        from py_clob_client_v2.clob_types import MarketOrderArgsV2
        args = MarketOrderArgsV2(token_id=token_id, amount=shares, side='SELL')
        resp = client.create_and_post_market_order(args, order_type='FOK')
        log.info(f"sell result: {resp}")
        return resp
    except Exception as e:
        log.error(f"sell error: {e}")
        return None
