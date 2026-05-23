"""回测分析：从positions.db读取数据，生成收益报告和CSV"""
import sqlite3
import csv
from datetime import datetime
from collections import defaultdict


def load_all_positions():
    conn = sqlite3.connect("data/positions.db")
    rows = conn.execute("""
        SELECT id, market_question, direction, entry_price, entry_time,
               bet_usd, shares, current_price, pnl_usd, status, headline, source
        FROM positions ORDER BY entry_time
    """).fetchall()
    conn.close()
    return rows


def export_csv(filename: str = "data/backtest_report.csv"):
    rows = load_all_positions()
    with open(filename, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "market", "direction", "entry_price", "entry_time",
                     "bet_usd", "shares", "current_price", "pnl_usd", "status", "headline", "source"])
        for r in rows:
            w.writerow(r)
    return filename


def generate_report() -> dict:
    rows = load_all_positions()
    if not rows:
        return {"error": "no data"}

    total = len(rows)
    closed = [r for r in rows if r[9] == "closed"]
    open_pos = [r for r in rows if r[9] == "open"]

    wins = [r for r in rows if (r[8] or 0) > 0]
    losses = [r for r in rows if (r[8] or 0) < 0]

    total_pnl = sum(r[8] or 0 for r in rows)
    total_invested = sum(r[5] for r in rows)

    # By direction
    by_dir = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for r in rows:
        d = r[2]
        by_dir[d]["count"] += 1
        by_dir[d]["pnl"] += r[8] or 0

    # By source
    by_source = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for r in rows:
        s = r[11] or "unknown"
        by_source[s]["count"] += 1
        by_source[s]["pnl"] += r[8] or 0

    # Best/worst
    sorted_by_pnl = sorted(rows, key=lambda r: r[8] or 0, reverse=True)

    return {
        "total_trades": total,
        "open": len(open_pos),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 1) if total > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "total_invested": round(total_invested, 2),
        "roi_pct": round(total_pnl / total_invested * 100, 2) if total_invested > 0 else 0,
        "avg_pnl_per_trade": round(total_pnl / total, 2) if total > 0 else 0,
        "by_direction": dict(by_dir),
        "by_source": dict(by_source),
        "best_trade": {
            "question": sorted_by_pnl[0][1] if sorted_by_pnl else "",
            "pnl": round(sorted_by_pnl[0][8] or 0, 2) if sorted_by_pnl else 0,
        },
        "worst_trade": {
            "question": sorted_by_pnl[-1][1] if sorted_by_pnl else "",
            "pnl": round(sorted_by_pnl[-1][8] or 0, 2) if sorted_by_pnl else 0,
        },
    }


def format_report(report: dict) -> str:
    if "error" in report:
        return "暂无数据"

    return f"""📈 回测报告

交易总数: {report['total_trades']}  (持仓 {report['open']} / 已结 {report['closed']})
胜率: {report['win_rate']}%  ({report['wins']}W / {report['losses']}L)
总盈亏: {'+$' if report['total_pnl'] >= 0 else '-$'}{abs(report['total_pnl']):.2f}
总投入: ${report['total_invested']}
ROI: {report['roi_pct']}%
平均每笔: ${report['avg_pnl_per_trade']}

最佳: {report['best_trade']['question'][:50]} (${report['best_trade']['pnl']})
最差: {report['worst_trade']['question'][:50]} (${report['worst_trade']['pnl']})

按方向:
  BUY_YES: {report['by_direction'].get('BUY_YES', {}).get('count', 0)}笔, ${report['by_direction'].get('BUY_YES', {}).get('pnl', 0):.2f}
  BUY_NO:  {report['by_direction'].get('BUY_NO', {}).get('count', 0)}笔, ${report['by_direction'].get('BUY_NO', {}).get('pnl', 0):.2f}

按来源:
""" + "\n".join(
    f"  {s}: {d['count']}笔, ${d['pnl']:.2f}"
    for s, d in sorted(report['by_source'].items(), key=lambda x: x[1]['pnl'], reverse=True)[:5]
)
