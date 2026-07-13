import json
import calendar
from datetime import datetime, timezone
import logging
import re
import time
import urllib.request
from zoneinfo import ZoneInfo
from poller import (resolve_clob_tokens, fetch_best_price,
                    fetch_fpmm_by_condition, fetch_market_snapshot,
                    direction_label)
from trader import copy_trade_buy, sell_position
from bot import send_message, format_alert, format_exit, format_tracked_sell
from db import (is_seen, mark_seen, save_alert, save_or_add_position, save_event,
                get_active_positions, mark_position_closed, get_tracked_by_wallet,
                is_wallet_relevant, get_active_position,
                get_active_positions_by_wallet, save_copy_trade_entry,
                close_copy_trades_for_position, update_alert_copy_decision,
                save_market_topics, get_recent_related_signals,
                save_related_signal)
from config import (COPY_TRADE_AMOUNT, COPY_TRADE_BOOST, MIN_SHARES,
                    VIRTUAL_COPY_TRADING, RELATED_WINDOW_MINUTES,
                    RELATED_MARKET_BONUS, RELATED_MULTI_WALLET_BONUS)
from relations import build_market_relation, summarize_related_signals

log = logging.getLogger("handler")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _beijing_time(utc_text: str = "") -> str:
    if utc_text:
        try:
            value = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S")
            value = value.replace(tzinfo=timezone.utc)
        except ValueError:
            return utc_text
    else:
        value = datetime.now(timezone.utc)
    return value.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S 北京时间")


def _fetch_end_date(condition_id: str) -> str:
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("end_date_iso", "")
    except Exception:
        return ""


def _hours_to_end(end_iso: str) -> float:
    if not end_iso:
        return -1
    for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            t = time.strptime(end_iso, fmt)
            return (calendar.timegm(t) - time.time()) / 3600
        except ValueError:
            continue
    return -1


def _fetch_event_snapshot(event_slug: str) -> dict:
    if not event_slug:
        return {}
    url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-signal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data[0] if data else {}
    except Exception:
        return {}


def _event_type(event_snapshot: dict, market_snapshot: dict) -> str:
    market_count = len(event_snapshot.get("markets", []))
    outcomes = {
        str(token.get("outcome", "")).strip().upper()
        for token in market_snapshot.get("tokens", [])
        if token.get("outcome")
    }
    if market_count > 1:
        return "multi_market_event"
    if outcomes == {"YES", "NO"}:
        return "binary_yes_no"
    if len(outcomes) == 2:
        return "binary_named"
    return "multi_outcome"


def _flatten_tags(tags) -> str:
    if not tags:
        return ""
    parts = []
    for tag in tags:
        if isinstance(tag, dict):
            parts.extend(str(v) for v in tag.values() if v)
        else:
            parts.append(str(tag))
    return " ".join(parts)


def _is_political_market(title: str, market_slug: str,
                         market_snapshot: dict, event_snapshot: dict) -> bool:
    classification_text = " ".join([
        event_snapshot.get("category", "") or "",
        _flatten_tags(event_snapshot.get("tags")),
        _flatten_tags(market_snapshot.get("tags")),
    ]).lower()
    if re.search(r"\b(?:politics?|elections?|geopolitics?)\b",
                 classification_text):
        return True

    text = " ".join([
        title or "",
        market_slug or "",
        market_snapshot.get("question", "") or "",
        market_snapshot.get("description", "") or "",
        event_snapshot.get("title", "") or "",
        event_snapshot.get("slug", "") or "",
    ]).lower()
    political_patterns = (
        r"\bpolitic(?:s|al)?\b", r"\belections?\b",
        r"\bpresident(?:ial)?\b", r"\bprime minister\b",
        r"\bsenate\b", r"\bcongress\b", r"\bgovernors?\b",
        r"\bmayors?\b", r"\bdemocrats?\b", r"\brepublicans?\b",
        r"\bparliament\b", r"\bsupreme court\b", r"\bscotus\b",
        r"\breferendums?\b", r"\bballots?\b", r"\bnominees?\b",
        r"\bmidterms?\b", r"\bcabinet\b", r"\bgovernment\b",
        r"\bwhite house\b", r"\bhouse of representatives\b",
        r"\bpresidential primar(?:y|ies)\b", r"\belection campaign\b",
        r"\btariffs?\b", r"\bsanctions?\b", r"\bceasefires?\b",
        r"\btrump\b", r"\bbiden\b", r"\bvance\b",
        r"\bdesantis\b", r"\bnewsom\b",
    )
    return any(re.search(pattern, text) for pattern in political_patterns)


