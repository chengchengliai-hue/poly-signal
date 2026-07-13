import unittest
from unittest.mock import Mock, patch

import poller


class BestPriceTests(unittest.TestCase):
    def test_virtual_buy_crosses_best_ask(self):
        client = Mock()
        client.get_price.return_value = {"price": "0.55"}
        with patch.object(poller, "get_clob_read_client", return_value=client):
            price = poller.fetch_best_price("token", "BUY")

        client.get_price.assert_called_once_with("token", "SELL")
        self.assertEqual(price, 0.55)

    def test_virtual_sell_crosses_best_bid(self):
        client = Mock()
        client.get_price.return_value = {"price": "0.45"}
        with patch.object(poller, "get_clob_read_client", return_value=client):
            price = poller.fetch_best_price("token", "SELL")

        client.get_price.assert_called_once_with("token", "BUY")
        self.assertEqual(price, 0.45)


if __name__ == "__main__":
    unittest.main()
