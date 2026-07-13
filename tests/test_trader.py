import unittest
from unittest.mock import patch

import trader


class VirtualSellTests(unittest.TestCase):
    def test_virtual_sell_uses_current_market_price(self):
        with patch.object(trader, "VIRTUAL_COPY_TRADING", True), \
             patch.object(trader, "fetch_best_price", return_value=0.42) as price_mock:
            result = trader.sell_position("token-id", 10, "Test market")

        price_mock.assert_called_once_with("token-id", "SELL")
        self.assertTrue(result["success"])
        self.assertEqual(result["filledShares"], 10)
        self.assertEqual(result["fillPrice"], 0.42)
        self.assertEqual(result["exitValue"], 4.2)
        self.assertEqual(result["priceSource"], "clob_best_bid")

    def test_virtual_sell_stays_open_when_price_is_unavailable(self):
        with patch.object(trader, "VIRTUAL_COPY_TRADING", True), \
             patch.object(trader, "fetch_best_price", return_value=0):
            result = trader.sell_position("token-id", 10, "Test market")

        self.assertIsNone(result)

    def test_virtual_buy_uses_best_ask(self):
        with patch.object(trader, "VIRTUAL_COPY_TRADING", True), \
             patch.object(trader, "fetch_best_price", return_value=0.44) as price_mock:
            result = trader.copy_trade_buy(
                "token-id", "condition", "Yes", "", "market/test",
                "Test market", 5, 80, "smart_money", 0)

        price_mock.assert_called_once_with("token-id", "BUY")
        self.assertTrue(result["success"])
        self.assertEqual(result["fillPrice"], 0.44)
        self.assertEqual(result["priceSource"], "clob_best_ask")
        self.assertAlmostEqual(result["takingAmount"], 5 / 0.44)


if __name__ == "__main__":
    unittest.main()