def _apply_market_score_adjustments(score: int, tags: list, title: str,
                                    market_slug: str, hours_to_end: float,
                                    market_snapshot: dict,
                                    event_snapshot: dict) -> int:
    if 0 <= hours_to_end < 6:
        score += 20
        tags.append("6h内临期(+20)")
    elif 0 <= hours_to_end < 24:
        score += 15
        tags.append("24h内临期(+15)")
    elif 0 <= hours_to_end < 72:
        score += 8
        tags.append("72h内临期(+8)")

    if _is_political_market(title, market_slug, market_snapshot, event_snapshot):
        score += 15
        tags.append("政治/政策事件(+15)")

    return min(score, 100)


def _apply_signal_score_adjustments(score: int, tags: list,
                                    age_hours: float,
                                    wallet_signal_tag: str,
                                    reference_price: float) -> int:
    if wallet_signal_tag == "小额测试重仓":
        score += 10
        tags.append("小额测试重仓(+10)")
    elif 0 <= age_hours < 6:
        score += 10
        tags.append("6h内新钱包(+10)")

    if 0 < reference_price < 0.1:
        score += 10
        tags.append(f"低概率赔率(fpmm={reference_price:.3f})(+10)")
    return score


def _save_event_market_topics(event_slug: str, event_snapshot: dict):
    event_title = (
        event_snapshot.get("title", "")
        or event_snapshot.get("question", "")
    )
    rows = []
    for market in event_snapshot.get("markets", []):
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        question = market.get("question", "")
        relation = build_market_relation(
            event_slug, event_title, question)
        rows.append({
            "condition_id": condition_id,
            "event_slug": event_slug,
            "event_title": event_title,
            "market_question": question,
            "raw_market": market,
            **relation,
        })
    save_market_topics(rows)


def _related_signal_context(t: dict, wallet: str, notional: float,
                            event_slug: str, event_snapshot: dict,
                            condition_id: str, title: str,
                            outcome: str, market_snapshot: dict) -> tuple:
    event_title = (
        event_snapshot.get("title", "")
        or event_snapshot.get("question", "")
    )
    relation = build_market_relation(
        event_slug, event_title, title, outcome)
    save_market_topics([{
        "condition_id": condition_id,
        "event_slug": event_slug,
        "event_title": event_title,
        "market_question": title,
        "raw_market": market_snapshot,
        **relation,
    }])
    try:
        signal_ts = int(float(t.get("timestamp") or time.time()))
    except (TypeError, ValueError):
        signal_ts = int(time.time())
    if signal_ts > 10_000_000_000:
        signal_ts //= 1000

    signal = {
        **relation,
        "tx_hash": t.get("transactionHash", ""),
        "wallet": wallet,
        "event_slug": event_slug,
        "condition_id": condition_id,
        "market_question": title,
        "outcome": outcome,
        "notional_usdc": notional,
        "signal_ts": signal_ts,
    }
    window_seconds = RELATED_WINDOW_MINUTES * 60
    previous = get_recent_related_signals(
        relation.get("topic_key", ""), relation.get("series_key", ""),
        signal_ts - window_seconds, signal_ts + window_seconds)
    return signal, summarize_related_signals(signal, previous)


