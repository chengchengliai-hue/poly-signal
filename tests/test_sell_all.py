import unittest
from unittest.mock import patch

import handler


class SellAllPositionsTests(unittest.TestCase):
    def test_any_wallet_sell_liquidates_all_wallet_positions(self):
        positions = [
            (1, "0xwallet", "market/a", "Market A", "token-a", "Yes",
             0.4, 10, 4, 80, "smart_money", "active", 0),
            (2, "0xwallet", "market/b", "Market B", "token-b", "Over",
             0.5, 10, 5, 80, "smart_money", "active", 0),
        ]
        trade = {
            "transactionHash": "sell-tx",
            "side": "SELL",
            "conditionId": "trigger-condition",
            "outcome": "No",
            "slug": "trigger-market",
            "title": "Trigger market",
            "price": 0.3,
            "size": 1,
        }

        def sell(token_id, shares, title):
            price = 0.6 if token_id == "token-a" else 0.7
            return {
                "success": True,
                "fillPrice": price,
                "filledShares": shares,
                "exitValue": price * shares,
            }

        with patch.object(handler, "is_wallet_relevant", return_value=True), \
             patch.object(handler, "is_seen", return_value=False), \
             patch.object(handler, "mark_seen"), \
             patch.object(handler, "get_active_positions_by_wallet",
                          return_value=positions), \
             patch.object(handler, "sell_position", side_effect=sell) as sell_mock, \
             patch.object(handler, "close_copy_trades_for_position") as close_mock, \
             patch.object(handler, "mark_position_closed") as position_close_mock, \
             patch.object(handler, "get_tracked_by_wallet", return_value=None), \
             patch.object(handler, "send_message"):
            handler.handle_trade(trade, "0xwallet", 0, 0, "sell")

        self.assertEqual(sell_mock.call_count, 2)
        self.assertEqual(close_mock.call_count, 2)
        self.assertEqual(position_close_mock.call_count, 2)
        self.assertEqual(
            {call.args[0] for call in close_mock.call_args_list}, {1, 2})


if __name__ == "__main__":
    unittest.main()
