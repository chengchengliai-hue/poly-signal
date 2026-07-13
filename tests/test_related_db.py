import sqlite3
import unittest

import db


class RelatedSignalDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.original_conn = db.conn
        db.conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init()

    def tearDown(self):
        db.conn.close()
        db.conn = self.original_conn

    def test_related_signal_round_trip(self):
        signal = {
            "tx_hash": "tx1", "wallet": "wallet1", "event_slug": "event1",
            "condition_id": "condition1", "market_question": "Question",
            "outcome": "Yes", "policy_family": "tariff",
            "topic_key": "tariff:china:us", "series_key": "series1",
            "proposition": "us tariffs china", "stance": "support",
            "notional_usdc": 5000, "signal_ts": 1000,
        }
        summary = {
            "relation_type": "none", "related_market_count": 1,
            "related_wallet_count": 1, "direction_agreement": 1,
            "related_notional_usdc": 5000,
        }
        db.save_related_signal(1, signal, summary, 30)

        rows = db.get_recent_related_signals(
            "tariff:china:us", "", 900, 1100)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tx_hash"], "tx1")
        self.assertEqual(rows[0]["stance"], "support")


if __name__ == "__main__":
    unittest.main()
