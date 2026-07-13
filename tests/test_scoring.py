import unittest

from handler import (_apply_market_score_adjustments,
                     _apply_signal_score_adjustments, _is_political_market)


class ScoringAdjustmentTests(unittest.TestCase):
    def test_new_wallet_under_six_hours_adds_ten(self):
        tags = []
        score = _apply_signal_score_adjustments(50, tags, 5.9, "", 0.2)
        self.assertEqual(score, 60)
        self.assertEqual(tags, ["6h内新钱包(+10)"])

    def test_test_heavy_adds_ten_without_wallet_age_bonus(self):
        tags = []
        score = _apply_signal_score_adjustments(
            50, tags, 2, "小额测试重仓", 0.2)
        self.assertEqual(score, 60)
        self.assertEqual(tags, ["小额测试重仓(+10)"])

    def test_reference_price_below_point_one_adds_ten(self):
        tags = []
        score = _apply_signal_score_adjustments(50, tags, 12, "", 0.099)
        self.assertEqual(score, 60)
        self.assertIn("低概率赔率(fpmm=0.099)(+10)", tags)

        tags = []
        score = _apply_signal_score_adjustments(50, tags, 12, "", 0.1)
        self.assertEqual(score, 50)
        self.assertEqual(tags, [])

    def test_near_expiry_adds_score_by_urgency(self):
        tags = []
        score = _apply_market_score_adjustments(
            60, tags, "Will France win today?", "market/test", 5.5, {}, {})
        self.assertEqual(score, 80)
        self.assertIn("6h内临期(+20)", tags)

        tags = []
        score = _apply_market_score_adjustments(
            60, tags, "Will France win tomorrow?", "market/test", 23.0, {}, {})
        self.assertEqual(score, 75)
        self.assertIn("24h内临期(+15)", tags)

        tags = []
        score = _apply_market_score_adjustments(
            60, tags, "Will France win soon?", "market/test", 60.0, {}, {})
        self.assertEqual(score, 68)
        self.assertIn("72h内临期(+8)", tags)

    def test_political_market_adds_score(self):
        market = {"tags": ["Politics"], "description": "US presidential election"}
        event = {"title": "2026 Midterms"}
        tags = []
        score = _apply_market_score_adjustments(
            70, tags, "Will a Democrat win?", "market/election", -1, market, event)
        self.assertEqual(score, 85)
        self.assertIn("政治/政策事件(+15)", tags)
        self.assertTrue(_is_political_market(
            "Will a Democrat win?", "market/election", market, event))

    def test_adjusted_score_caps_at_100(self):
        tags = []
        score = _apply_market_score_adjustments(
            95, tags, "Will Trump win the election?", "market/election",
            3.0, {}, {})
        self.assertEqual(score, 100)

    def test_sports_descriptions_do_not_match_political_substrings(self):
        market = {
            "question": "France vs Morocco: Team to Advance",
            "description": (
                "France will advance. The primary resolution source is FIFA."
            ),
            "tags": ["Sports"],
        }
        event = {"title": "2026 FIFA World Cup", "category": "Sports"}

        self.assertFalse(_is_political_market(
            market["question"], "market/team-to-advance", market, event))


if __name__ == "__main__":
    unittest.main()
