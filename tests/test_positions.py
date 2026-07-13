import sqlite3
import unittest

import db


class AggregatedPositionTests(unittest.TestCase):
    def setUp(self):
        self.original_conn = db.conn
        db.conn = sqlite3.connect(":memory:")
        db.init()

    def tearDown(self):
        db.conn.close()
        db.conn = self.original_conn

    def test_multiple_entries_share_one_position_and_close_individually(self):
        first_id = db.save_or_add_position(
            "0xwallet", "market/test", "Test", "condition", "token", "Over",
            0.4, 10, 4, 80, "smart_money", "event-test")
        second_id = db.save_or_add_position(
            "0xwallet", "market/test", "Test", "condition", "token", "Over",
            0.5, 10, 5, 90, "smart_money", "event-test")

        self.assertEqual(first_id, second_id)
        position = db.get_active_position(first_id)
        self.assertEqual(position[7], 20)
        self.assertEqual(position[8], 9)
        self.assertEqual(position[6], 0.45)

        common = dict(
            position_id=first_id, mode="virtual", wallet="0xwallet",
            market_slug="market/test", market_question="Test",
            condition_id="condition", token_id="token", outcome="Over",
            direction="selected_outcome", source="smart_money",
            signal_side="BUY", signal_price=0.4, signal_size=100,
            signal_notional=40, wallet_age_hours=1, score=80, tags=[],
            hours_to_end=10, end_date="", raw_signal={}, raw_market={},
            event_slug="event-test", event_type="binary_named",
            market_count=1, outcome_count=2, raw_event={},
        )
        db.save_copy_trade_entry(
            signal_tx_hash="tx1", entry_price=0.4, entry_shares=10,
            entry_cost=4, **common)
        db.save_copy_trade_entry(
            signal_tx_hash="tx2", entry_price=0.5, entry_shares=10,
            entry_cost=5, **common)

        db.close_copy_trades_for_position(
            first_id, "tracked_sell", 0.6, "sell-tx", {"test": True})
        rows = db.conn.execute(
            "SELECT status, exit_value, pnl FROM copy_trades ORDER BY id"
        ).fetchall()
        self.assertEqual(rows, [
            ("closed", 6.0, 2.0),
            ("closed", 6.0, 1.0),
        ])

    def test_wallet_sell_lookup_returns_all_active_positions(self):
        for token in ("token-a", "token-b"):
            db.save_or_add_position(
                "0xwallet", f"market/{token}", token, f"condition-{token}",
                token, "Yes", 0.5, 10, 5, 80, "smart_money", "event")

        self.assertEqual(len(db.get_active_positions_by_wallet("0xWALLET")), 2)


if __name__ == "__main__":
    unittest.main()
