import unittest
from unittest.mock import patch

import settle


class SettlementLookupTests(unittest.TestCase):
    def test_open_market_is_not_settled(self):
        market = {
            "closed": False,
            "accepting_orders": True,
            "tokens": [
                {"token_id": "yes-token", "outcome": "Yes", "price": 0.001},
                {"token_id": "no-token", "outcome": "No", "price": 0.999},
            ],
        }
        with patch.object(settle, "fetch_market_snapshot", return_value=market):
            price, snapshot = settle._get_settlement(
                "condition", "yes-token", "Yes")

        self.assertIsNone(price)
        self.assertIs(snapshot, market)

    def test_paused_market_is_not_treated_as_settled(self):
        market = {
            "closed": False,
            "accepting_orders": False,
            "tokens": [
                {"token_id": "yes-token", "outcome": "Yes", "price": 0.001},
                {"token_id": "no-token", "outcome": "No", "price": 0.999},
            ],
        }
        with patch.object(settle, "fetch_market_snapshot", return_value=market):
            price, snapshot = settle._get_settlement(
                "condition", "yes-token", "Yes")

        self.assertIsNone(price)
        self.assertIs(snapshot, market)

    def test_closed_winning_token_is_selected_by_id(self):
        market = {
            "closed": True,
            "accepting_orders": False,
            "tokens": [
                {"token_id": "mexico", "outcome": "Mexico", "price": 1,
                 "winner": True},
                {"token_id": "england", "outcome": "England", "price": 0,
                 "winner": False},
            ],
        }
        with patch.object(settle, "fetch_market_snapshot", return_value=market):
            price, _ = settle._get_settlement(
                "condition", "mexico", "Mexico")

        self.assertEqual(price, 1.0)

    def test_closed_losing_token_is_selected_by_id(self):
        market = {
            "closed": True,
            "accepting_orders": False,
            "tokens": [
                {"token_id": "over", "outcome": "Over", "price": 1},
                {"token_id": "under", "outcome": "Under", "price": 0},
            ],
        }
        with patch.object(settle, "fetch_market_snapshot", return_value=market):
            price, _ = settle._get_settlement(
                "condition", "under", "Under")

        self.assertEqual(price, 0.0)


if __name__ == "__main__":
    unittest.main()
