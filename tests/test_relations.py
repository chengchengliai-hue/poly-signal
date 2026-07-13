import unittest

from relations import build_market_relation, summarize_related_signals


class MarketRelationTests(unittest.TestCase):
    def test_deadline_variants_share_a_series(self):
        july = build_market_relation(
            "us-blockade-iran", "US blockade on Iran by...?",
            "Will the US announce a blockade on Iran by July 31?", "Yes")
        august = build_market_relation(
            "us-blockade-iran", "US blockade on Iran by...?",
            "Will the US announce a blockade on Iran by August 31?", "Yes")

        self.assertEqual(july["series_key"], august["series_key"])
        self.assertEqual(july["topic_key"], "military:iran:us")
        self.assertEqual(july["stance"], "support")

    def test_cross_event_tariff_markets_share_a_topic(self):
        first = build_market_relation(
            "trump-tariff", "Trump tariffs",
            "Will Donald Trump impose tariffs on China by July 31?", "Yes")
        second = build_market_relation(
            "china-import-duty", "Chinese import duties",
            "Will the US announce new Chinese import tariffs before August 31?",
            "Yes")

        self.assertNotEqual(first["series_key"], second["series_key"])
        self.assertEqual(first["topic_key"], second["topic_key"])
        self.assertEqual(first["topic_key"], "tariff:china:us")

    def test_candidate_markets_are_not_treated_as_same_thesis(self):
        vance = build_market_relation(
            "gop-nominee", "Republican Presidential Nominee 2028",
            "Will J.D. Vance win the 2028 Republican nomination?", "Yes")
        rubio = build_market_relation(
            "gop-nominee", "Republican Presidential Nominee 2028",
            "Will Marco Rubio win the 2028 Republican nomination?", "Yes")

        self.assertNotEqual(vance["series_key"], rubio["series_key"])
        self.assertEqual(vance["topic_key"], "")
        self.assertEqual(rubio["topic_key"], "")

    def test_broad_geopolitical_topic_requires_two_entities(self):
        relation = build_market_relation(
            "iran-airspace", "Iran airspace closure",
            "Will Iran fully close its airspace by July 31?", "Yes")

        self.assertEqual(relation["policy_family"], "military")
        self.assertEqual(relation["topic_key"], "")
        self.assertTrue(relation["series_key"])

    def test_negated_question_is_directionally_inverted(self):
        relation = build_market_relation(
            "no-tariff", "Trump tariffs",
            "Will Trump not impose new tariffs on China?", "Yes")
        self.assertEqual(relation["stance"], "oppose")

    def test_multiple_markets_and_wallets_create_strong_signal(self):
        current = {
            "tx_hash": "tx2", "wallet": "wallet2", "condition_id": "market2",
            "topic_key": "tariff:china:us", "series_key": "series2",
            "stance": "support", "notional_usdc": 6000,
        }
        previous = [{
            "tx_hash": "tx1", "wallet": "wallet1", "condition_id": "market1",
            "topic_key": "tariff:china:us", "series_key": "series1",
            "stance": "support", "notional_usdc": 5000,
        }]

        summary = summarize_related_signals(current, previous)

        self.assertTrue(summary["is_related"])
        self.assertTrue(summary["is_strong"])
        self.assertEqual(summary["relation_type"], "same_policy_topic")
        self.assertEqual(summary["related_market_count"], 2)
        self.assertEqual(summary["related_wallet_count"], 2)
        self.assertEqual(summary["direction_agreement"], 1)


if __name__ == "__main__":
    unittest.main()
