import unittest

import poller


def activity(tx_hash, amount, timestamp=100):
    return {
        "type": "TRADE",
        "side": "BUY",
        "transactionHash": tx_hash,
        "conditionId": "condition",
        "asset": "asset",
        "timestamp": timestamp,
        "usdcSize": amount,
    }


class TestHeavyProfileTests(unittest.TestCase):
    def test_wallet_age_uses_earliest_timestamp_not_api_order(self):
        now = 10_000
        activities = [{"timestamp": 9_900}, {"timestamp": 9_000}]
        with unittest.mock.patch.object(poller.time, "time", return_value=now):
            is_new, age_hours = poller.check_new_wallet("0xwallet", activities)
        self.assertTrue(is_new)
        self.assertAlmostEqual(age_hours, 1000 / 3600)

    def test_detects_ten_times_median_after_small_tests(self):
        activities = [activity(f"old-{i}", amount)
                      for i, amount in enumerate([100, 200, 300, 400, 500])]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 3000)

        self.assertTrue(profile["qualifies"])
        self.assertEqual(profile["order_count"], 5)
        self.assertEqual(profile["median_usdc"], 300)
        self.assertEqual(profile["multiple"], 10)

    def test_excludes_current_trade_and_merges_split_fills(self):
        activities = [
            activity("split", 40), activity("split", 60),
            activity("old-2", 100), activity("old-3", 100),
            activity("old-4", 100), activity("old-5", 100),
            activity("current", 3000, timestamp=200),
        ]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 3000)

        self.assertTrue(profile["qualifies"])
        self.assertEqual(profile["order_count"], 5)
        self.assertEqual(profile["median_usdc"], 100)

    def test_accepts_three_prior_buy_orders(self):
        activities = [activity(f"old-{i}", 100) for i in range(3)]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 2001)

        self.assertTrue(profile["qualifies"])

    def test_rejects_fewer_than_three_prior_buy_orders(self):
        activities = [activity(f"old-{i}", 10) for i in range(2)]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 3000)

        self.assertFalse(profile["qualifies"])

    def test_accepts_fifty_and_rejects_fifty_one_prior_orders(self):
        current = {"transactionHash": "current", "timestamp": 200}

        accepted = poller.test_heavy_profile(
            [activity(f"old-{i}", 100) for i in range(50)], current, 2001)
        rejected = poller.test_heavy_profile(
            [activity(f"old-{i}", 100) for i in range(51)], current, 2001)

        self.assertTrue(accepted["qualifies"])
        self.assertFalse(rejected["qualifies"])

    def test_requires_current_notional_strictly_above_two_thousand(self):
        activities = [activity(f"old-{i}", 100) for i in range(3)]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 2000)

        self.assertFalse(profile["qualifies"])

    def test_rejects_truncated_activity_history(self):
        activities = [activity(f"old-{i}", 10) for i in range(100)]
        current = {"transactionHash": "current", "timestamp": 200}

        profile = poller.test_heavy_profile(activities, current, 3000)

        self.assertFalse(profile["qualifies"])


if __name__ == "__main__":
    unittest.main()
