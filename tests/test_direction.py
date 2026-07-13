import unittest

from bot import format_alert
from poller import direction_label
from handler import _beijing_time


class DirectionTests(unittest.TestCase):
    def test_utc_database_time_is_displayed_in_beijing_time(self):
        self.assertEqual(
            _beijing_time("2026-07-13 07:59:36"),
            "2026-07-13 15:59:36 北京时间",
        )

    def test_yes_and_no_keep_binary_direction(self):
        self.assertEqual(direction_label("Yes", "BUY"), "bullish")
        self.assertEqual(direction_label("No", "BUY"), "bearish")

    def test_named_and_total_outcomes_are_selected_outcomes(self):
        self.assertEqual(direction_label("Over", "BUY"), "selected_outcome")
        self.assertEqual(direction_label("England", "BUY"), "selected_outcome")

    def test_non_yes_no_alert_names_selected_outcome(self):
        text, _ = format_alert(
            "0xwallet", "Portugal vs Croatia", "Over", "BUY", 3000, 80,
            [], "market/test", "selected_outcome")
        self.assertIn("看多 Over", text)
        self.assertNotIn("看空", text)


if __name__ == "__main__":
    unittest.main()
