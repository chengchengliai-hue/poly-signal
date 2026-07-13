import sqlite3
import json
import os
import threading
from functools import wraps
from config import SQLITE_PATH

os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
_db_lock = threading.RLock()


def _locked(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _db_lock:
            return func(*args, **kwargs)
    return wrapper


@_locked
def init():
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seen_events (
            tx_hash TEXT PRIMARY KEY,
            seen_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT NOT NULL,
            market_slug TEXT,
            market_question TEXT,
            token_id TEXT,
            outcome TEXT,
            action TEXT,
            direction TEXT,
            notional_usdc REAL,
            score INTEGER,
            source TEXT,
            tags TEXT DEFAULT '[]',
            condition_id TEXT,
            signal_tx_hash TEXT,
            event_slug TEXT,
            reference_price REAL,
            execution_price REAL,
            copy_decision TEXT,
            skip_reason TEXT,
            raw_signal TEXT,
            raw_market TEXT,
            raw_event TEXT,
            alerted_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            market_question TEXT,
            token_id TEXT,
            outcome TEXT,
            entry_price REAL,
            shares REAL,
            cost REAL,
            alert_score INTEGER,
            alert_source TEXT,
            status TEXT DEFAULT 'active',
            pnl REAL DEFAULT 0,
            condition_id TEXT,
            event_slug TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            event_slug TEXT PRIMARY KEY,
            event_id TEXT,
            event_title TEXT,
            event_type TEXT,
            market_count INTEGER DEFAULT 0,
            raw_event TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS market_topics (
            condition_id TEXT PRIMARY KEY,
            event_slug TEXT,
            event_title TEXT,
            market_question TEXT,
            policy_family TEXT,
            topic_key TEXT,
            series_key TEXT,
            proposition TEXT,
            raw_market TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS related_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            tx_hash TEXT UNIQUE,
            wallet TEXT,
            event_slug TEXT,
            condition_id TEXT,
            market_question TEXT,
            outcome TEXT,
            policy_family TEXT,
            topic_key TEXT,
            series_key TEXT,
            proposition TEXT,
            stance TEXT,
            notional_usdc REAL,
            signal_ts INTEGER,
            relation_type TEXT,
            related_market_count INTEGER DEFAULT 1,
            related_wallet_count INTEGER DEFAULT 1,
            direction_agreement REAL DEFAULT 0,
            related_notional_usdc REAL DEFAULT 0,
            relation_window_minutes INTEGER DEFAULT 30,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tracked (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT,
            amount REAL,
            score INTEGER,
            status TEXT DEFAULT 'tracking',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS runtime (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS copy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mode TEXT DEFAULT 'virtual',
            status TEXT DEFAULT 'open',
            wallet TEXT NOT NULL,
            market_slug TEXT,
            market_question TEXT,
            condition_id TEXT,
            token_id TEXT,
            outcome TEXT,
            direction TEXT,
            source TEXT,
            signal_tx_hash TEXT,
            signal_side TEXT,
            signal_price REAL,
            signal_size REAL,
            signal_notional_usdc REAL,
            wallet_age_hours REAL,
            score INTEGER,
            tags TEXT DEFAULT '[]',
            entry_price REAL,
            entry_shares REAL,
            entry_cost REAL,
            hours_to_end REAL,
            end_date TEXT,
            raw_signal TEXT,
            raw_market TEXT,
            event_slug TEXT,
            event_type TEXT,
            market_count INTEGER DEFAULT 0,
            outcome_count INTEGER DEFAULT 0,
            raw_event TEXT,
            exit_tx_hash TEXT,
            exit_reason TEXT,
            exit_price REAL,
            exit_size REAL,
            exit_value REAL,
            pnl REAL DEFAULT 0,
            roi_pct REAL DEFAULT 0,
            hold_minutes REAL DEFAULT 0,
            raw_exit TEXT,
            opened_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        );
    """)
    conn.commit()
    _ensure_alert_columns()
    _ensure_copy_trade_columns()
    _ensure_position_columns()
    _ensure_indexes()


@_locked
def _ensure_alert_columns():
    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    migrations = {
        "condition_id": "ALTER TABLE alerts ADD COLUMN condition_id TEXT",
        "signal_tx_hash": "ALTER TABLE alerts ADD COLUMN signal_tx_hash TEXT",
        "event_slug": "ALTER TABLE alerts ADD COLUMN event_slug TEXT",
        "reference_price": "ALTER TABLE alerts ADD COLUMN reference_price REAL",
        "execution_price": "ALTER TABLE alerts ADD COLUMN execution_price REAL",
        "copy_decision": "ALTER TABLE alerts ADD COLUMN copy_decision TEXT",
        "skip_reason": "ALTER TABLE alerts ADD COLUMN skip_reason TEXT",
        "raw_signal": "ALTER TABLE alerts ADD COLUMN raw_signal TEXT",
        "raw_market": "ALTER TABLE alerts ADD COLUMN raw_market TEXT",
        "raw_event": "ALTER TABLE alerts ADD COLUMN raw_event TEXT",
    }
    for col, sql in migrations.items():
        if col not in cols:
            conn.execute(sql)
    conn.commit()


@_locked
def _ensure_indexes():
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_alerts_alerted_at
            ON alerts(alerted_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_signal_tx_hash
            ON alerts(signal_tx_hash);
        CREATE INDEX IF NOT EXISTS idx_positions_active_wallet
            ON positions(status, wallet);
        CREATE INDEX IF NOT EXISTS idx_copy_trades_status
            ON copy_trades(status);
        CREATE INDEX IF NOT EXISTS idx_copy_trades_position_status
            ON copy_trades(position_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_copy_trades_signal_tx_hash
            ON copy_trades(signal_tx_hash)
            WHERE signal_tx_hash IS NOT NULL AND signal_tx_hash != '';
        CREATE INDEX IF NOT EXISTS idx_market_topics_topic
            ON market_topics(topic_key);
        CREATE INDEX IF NOT EXISTS idx_market_topics_series
            ON market_topics(series_key);
        CREATE INDEX IF NOT EXISTS idx_related_signals_topic_time
            ON related_signals(topic_key, signal_ts);
        CREATE INDEX IF NOT EXISTS idx_related_signals_series_time
            ON related_signals(series_key, signal_ts);
    """)
    conn.commit()


@_locked
def _ensure_position_columns():
    cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
    migrations = {
        "pnl": "ALTER TABLE positions ADD COLUMN pnl REAL DEFAULT 0",
        "condition_id": "ALTER TABLE positions ADD COLUMN condition_id TEXT",
        "event_slug": "ALTER TABLE positions ADD COLUMN event_slug TEXT",
    }
    for col, sql in migrations.items():
        if col not in cols:
            conn.execute(sql)
    conn.execute(
        """
        UPDATE positions
        SET condition_id=COALESCE(
                condition_id,
                (SELECT condition_id FROM copy_trades
                 WHERE copy_trades.position_id=positions.id LIMIT 1)),
            event_slug=COALESCE(
                event_slug,
                (SELECT event_slug FROM copy_trades
                 WHERE copy_trades.position_id=positions.id LIMIT 1))
        WHERE condition_id IS NULL OR event_slug IS NULL
        """)
    conn.commit()


@_locked
def _ensure_copy_trade_columns():
    cols = {row[1] for row in conn.execute("PRAGMA table_info(copy_trades)").fetchall()}
    migrations = {
        "raw_market": "ALTER TABLE copy_trades ADD COLUMN raw_market TEXT",
        "roi_pct": "ALTER TABLE copy_trades ADD COLUMN roi_pct REAL DEFAULT 0",
        "hold_minutes": "ALTER TABLE copy_trades ADD COLUMN hold_minutes REAL DEFAULT 0",
        "event_slug": "ALTER TABLE copy_trades ADD COLUMN event_slug TEXT",
        "event_type": "ALTER TABLE copy_trades ADD COLUMN event_type TEXT",
        "market_count": "ALTER TABLE copy_trades ADD COLUMN market_count INTEGER DEFAULT 0",
        "outcome_count": "ALTER TABLE copy_trades ADD COLUMN outcome_count INTEGER DEFAULT 0",
        "raw_event": "ALTER TABLE copy_trades ADD COLUMN raw_event TEXT",
    }
    for col, sql in migrations.items():
        if col not in cols:
            conn.execute(sql)
    conn.commit()


@_locked
def is_seen(tx_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_events WHERE tx_hash = ?", (tx_hash,)).fetchone()
    return row is not None


@_locked
def mark_seen(tx_hash: str):
    conn.execute("INSERT OR IGNORE INTO seen_events (tx_hash) VALUES (?)", (tx_hash,))
    conn.commit()


@_locked
def save_alert(wallet, market_slug, market_question, token_id, outcome, action,
               direction, notional, score, source, tags, condition_id="",
               signal_tx_hash="", event_slug="", reference_price=0,
               execution_price=0, copy_decision="", skip_reason="",
               raw_signal=None, raw_market=None, raw_event=None):
    cur = conn.execute(
        """
        INSERT INTO alerts (
            wallet, market_slug, market_question, token_id, outcome, action,
            direction, notional_usdc, score, source, tags, condition_id,
            signal_tx_hash, event_slug, reference_price, execution_price,
            copy_decision, skip_reason, raw_signal, raw_market, raw_event
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            wallet, market_slug, market_question, token_id, outcome, action,
            direction, notional, score, source, json.dumps(tags), condition_id,
            signal_tx_hash, event_slug, reference_price, execution_price,
            copy_decision, skip_reason,
            json.dumps(raw_signal or {}, ensure_ascii=False),
            json.dumps(raw_market or {}, ensure_ascii=False),
            json.dumps(raw_event or {}, ensure_ascii=False),
        ))
    conn.commit()
    return cur.lastrowid


@_locked
def update_alert_copy_decision(alert_id: int, decision: str,
                               skip_reason: str = "",
                               execution_price: float = 0):
    conn.execute(
        """
        UPDATE alerts
        SET copy_decision=?, skip_reason=?, execution_price=?
        WHERE id=?
        """,
        (decision, skip_reason, execution_price, alert_id))
    conn.commit()


@_locked
def save_position(wallet, market_slug, market_question, token_id, outcome,
                  entry_price, shares, cost, alert_score, alert_source,
                  condition_id="", event_slug=""):
    cur = conn.execute(
        """
        INSERT INTO positions (
            wallet, market_slug, market_question, token_id, outcome, entry_price,
            shares, cost, alert_score, alert_source, condition_id, event_slug
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (wallet, market_slug, market_question, token_id, outcome, entry_price,
         shares, cost, alert_score, alert_source, condition_id, event_slug))
    conn.commit()
    return cur.lastrowid


@_locked
def save_or_add_position(wallet, market_slug, market_question, condition_id,
                         token_id, outcome, entry_price, shares, cost,
                         alert_score, alert_source, event_slug=""):
    row = conn.execute(
        """
        SELECT id, shares, cost
        FROM positions
        WHERE LOWER(wallet)=LOWER(?) AND condition_id=? AND token_id=?
          AND status='active'
        ORDER BY id
        LIMIT 1
        """,
        (wallet, condition_id, token_id)).fetchone()
    if not row:
        return save_position(
            wallet, market_slug, market_question, token_id, outcome,
            entry_price, shares, cost, alert_score, alert_source,
            condition_id, event_slug)

    pos_id, old_shares, old_cost = row
    new_shares = float(old_shares or 0) + float(shares or 0)
    new_cost = float(old_cost or 0) + float(cost or 0)
    average_price = new_cost / new_shares if new_shares > 0 else entry_price
    conn.execute(
        """
        UPDATE positions
        SET shares=?, cost=?, entry_price=?, alert_score=MAX(alert_score, ?),
            market_slug=?, market_question=?, outcome=?, event_slug=?
        WHERE id=?
        """,
        (new_shares, new_cost, average_price, alert_score, market_slug,
         market_question, outcome, event_slug, pos_id))
    conn.commit()
    return pos_id


@_locked
def save_event(event_slug, event_id, event_title, event_type, market_count,
               raw_event):
    if not event_slug:
        return
    conn.execute(
        """
        INSERT INTO events (
            event_slug, event_id, event_title, event_type, market_count, raw_event
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT(event_slug) DO UPDATE SET
            event_id=excluded.event_id,
            event_title=excluded.event_title,
            event_type=excluded.event_type,
            market_count=excluded.market_count,
            raw_event=excluded.raw_event,
            last_seen_at=datetime('now')
        """,
        (event_slug, event_id, event_title, event_type, market_count,
         json.dumps(raw_event or {}, ensure_ascii=False)))
    conn.commit()


@_locked
def save_market_topics(rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO market_topics (
            condition_id, event_slug, event_title, market_question,
            policy_family, topic_key, series_key, proposition, raw_market
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(condition_id) DO UPDATE SET
            event_slug=excluded.event_slug,
            event_title=excluded.event_title,
            market_question=excluded.market_question,
            policy_family=excluded.policy_family,
            topic_key=excluded.topic_key,
            series_key=excluded.series_key,
            proposition=excluded.proposition,
            raw_market=excluded.raw_market,
            last_seen_at=datetime('now')
        """,
        [(
            row.get("condition_id", ""), row.get("event_slug", ""),
            row.get("event_title", ""), row.get("market_question", ""),
            row.get("policy_family", ""), row.get("topic_key", ""),
            row.get("series_key", ""), row.get("proposition", ""),
            json.dumps(row.get("raw_market") or {}, ensure_ascii=False),
        ) for row in rows if row.get("condition_id")])
    conn.commit()


@_locked
def get_recent_related_signals(topic_key: str, series_key: str,
                               start_ts: int, end_ts: int) -> list:
    if not topic_key and not series_key:
        return []
    rows = conn.execute(
        """
        SELECT tx_hash, wallet, condition_id, topic_key, series_key, stance,
               notional_usdc, signal_ts
        FROM related_signals
        WHERE signal_ts BETWEEN ? AND ?
          AND ((? != '' AND topic_key=?) OR (? != '' AND series_key=?))
        """,
        (start_ts, end_ts, topic_key, topic_key, series_key, series_key),
    ).fetchall()
    keys = (
        "tx_hash", "wallet", "condition_id", "topic_key", "series_key",
        "stance", "notional_usdc", "signal_ts",
    )
    return [dict(zip(keys, row)) for row in rows]


@_locked
def save_related_signal(alert_id: int, signal: dict, summary: dict,
                        window_minutes: int):
    conn.execute(
        """
        INSERT OR IGNORE INTO related_signals (
            alert_id, tx_hash, wallet, event_slug, condition_id,
            market_question, outcome, policy_family, topic_key, series_key,
            proposition, stance, notional_usdc, signal_ts, relation_type,
            related_market_count, related_wallet_count, direction_agreement,
            related_notional_usdc, relation_window_minutes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            alert_id, signal.get("tx_hash", ""), signal.get("wallet", ""),
            signal.get("event_slug", ""), signal.get("condition_id", ""),
            signal.get("market_question", ""), signal.get("outcome", ""),
            signal.get("policy_family", ""), signal.get("topic_key", ""),
            signal.get("series_key", ""), signal.get("proposition", ""),
            signal.get("stance", ""), signal.get("notional_usdc", 0),
            signal.get("signal_ts", 0), summary.get("relation_type", "none"),
            summary.get("related_market_count", 1),
            summary.get("related_wallet_count", 1),
            summary.get("direction_agreement", 0),
            summary.get("related_notional_usdc", 0), window_minutes,
        ))
    conn.commit()


@_locked
def save_copy_trade_entry(position_id, mode, wallet, market_slug, market_question,
                          condition_id, token_id, outcome, direction, source,
                          signal_tx_hash, signal_side, signal_price, signal_size,
                          signal_notional, wallet_age_hours, score, tags,
                          entry_price, entry_shares, entry_cost, hours_to_end,
                          end_date, raw_signal, raw_market=None, event_slug="",
                          event_type="", market_count=0, outcome_count=0,
                          raw_event=None):
    cur = conn.execute(
        """
        INSERT INTO copy_trades (
            position_id, mode, wallet, market_slug, market_question, condition_id,
            token_id, outcome, direction, source, signal_tx_hash, signal_side,
            signal_price, signal_size, signal_notional_usdc, wallet_age_hours,
            score, tags, entry_price, entry_shares, entry_cost, hours_to_end,
            end_date, raw_signal, raw_market, event_slug, event_type,
            market_count, outcome_count, raw_event
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            position_id, mode, wallet, market_slug, market_question, condition_id,
            token_id, outcome, direction, source, signal_tx_hash, signal_side,
            signal_price, signal_size, signal_notional, wallet_age_hours,
            score, json.dumps(tags), entry_price, entry_shares, entry_cost,
            hours_to_end, end_date, json.dumps(raw_signal, ensure_ascii=False),
            json.dumps(raw_market or {}, ensure_ascii=False), event_slug,
            event_type, market_count, outcome_count,
            json.dumps(raw_event or {}, ensure_ascii=False)
        ))
    conn.commit()
    return cur.lastrowid


@_locked
def mark_copy_trade_closed(position_id, exit_reason, pnl=0, exit_tx_hash="",
                           exit_price=0, exit_size=0, exit_value=0, raw_exit=None):
    conn.execute(
        """
        UPDATE copy_trades
        SET status='closed', exit_tx_hash=?, exit_reason=?, exit_price=?,
            exit_size=?, exit_value=?, pnl=?,
            roi_pct=CASE WHEN entry_cost > 0 THEN (? / entry_cost) * 100 ELSE 0 END,
            hold_minutes=(julianday('now') - julianday(opened_at)) * 24 * 60,
            raw_exit=?, closed_at=datetime('now')
        WHERE position_id=? AND status='open'
        """,
        (
            exit_tx_hash, exit_reason, exit_price, exit_size, exit_value, pnl, pnl,
            json.dumps(raw_exit or {}, ensure_ascii=False), position_id
        ))
    conn.commit()


@_locked
def close_copy_trades_for_position(position_id, exit_reason, exit_price,
                                   exit_tx_hash="", raw_exit=None):
    conn.execute(
        """
        UPDATE copy_trades
        SET status='closed', exit_tx_hash=?, exit_reason=?, exit_price=?,
            exit_size=entry_shares,
            exit_value=entry_shares * ?,
            pnl=(entry_shares * ?) - entry_cost,
            roi_pct=CASE
                WHEN entry_cost > 0
                THEN (((entry_shares * ?) - entry_cost) / entry_cost) * 100
                ELSE 0
            END,
            hold_minutes=(julianday('now') - julianday(opened_at)) * 24 * 60,
            raw_exit=?, closed_at=datetime('now')
        WHERE position_id=? AND status='open'
        """,
        (
            exit_tx_hash, exit_reason, exit_price, exit_price, exit_price,
            exit_price, json.dumps(raw_exit or {}, ensure_ascii=False),
            position_id,
        ))
    conn.commit()


@_locked
def get_active_positions():
    rows = conn.execute(
        """
        SELECT id, wallet, market_slug, market_question, token_id, outcome,
               entry_price, shares, cost, alert_score, alert_source, status, pnl,
               COALESCE((
                   SELECT ct.tags
                   FROM copy_trades ct
                   WHERE ct.position_id=positions.id
                   ORDER BY ct.score DESC, ct.id DESC
                   LIMIT 1
               ), '[]') AS score_tags
        FROM positions
        WHERE status='active'
        """
    ).fetchall()
    return rows


@_locked
def get_active_position(pos_id: int):
    return conn.execute(
        """
        SELECT id, wallet, market_slug, market_question, token_id, outcome,
               entry_price, shares, cost, alert_score, alert_source, status, pnl
        FROM positions
        WHERE id=? AND status='active'
        """,
        (pos_id,)).fetchone()


@_locked
def get_active_positions_by_wallet(wallet: str):
    return conn.execute(
        """
        SELECT id, wallet, market_slug, market_question, token_id, outcome,
               entry_price, shares, cost, alert_score, alert_source, status, pnl
        FROM positions
        WHERE LOWER(wallet)=LOWER(?) AND status='active'
        ORDER BY id
        """,
        (wallet,)).fetchall()


@_locked
def mark_position_closed(pos_id, pnl=0):
    conn.execute("UPDATE positions SET status='closed', pnl=? WHERE id=?", (pnl, pos_id))
    conn.commit()


@_locked
def get_recent_alerts(limit=5):
    rows = conn.execute(
        """
        SELECT wallet, market_slug, market_question, token_id, outcome, action,
               notional_usdc, score, tags, alerted_at, direction
        FROM alerts
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,)).fetchall()
    return rows


@_locked
def get_positions_for_copy_stats():
    return conn.execute(
        "SELECT id, wallet, market_slug, market_question, outcome, entry_price, shares, cost, alert_score, alert_source, status, pnl FROM positions ORDER BY id DESC"
    ).fetchall()


@_locked
def get_copy_trade_stats():
    return conn.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN status='open' THEN 1 ELSE 0 END), 0) AS active,
               COALESCE(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END), 0) AS closed,
               COALESCE(SUM(entry_cost), 0) AS total_cost,
               COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END), 0) AS total_pnl,
               COALESCE(SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END), 0) AS wins
        FROM copy_trades
        """
    ).fetchone()


@_locked
def add_tracked(wallet, market_slug, market_question, outcome, amount, score):
    conn.execute(
        "INSERT OR IGNORE INTO tracked (wallet, market_slug, market_question, outcome, amount, score) VALUES (?,?,?,?,?,?)",
        (wallet, market_slug, market_question, outcome, amount, score))
    conn.commit()


@_locked
def remove_tracked(track_id):
    conn.execute("UPDATE tracked SET status='removed' WHERE id=?", (track_id,))


@_locked
def get_tracked_by_wallet(wallet, market_slug, outcome):
    return conn.execute(
        "SELECT * FROM tracked WHERE wallet=? AND market_slug=? AND outcome=? AND status='tracking'",
        (wallet, market_slug, outcome)).fetchone()


@_locked
def get_runtime(key: str) -> str:
    row = conn.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
    return row[0] if row else ""


@_locked
def set_runtime(key: str, value: str):
    conn.execute("INSERT OR REPLACE INTO runtime (key, value) VALUES (?,?)", (key, value))
    conn.commit()


@_locked
def is_wallet_relevant(wallet: str) -> bool:
    """True if wallet has active positions or is tracked — used as SELL pre-filter."""
    row = conn.execute(
        "SELECT 1 FROM positions WHERE LOWER(wallet)=LOWER(?) AND status='active' LIMIT 1",
        (wallet,)).fetchone()
    if row:
        return True
    row = conn.execute(
        "SELECT 1 FROM tracked WHERE LOWER(wallet)=LOWER(?) AND status='tracking' LIMIT 1",
        (wallet,)).fetchone()
    return row is not None


@_locked
def get_position_by_wallet_market(wallet: str, market_slug: str, outcome: str):
    return conn.execute(
        """
        SELECT id, wallet, market_slug, market_question, token_id, outcome,
               entry_price, shares, cost, alert_score, alert_source, status, pnl
        FROM positions
        WHERE LOWER(wallet)=LOWER(?) AND market_slug=?
          AND UPPER(outcome)=UPPER(?) AND status='active'
        """,
        (wallet, market_slug, outcome)).fetchone()


@_locked
def get_open_copy_trade_market(position_id: int):
    return conn.execute(
        """
        SELECT condition_id, token_id
        FROM copy_trades
        WHERE position_id=? AND status='open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (position_id,)).fetchone()


@_locked
def gc():
    conn.execute("DELETE FROM seen_events WHERE seen_at < datetime('now', '-120 minutes')")
    conn.commit()
