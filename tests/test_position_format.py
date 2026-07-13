import unittest

from bot import format_position


class PositionFormatTests(unittest.TestCase):
    def test_position_displays_original_score_breakdown(self):
        text, _ = format_position(
            "0x1234567890abcdef", "Test market", "Yes", 10, 5, 90, 1,
            ["数小时内新建(+20)", "大额($5,534)(+20)"],
        )

        self.assertIn("原始评分: 90", text)
        self.assertIn(
            "评分构成: 基础分50 + 数小时内新建(+20) + 大额($5,534)(+20)",
            text,
        )

    def test_position_marks_missing_historical_details(self):
        text, _ = format_position(
            "0x1234567890abcdef", "Test market", "Yes", 10, 5, 80, 1)

        self.assertIn("历史评分明细未保存", text)


if __name__ == "__main__":
    unittest.main()
