import json
import logging
import time
import urllib.request
from db import (get_active_positions, get_open_copy_trade_market,
                mark_position_closed, close_copy_trades_for_position)
from bot import send_message
from trader import sell_position
from config import STOP_LOSS_PCT
from poller import fetch_best_price, fetch_market_snapshot

log = logging.getLogger("settle")


def _get_condition_id_for_slug(market_slug: str) -> str:
    """Get condition_id from Gamma API for a given slug."""
    slug = market_slug.replace("market/", "")
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data:
            for m in data[0].get("markets", []):
                cid = m.get("conditionId", "")
                if cid:
                    return cid
    except Exception:
        pass
    return ""


def _get_position_market_ids(pos_id: int, market_slug: str, token_id: str):
    copy_market = get_open_copy_trade_market(pos_id)
    if copy_market and copy_market[0]:
        return copy_market[0], copy_market[1] or token_id
    return _get_condition_id_for_slug(market_slug), token_id


def _get_settlement(condition_id: str, token_id: str, outcome: str):
    """Return (final_price, market_snapshot), or (None, snapshot) if unresolved."""
    if not condition_id:
        return None, {}

    market = fetch_market_snapshot(condition_id)
    if not market:
        return None, {}

    selected = None
    for token in market.get("tokens", []):
        current_token_id = token.get("token_id") or token.get("tokenId")
        if token_id and str(current_token_id) == str(token_id):
            selected = token
            break
    if selected is None:
        for token in market.get("tokens", []):
            if token.get("outcome", "").lower() == outcome.lower():
                selected = token
                break
    if selected is None:
        return None, market

    is_closed = market.get("closed") is True
    has_explicit_winner = any(
        token.get("winner") is True for token in market.get("tokens", []))
    if not is_closed and not has_explicit_winner:
        return None, market

    if selected.get("winner") is True:
        return 1.0, market
    if has_explicit_winner:
        return 0.0, market
    try:
        price = float(selected.get("price"))
    except (TypeError, ValueError):
        return None, market
    if price >= 0.99:
        return 1.0, market
    if price <= 0.01:
        return 0.0, market
    return None, market


def check_stop_loss():
    """Check active positions for stop-loss triggers."""
    positions = get_active_positions()
    for pos in positions:
        pos_id, wallet, market_slug, market_q, token_id, outcome, entry_price, shares, cost, score, source, status = pos[:12]
        if not market_slug or not outcome:
            continue
        if float(shares or 0) <= 0:
            continue

        condition_id, _ = _get_position_market_ids(
            pos_id, market_slug, token_id)
        if not token_id:
            continue

        current_bid = fetch_best_price(token_id, "SELL")
        if current_bid <= 0 or current_bid >= 1:
            continue

        entry = float(entry_price or 0)
        if entry <= 0:
            continue

        drop_pct = (entry - current_bid) / entry * 100
        if drop_pct >= STOP_LOSS_PCT:
            log.info(f"Stop-loss: {market_q[:40]} {outcome} entry={entry:.4f} bid={current_bid:.4f} drop={drop_pct:.1f}%")
            if token_id:
                result = sell_position(token_id, float(shares), market_q or "")
                if result and result.get("success"):
                    exit_price = float(result.get(
                        "fillPrice", current_bid) or 0)
                    exit_size = float(result.get(
                        "filledShares", shares) or 0)
                    exit_value = float(result.get(
                        "exitValue", exit_price * exit_size) or 0)
                    pnl = exit_value - float(cost or 0)
                    send_message(
                        f"🛑 止损平仓\n\n"
                        f"市场: {market_q[:60]}\n"
                        f"持仓: {exit_size:.1f} 股 ({outcome})\n"
                        f"入场: {entry:.4f}  →  成交: {exit_price:.4f}\n"
                        f"跌幅: {drop_pct:.1f}%  |  盈亏: ${pnl:+.2f}")
                    close_copy_trades_for_position(
                        pos_id, "stop_loss", exit_price, "",
                        {"condition_id": condition_id,
                         "trigger_price": current_bid,
                         "trigger_price_source": "clob_best_bid",
                         "execution": result})
                    mark_position_closed(pos_id, pnl)


def check_settlements():
    """Scan active positions and check if markets have resolved."""
    positions = get_active_positions()
    if not positions:
        return

    for pos in positions:
        pos_id, wallet, market_slug, market_q, token_id, outcome, entry_price, shares, cost, score, source, status = pos[:12]
        if not market_slug:
            continue

        try:
            condition_id, resolved_token_id = _get_position_market_ids(
                pos_id, market_slug, token_id)
            winner_price, market = _get_settlement(
                condition_id, resolved_token_id, outcome)
            if winner_price is None:
                continue

            share_count = float(shares or 0)
            cost_val = float(cost or 0)
            if winner_price >= 0.99:
                pnl = share_count - cost_val
                exit_value = share_count
                exit_reason = "settlement_win"
                log.info(f"Settled WIN: {market_q[:40]} +${pnl:.2f}")
                send_message(
                    f"✅ 已结算\n\n市场: {market_q[:60]}\n"
                    f"持仓: {share_count:.1f} 股 ({outcome})\n"
                    f"成本: ${cost_val:.2f}  |  价值: ${share_count:.2f}\n"
                    f"盈亏: ${pnl:+,.2f}")
            else:
                pnl = -cost_val
                exit_value = 0
                exit_reason = "settlement_loss"
                log.info(f"Settled LOSS: {market_q[:40]} -${cost_val:.2f}")
                send_message(
                    f"❌ 已结算\n\n市场: {market_q[:60]}\n"
                    f"持仓: {share_count:.1f} 股 ({outcome})\n"
                    f"成本: ${cost_val:.2f}  |  盈亏: ${pnl:+,.2f}")

            close_copy_trades_for_position(
                pos_id, exit_reason, winner_price, "", market)
            mark_position_closed(pos_id, pnl)
        except Exception as e:
            log.debug(f"settle check error: {e}")


def settlement_loop():
    while True:
        time.sleep(600)  # every 10 minutes
        try:
            check_stop_loss()
            check_settlements()
        except Exception as e:
            log.error(f"settlement error: {e}")
