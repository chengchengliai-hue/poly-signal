import json
import time
import logging
import threading
import urllib.request
from config import BOT_TOKEN, CHAT_ID

log = logging.getLogger("bot")

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

_track_contexts = {}
_track_contexts_lock = threading.Lock()
_last_update_id = 0


def set_commands():
    cmds = [{"command": c, "description": d} for c, d in [
        ("smart_money", "聪明钱预警"),
        ("positions", "跟踪仓位"),
        ("copy_stats", "跟单统计"),
        ("stop", "停止跟单"),
    ]]
    payload = json.dumps({"commands": cmds}).encode()
    req = urllib.request.Request(f"{TG_URL}/setMyCommands", data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def send_message(text: str, keyboard: str = "", chat_id: str = None):
    if not BOT_TOKEN:
        return
    cid = int(chat_id or CHAT_ID)
    payload = {"chat_id": cid, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = json.loads(keyboard)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{TG_URL}/sendMessage", data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error(f"send error: {e}")


def format_alert(wallet: str, market: str, outcome: str, action: str,
                 notional: float, score: int, tags: list, market_slug: str,
                 direction: str, detected_at: str = "") -> tuple:
    """Returns (text, keyboard) for alert Telegram message."""
    severity = "🔴" if score >= 90 else "🟡"
    normalized_outcome = outcome.strip().upper()
    if normalized_outcome == "YES":
        direction_cn = "看多 YES"
    elif normalized_outcome == "NO":
        direction_cn = "看空（买入 NO）"
    else:
        direction_cn = f"看多 {outcome}"
    wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) > 14 else wallet
    market_url = f"https://polymarket.com/{market_slug}" if market_slug else ""
    wallet_url = f"https://polygonscan.com/address/{wallet}"
    profile_url = f"https://polymarket.com/profile/{wallet}"

    # Store context for track callback
    track_id = _gen_id()
    with _track_contexts_lock:
        _track_contexts[track_id] = {
            "wallet": wallet, "market_slug": market_slug,
            "market": market, "outcome": outcome, "amount": notional, "score": score,
        }

    text = (
        f"{severity} 聪明钱预警 — {score}分\n\n"
        f"钱包: {wallet_short}\n"
        f"市场: {market[:80]}\n"
        f"金额: ${notional:,.0f}  |  {direction_cn}\n"
        f"标签: {' · '.join(tags)}\n"
        f"{'⏰ ' + detected_at if detected_at else ''}"
    )
    kb = json.dumps({"inline_keyboard": [
        [{"text": "🔍 钱包", "url": wallet_url},
         {"text": "📊 持仓", "url": profile_url}],
        [{"text": "👁 跟踪", "callback_data": f"t|{track_id}"}],
    ]})
    return text, kb


def format_position(wallet: str, market: str, outcome: str, shares: float,
                    cost: float, score: int, pos_id: int,
                    score_tags=None) -> tuple:
    """Returns (text, keyboard) for position display."""
    wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) > 14 else wallet
    normalized_outcome = outcome.strip().upper()
    if normalized_outcome == "YES":
        direction = "看多 YES"
    elif normalized_outcome == "NO":
        direction = "看空（买入 NO）"
    else:
        direction = f"看多 {outcome}"
    score_tags = score_tags or []
    score_detail = "基础分50"
    if score_tags:
        score_detail += " + " + " + ".join(str(tag) for tag in score_tags)
    else:
        score_detail += "（历史评分明细未保存）"
    text = (
        f"📋 跟单仓位\n\n"
        f"钱包: {wallet_short}\n"
        f"市场: {market[:80]}\n"
        f"持仓: {shares:.1f} 股  |  {direction}\n"
        f"成本: ${cost:.0f}  |  原始评分: {score}\n"
        f"评分构成: {score_detail}"
    )
    profile_url = f"https://polymarket.com/profile/{wallet}"
    wallet_url = f"https://polygonscan.com/address/{wallet}"
    kb = json.dumps({"inline_keyboard": [
        [{"text": "🔍 钱包", "url": wallet_url},
         {"text": "📊 持仓", "url": profile_url}],
        [{"text": "❌ 取消跟踪", "callback_data": f"u|{pos_id}"}],
    ]})
    return text, kb


def format_exit(wallet: str, market: str, outcome: str, shares: float,
                sold: float, exit_type: str) -> str:
    wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) > 14 else wallet
    emoji = "🔴" if exit_type == "full" else "🟡"
    label = "已清仓" if exit_type == "full" else "大幅减仓"
    return (
        f"{emoji} {label}\n\n"
        f"钱包: {wallet_short}\n"
        f"市场: {market[:60]}\n"
        f"原持仓: {shares:.1f} 股 ({outcome})\n"
        f"已卖出: {sold:.1f} 股"
    )


def poll_bot(callback):
    """Poll Telegram updates, call callback(update) for each command/callback."""
    global _last_update_id
    set_commands()
    log.info("bot polling started")
    while True:
        try:
            url = f"{TG_URL}/getUpdates?offset={_last_update_id + 1}&timeout=10"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            if not data.get("ok"):
                continue
            for u in data.get("result", []):
                _last_update_id = max(_last_update_id, u["update_id"])
                callback(u)
        except Exception as e:
            log.error(f"poll error: {e}")
        time.sleep(2)


def answer_callback(callback_id: str, text: str = ""):
    payload = json.dumps({"callback_query_id": callback_id, "text": text}).encode()
    req = urllib.request.Request(f"{TG_URL}/answerCallbackQuery", data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def consume_track_context(short_id: str) -> dict:
    with _track_contexts_lock:
        return _track_contexts.pop(short_id, None)


def format_tracked_sell(wallet: str, market: str, outcome: str,
                         shares: float, price: float) -> str:
    """Notification when a tracked wallet sells."""
    wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) > 14 else wallet
    return (
        f"👁 跟踪钱包卖出\n\n"
        f"钱包: {wallet_short}\n"
        f"市场: {market[:60]}\n"
        f"卖出: {shares:.1f} 股 ({outcome}) @ ${price:.4f}"
    )


def _gen_id():
    import random
    return hex(random.getrandbits(32))[2:]