def handle_trade(t, wallet, notional, age_hours, direction,
                 wallet_signal_tag=""):
    tx_hash = t["transactionHash"]
    side = t.get("side", "").upper()

    # ── SELL: exit detection (checked BEFORE mark_seen for relevance) ──
    if side == "SELL":
        if not is_wallet_relevant(wallet):
            return True
        if is_seen(tx_hash):
            return True

        outcome = t.get("outcome", "")
        slug = t.get("slug", "")
        market_slug = f"market/{slug}" if slug else ""
        title = t.get("title", "")

        # Any SELL from a copied wallet liquidates all positions derived from it.
        positions = get_active_positions_by_wallet(wallet)
        all_closed = True
        for pos in positions:
            clob_token_id = pos[4]
            shares_held = float(pos[7]) if pos[7] else 0
            if clob_token_id and shares_held > 0:
                log.info(f"Exit: {wallet[:10]}... selling {shares_held:.1f} shares of {title[:40]}")
                result = sell_position(clob_token_id, shares_held, pos[3] or title)
                if result and result.get("success"):
                    sell_price = float(result.get("fillPrice",
                                                  t.get("price", 0)) or 0)
                    exit_size = float(result.get("filledShares",
                                                 shares_held) or 0)
                    sell_value = float(result.get(
                        "exitValue", sell_price * exit_size) or 0)
                    cost = float(pos[8] or 0)
                    pnl = sell_value - cost
                    send_message(format_exit(
                        wallet, pos[3] or title, pos[5] or outcome,
                        shares_held, exit_size, "full"))
                    close_copy_trades_for_position(
                        pos[0], "tracked_sell", sell_price, tx_hash,
                        {"trigger": t, "execution": result})
                    mark_position_closed(pos[0], pnl)
                else:
                    all_closed = False

        # Check tracked: notify if a tracked wallet sells (even without position)
        tracked = get_tracked_by_wallet(wallet, market_slug, outcome)
        if tracked:
            sell_price = float(t.get("price", 0) or 0)
            sell_size = float(t.get("size", 0) or 0)
            send_message(format_tracked_sell(wallet, title, outcome, sell_size, sell_price))
        if not all_closed:
            return False
        mark_seen(tx_hash)
        return True

    # ── BUY only below ──
    if side != "BUY":
        return True

    if is_seen(tx_hash):
        return True

    token_id = t.get("asset", "")
    condition_id = t.get("conditionId", "")
    outcome = t.get("outcome", "")
    slug = t.get("slug", "")
    market_slug = f"market/{slug}" if slug else ""
    title = t.get("title", "")

    clob_tokens = resolve_clob_tokens(condition_id)
    clob_token_id = clob_tokens.get(outcome.upper(), token_id)

    # ── Scoring ──
    score = 50
    tags = []
    fpmm = fetch_fpmm_by_condition(condition_id, outcome)
    score = _apply_signal_score_adjustments(
        score, tags, age_hours, wallet_signal_tag, fpmm)

    market_snapshot = fetch_market_snapshot(condition_id)
    event_slug = t.get("eventSlug", "")
    event_snapshot = _fetch_event_snapshot(event_slug)
    event_type = _event_type(event_snapshot, market_snapshot)
    market_count = len(event_snapshot.get("markets", []))
    outcome_count = len(market_snapshot.get("tokens", []))
    save_event(
        event_slug,
        str(event_snapshot.get("id", "")),
        event_snapshot.get("title", "") or event_snapshot.get("question", ""),
        event_type,
        market_count,
        event_snapshot,
    )
    _save_event_market_topics(event_slug, event_snapshot)
    end_date = market_snapshot.get("end_date_iso", "") or _fetch_end_date(condition_id)
    hours_to_end = _hours_to_end(end_date) if end_date else -1
    score = _apply_market_score_adjustments(
        score, tags, title, market_slug, hours_to_end,
        market_snapshot, event_snapshot)

    relation_signal, relation_summary = _related_signal_context(
        t, wallet, notional, event_slug, event_snapshot,
        condition_id, title, outcome, market_snapshot)
    is_policy_relation = relation_signal.get("is_policy", False)
    if is_policy_relation and relation_summary.get("is_related"):
        markets = relation_summary["related_market_count"]
        wallets = relation_summary["related_wallet_count"]
        agreement = relation_summary["direction_agreement"] * 100
        bonus = (
            RELATED_MULTI_WALLET_BONUS
            if relation_summary.get("is_strong")
            else RELATED_MARKET_BONUS
        )
        score = min(score + bonus, 100)
        tags.append(
            f"关联市场同向({markets}市/{wallets}钱包/{agreement:.0f}%)(+{bonus})")
        log.info(
            f"related signal: topic={relation_signal.get('topic_key') or relation_signal.get('series_key')} "
            f"markets={markets} wallets={wallets} agreement={agreement:.0f}%")
    trade_amount = COPY_TRADE_AMOUNT
    if end_date and 0 <= hours_to_end < 24:
        trade_amount = COPY_TRADE_BOOST
        tags.append("临期加码")

    execution_price = (
        fetch_best_price(clob_token_id, "BUY")
        if VIRTUAL_COPY_TRADING else fpmm
    )
    if execution_price <= 0 or execution_price >= 1:
        copy_decision = "skipped"
        skip_reason = "invalid_execution_price"
    elif execution_price > 0.95:
        copy_decision = "skipped"
        skip_reason = "price_above_0.95"
    else:
        copy_decision = "pending"
        skip_reason = ""

    detected_at = _beijing_time()

    alert_id = save_alert(
        wallet, market_slug, title, token_id, outcome, "BUY", direction,
        notional, score, "smart_money", tags, condition_id=condition_id,
        signal_tx_hash=tx_hash, event_slug=event_slug,
        reference_price=fpmm, execution_price=execution_price,
        copy_decision=copy_decision, skip_reason=skip_reason,
        raw_signal=t, raw_market=market_snapshot, raw_event=event_snapshot)
    save_related_signal(
        alert_id, relation_signal, relation_summary,
        RELATED_WINDOW_MINUTES)
    text, kb = format_alert(wallet, title, outcome, "BUY", notional, score, tags,
                            market_slug, direction, detected_at)
    send_message(text, kb)

    # ── Copy trade ──
    if execution_price <= 0 or execution_price >= 1:
        log.info(f"copy skip: execution_price={execution_price:.4f}")
        mark_seen(tx_hash)
        return True
    if execution_price > 0.95:
        log.info(f"copy skip: execution_price={execution_price:.4f} > 0.95")
        mark_seen(tx_hash)
        return True

    shares = trade_amount / execution_price
    if shares < MIN_SHARES:
        trade_amount = execution_price * MIN_SHARES * 1.05
        shares = trade_amount / execution_price
        log.info(f"bumped to ${trade_amount:.2f} for min {int(MIN_SHARES)} shares")

    log.info(f"copy BUY: {title[:40]} {outcome} price={execution_price:.4f} ${trade_amount:.2f} ({shares:.0f} shares)")
    result = copy_trade_buy(clob_token_id, condition_id, outcome, "",
                            market_slug, title, trade_amount, score,
                            "smart_money", execution_price)
    if result and result.get("success"):
        filled_shares = float(result.get("takingAmount", 0))
        cost = float(result.get("makingAmount", 0))
        entry_price = float(result.get("fillPrice", execution_price) or 0)
        position_id = save_or_add_position(
            wallet, market_slug, title, condition_id, clob_token_id, outcome,
            entry_price, filled_shares, cost, score, "smart_money", event_slug)
        save_copy_trade_entry(
            position_id=position_id,
            mode="virtual" if (VIRTUAL_COPY_TRADING or result.get("virtual")) else "live",
            wallet=wallet,
            market_slug=market_slug,
            market_question=title,
            condition_id=condition_id,
            token_id=clob_token_id,
            outcome=outcome,
            direction=direction,
            source="smart_money",
            signal_tx_hash=tx_hash,
            signal_side=side,
            signal_price=float(t.get("price", 0) or 0),
            signal_size=float(t.get("size", 0) or 0),
            signal_notional=notional,
            wallet_age_hours=age_hours,
            score=score,
            tags=tags,
            entry_price=entry_price,
            entry_shares=filled_shares,
            entry_cost=cost,
            hours_to_end=hours_to_end,
            end_date=end_date,
            raw_signal=t,
            raw_market=market_snapshot,
            event_slug=event_slug,
            event_type=event_type,
            market_count=market_count,
            outcome_count=outcome_count,
            raw_event=event_snapshot,
        )
        update_alert_copy_decision(
            alert_id, "copied", "", entry_price)
    else:
        update_alert_copy_decision(
            alert_id, "failed", "execution_failed", execution_price)
    mark_seen(tx_hash)
    return True


