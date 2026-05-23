"""Telegram bot button handler: 聪明钱 / 跟单 / 日报"""
import asyncio
import json
import sqlite3
import time
from datetime import datetime

import httpx

from src.config import TG_BOT_TOKEN, TG_CHAT_ID

# Track last update_id to avoid duplicate handling
last_update_id = 0

# Persistent bottom menu
MENU_BUTTONS = {
    "keyboard": [
        [{"text": "💡 聪明钱"}, {"text": "📊 跟单信号"}, {"text": "📈 日报"}],
    ],
    "resize_keyboard": True,
}


async def send_menu():
    """Send persistent bottom menu."""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": "📋 菜单已就绪",
                "reply_markup": MENU_BUTTONS,
            },
        )


async def handle_callback(callback_id: str, text: str):
    """Handle button click."""
    if text == "💡 聪明钱":
        msg = get_smart_money_summary()
    elif text == "📊 跟单信号":
        msg = get_copy_trade_summary()
    elif text == "📈 日报":
        msg = get_daily_report()
    else:
        msg = "未知命令"

    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": MENU_BUTTONS,
            },
        )
        # Answer callback to remove loading spinner
        await client.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
        )


def get_smart_money_summary() -> str:
    """Read smart money alerts: whale USDT transfers + Polymarket trades."""
    try:
        conn = sqlite3.connect("/opt/listener/polygon_smart_money_watch.db")

        # 巨鲸转账
        whale_rows = conn.execute(
            "SELECT severity, score, address, round(total_usd), substr(tags,1,40), substr(alerted_at,12,5) "
            "FROM whale_alerts ORDER BY id DESC LIMIT 5"
        ).fetchall()
        whale_total = conn.execute("SELECT COUNT(*) FROM whale_alerts").fetchone()[0]
        whale_high = conn.execute("SELECT COUNT(*) FROM whale_alerts WHERE severity='high'").fetchone()[0]

        # Polymarket 下注
        poly_rows = conn.execute(
            "SELECT severity, score, substr(root_address,1,14), round(estimated_usdc), outcome, action, substr(category,1,20), substr(alerted_at,12,5) "
            "FROM informed_event_alerts ORDER BY id DESC LIMIT 5"
        ).fetchall()
        poly_total = conn.execute("SELECT COUNT(*) FROM informed_event_alerts").fetchone()[0]
        conn.close()

        lines = [f"<b>💡 聪明钱</b>\n巨鲸转账 {whale_total} 次 | 高危 {whale_high} | 下注 {poly_total} 次\n"]

        if poly_rows:
            lines.append("\n<b>🎯 最近下注：</b>")
            for r in poly_rows:
                sev = "🔴" if r[0] == "high" else "🟡" if r[0] == "normal" else "⚪"
                outcome = r[4] or "?"
                action = r[5] or ""
                direction = f"{action} {outcome}"
                lines.append(f"{sev} {direction} ${r[3]:.0f} | {r[2]} | {r[6][:15]} | {r[7]}")

        if whale_rows:
            lines.append("\n<b>💰 最近链上转账：</b>")
            for r in whale_rows:
                sev = "🔴" if r[0] == "high" else "🟡" if r[0] == "normal" else "⚪"
                addr_short = r[2][:10] + "..." + r[2][-6:]
                lines.append(f"{sev} {addr_short} ${r[3]:.0f} {r[4][:25]} {r[5]}")

        return "\n".join(lines)
    except Exception as e:
        return f"聪明钱数据读取失败: {e}"


def get_copy_trade_summary() -> str:
    """Read copy trading positions with full detail."""
    try:
        conn = sqlite3.connect("/opt/poly-signal/data/positions.db")
        rows = conn.execute(
            "SELECT id, direction, round(entry_price,2), COALESCE(round(current_price,2), 0) as cur_price, round(pnl_usd,2), "
            "bet_usd, round(shares,2), market_question, headline, substr(entry_time,12,5) "
            "FROM positions ORDER BY id DESC LIMIT 12"
        ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        open_pos = conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
        total_pnl = conn.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM positions WHERE status='open'").fetchone()[0]
        total_invested = conn.execute("SELECT COALESCE(SUM(bet_usd),0) FROM positions").fetchone()[0]
        conn.close()

        pnl_sign = "+$" if total_pnl >= 0 else "-$"
        lines = [f"<b>📊 跟单信号</b> | {total}笔 | 持仓{open_pos} | 盈亏{pnl_sign}{abs(total_pnl):.2f} | 投入${total_invested:.0f}\n"]
        for r in rows:
            dir_emoji = "📈" if r[0] == "BUY_YES" else "📉"
            pnl_str = f"+${r[4]:.2f}" if (r[4] or 0) >= 0 else f"-${abs(r[4] or 0):.2f}"
            cur_price = f"→ ${r[3]:.2f}" if r[3] else "待更新"
            lines.append(
                f"{dir_emoji} #{r[0]} 入场${r[2]:.2f} {cur_price} | {pnl_str}\n"
                f"    {r[7][:50]}\n"
                f"    来源: {r[8][:40] if r[8] else ''} | {r[9]}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"跟单数据读取失败: {e}"


def get_daily_report() -> str:
    """Generate daily P&L report."""
    try:
        from src.analytics import generate_report, format_report
        report = generate_report()
        return f"<b>📈 日报</b>\n\n{format_report(report)}"
    except Exception:
        try:
            conn = sqlite3.connect("/opt/poly-signal/data/positions.db")
            total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            pnl = conn.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM positions WHERE status='open'").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE date(entry_time) = date('now')"
            ).fetchone()[0]
            conn.close()
            return f"<b>📈 日报</b>\n\n今日新增: {today} 笔\n总持仓盈亏: ${pnl:.2f}\n累计交易: {total} 笔"
        except Exception as e:
            return f"日报生成失败: {e}"


async def poll_updates():
    """Poll Telegram for button clicks."""
    global last_update_id
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates",
                    params={"offset": last_update_id + 1, "timeout": 60},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(2)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    last_update_id = max(last_update_id, update["update_id"])

                    # Handle button callback
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        await handle_callback(cb["id"], cb.get("data", ""))

                    # Handle text message (menu buttons send text)
                    if "message" in update and "text" in update["message"]:
                        text = update["message"]["text"]
                        if text in ("💡 聪明钱", "📊 跟单信号", "📈 日报"):
                            await handle_callback(
                                str(update["update_id"]), text
                            )

        except Exception as e:
            print(f"Poll error: {e}")
            await asyncio.sleep(5)


async def main():
    print("[bot] Starting Telegram button handler...")
    await send_menu()
    await poll_updates()


if __name__ == "__main__":
    asyncio.run(main())
