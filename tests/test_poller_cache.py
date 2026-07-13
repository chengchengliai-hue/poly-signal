import unittest
from unittest.mock import patch

import poller


class RecentTradeCacheTests(unittest.TestCase):
    def setUp(self):
        poller._recent_trade_hashes.clear()
        poller._pending_trades.clear()

    def tearDown(self):
        poller._recent_trade_hashes.clear()
        poller._pending_trades.clear()

    def test_trade_hash_is_only_new_once(self):
        self.assertTrue(poller._remember_trade("tx1"))
        self.assertFalse(poller._remember_trade("tx1"))

    def test_cache_evicts_oldest_hash(self):
        with patch.object(poller, "RECENT_TRADE_CACHE_SIZE", 2):
            self.assertTrue(poller._remember_trade("tx1"))
            self.assertTrue(poller._remember_trade("tx2"))
            self.assertTrue(poller._remember_trade("tx3"))

        self.assertNotIn("tx1", poller._recent_trade_hashes)
        self.assertIn("tx2", poller._recent_trade_hashes)
        self.assertIn("tx3", poller._recent_trade_hashes)

    def test_activity_failure_is_not_cached_and_can_retry(self):
        trade = {
            "transactionHash": "retry-tx", "proxyWallet": "0xwallet",
            "side": "BUY", "price": 0.5, "size": 6000,
            "conditionId": "condition", "outcome": "YES",
        }
        callback = lambda *args: True

        with patch.object(poller, "fetch_wallet_activity",
                          side_effect=TimeoutError("temporary")):
            self.assertFalse(poller._process_polled_trade(trade, callback))
        self.assertNotIn("retry-tx", poller._recent_trade_hashes)

        activities = [{"timestamp": 1}]
        with patch.object(poller, "fetch_wallet_activity",
                          return_value=activities), \
             patch.object(poller, "check_new_wallet", return_value=(True, 1)), \
             patch.object(poller, "SIGNAL_CONFIRM_DELAY_SECONDS", 0):
            self.assertTrue(poller._process_polled_trade(trade, callback))
            poller._process_due_confirmations(callback)
        self.assertIn("retry-tx", poller._recent_trade_hashes)

    def test_callback_failure_is_not_cached(self):
        trade = {
            "transactionHash": "callback-fail", "proxyWallet": "0xwallet",
            "side": "SELL",
        }

        self.assertFalse(poller._process_polled_trade(
            trade, lambda *args: False))
        self.assertNotIn("callback-fail", poller._recent_trade_hashes)

    def test_rapid_round_trip_is_filtered_after_confirmation(self):
        trade = {
            "transactionHash": "buy-tx", "proxyWallet": "0xwallet",
            "side": "BUY", "price": 0.5, "size": 6000,
            "conditionId": "condition", "asset": "token",
            "outcome": "YES", "timestamp": 100,
        }
        activities = [
            {"type": "TRADE", "side": "BUY", "size": 6000,
             "timestamp": 100, "conditionId": "condition", "asset": "token"},
            {"type": "TRADE", "side": "SELL", "size": 6000,
             "timestamp": 120, "conditionId": "condition", "asset": "token"},
        ]
        callback_calls = []

        with patch.object(poller, "fetch_wallet_activity",
                          return_value=activities), \
             patch.object(poller, "check_new_wallet", return_value=(True, 1)), \
             patch.object(poller, "SIGNAL_CONFIRM_DELAY_SECONDS", 0):
            poller._process_polled_trade(
                trade, lambda *args: callback_calls.append(args))
            poller._process_due_confirmations(
                lambda *args: callback_calls.append(args))

        self.assertEqual(callback_calls, [])
        self.assertIn("buy-tx", poller._recent_trade_hashes)
        self.assertNotIn("buy-tx", poller._pending_trades)

    def test_confirmation_preserves_test_heavy_classification(self):
        trade = {
            "transactionHash": "heavy-tx", "proxyWallet": "0xwallet",
            "side": "BUY", "price": 0.5, "size": 6000,
            "conditionId": "condition", "asset": "token",
            "outcome": "YES", "timestamp": 200,
        }
        activities = []
        for index, amount in enumerate((100, 150, 200, 250, 300)):
            activities.append({
                "type": "TRADE", "side": "BUY", "usdcSize": amount,
                "size": amount, "price": 1, "timestamp": 100 + index,
                "transactionHash": f"old-{index}",
                "conditionId": "other", "asset": "other-token",
            })
        callback_calls = []

        with patch.object(poller, "fetch_wallet_activity",
                          return_value=activities), \
             patch.object(poller, "SIGNAL_CONFIRM_DELAY_SECONDS", 0):
            poller._process_polled_trade(
                trade, lambda *args: callback_calls.append(args) or True)
            poller._process_due_confirmations(
                lambda *args: callback_calls.append(args) or True)

        self.assertEqual(len(callback_calls), 1)
        self.assertEqual(callback_calls[0][-1], "小额测试重仓")
        self.assertIn("heavy-tx", poller._recent_trade_hashes)


if __name__ == "__main__":
    unittest.main()