def handle_bot_update(update: dict):
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data", "")
        if data.startswith("t|"):
            from bot import consume_track_context, answer_callback
            ctx = consume_track_context(data[2:])
            answer_callback(cb["id"])
            if ctx:
                from db import add_tracked
                add_tracked(ctx["wallet"], ctx["market_slug"], ctx["market"],
                            ctx["outcome"], ctx["amount"], ctx["score"])
                send_message("✅ 已开始跟踪", chat_id=str(cb["message"]["chat"]["id"]))
            return
        if data.startswith("u|"):
            from bot import answer_callback
            pos_id = int(data[2:])
            pos = get_active_position(pos_id)
            if not pos:
                answer_callback(cb["id"], "仓位已关闭")
                return
            result = sell_position(pos[4], float(pos[7] or 0), pos[3] or "")
            if result and result.get("success"):
                exit_price = float(result.get("fillPrice", 0) or 0)
                exit_size = float(result.get("filledShares", pos[7]) or 0)
                exit_value = float(result.get("exitValue",
                                              exit_price * exit_size) or 0)
                pnl = exit_value - float(pos[8] or 0)
                close_copy_trades_for_position(
                    pos_id, "manual_cancel", exit_price, "", result)
                mark_position_closed(pos_id, pnl)
                answer_callback(cb["id"], "已按当前盘口平仓")
            else:
                answer_callback(cb["id"], "盘口不足，未能平仓")
            return

    msg = update.get("message")
    if not msg or "text" not in msg:
        return
    text = msg["text"].strip()
    cid = msg["chat"]["id"]

    if text in ("/smart_money", "/sm"):
        alerts = get_recent_alerts(5)
        if not alerts:
            send_message("暂无预警", chat_id=str(cid))
            return
        for a in alerts:
            tags = json.loads(a[8]) if a[8] else []
            txt, kb = format_alert(a[0], a[2] or "", a[4] or "", a[5] or "",
                                    a[6] or 0, a[7] or 0, tags, a[1] or "",
                                    a[10] or direction_label(a[4] or "", a[5] or ""),
                                    _beijing_time(a[9]) if a[9] else "")
            send_message(txt, kb, chat_id=str(cid))
    elif text in ("/positions", "/pos"):
        positions = get_active_positions()
        if not positions:
            send_message("暂无跟单仓位", chat_id=str(cid))
            return
        from bot import format_position
        for p in positions:
            try:
                score_tags = json.loads(p[13]) if len(p) > 13 and p[13] else []
            except (TypeError, ValueError, json.JSONDecodeError):
                score_tags = []
            txt, kb = format_position(p[1], p[3] or "", p[5] or "",
                                      float(p[7] or 0), float(p[8] or 0),
                                      p[9] or 0, p[0], score_tags)
            send_message(txt, kb, chat_id=str(cid))
    elif text in ("/copy_stats", "/stats"):
        from db import get_copy_trade_stats
        stats = get_copy_trade_stats()
        total, active, closed, total_cost, total_pnl, wins = stats
        if not total:
            send_message("暂无跟单数据", chat_id=str(cid))
            return
        txt = (
            f"📈 跟单统计\n\n"
            f"总跟单: {total} 笔\n"
            f"进行中: {active}  |  已平仓: {closed}\n"
            f"总成本: ${total_cost:,.2f}  |  已实现盈亏: ${total_pnl:+,.2f}\n"
            f"胜率: {wins/max(closed,1)*100:.0f}% ({wins}/{closed})"
        )
        send_message(txt, chat_id=str(cid))
    elif text in ("/stop",):
        positions = get_active_positions()
        sold = 0
        failed = 0
        for p in positions:
            pos_id = p[0]
            token_id = p[4]
            shares = float(p[7] or 0)
            market_q = p[3] or ""
            if token_id and shares > 0:
                result = sell_position(token_id, shares, market_q)
                if result and result.get("success"):
                    exit_price = float(result.get("fillPrice", 0) or 0)
                    exit_size = float(result.get("filledShares", shares) or 0)
                    exit_value = float(result.get(
                        "exitValue", exit_price * exit_size) or 0)
                    pnl = exit_value - float(p[8] or 0)
                    close_copy_trades_for_position(
                        pos_id, "manual_stop", exit_price, "", result)
                    mark_position_closed(pos_id, pnl)
                    sold += 1
                else:
                    failed += 1
            else:
                failed += 1
        msg_text = f"🛑 已清仓 {sold} 个仓位"
        if failed:
            msg_text += f"，{failed} 个卖出失败"
        send_message(msg_text, chat_id=str(cid))
    elif text == "/start":
        send_message(
            "🔘 Poly Signal\n\n"
            "/smart_money — 聪明钱预警\n"
            "/positions — 跟单仓位\n"
            "/copy_stats — 跟单统计\n"
            "/stop — 卖出全部跟单并停止",
            chat_id=str(cid))


from db import get_recent_alerts
