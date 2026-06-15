import unittest

from src.haircut_bot.parsing import (
    build_display_label,
    build_event_title,
    parse_amount_to_won,
    parse_balance_from_text,
    parse_transaction,
)


class ParsingTests(unittest.TestCase):
    def test_parse_spend_transaction(self) -> None:
        parsed = parse_transaction("이발 3만", ("충전", "입금"), "man")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.kind, "spend")
        self.assertEqual(parsed.amount_won, 30000)
        self.assertEqual(parsed.delta_won, -30000)

    def test_parse_charge_transaction(self) -> None:
        parsed = parse_transaction("충전 30만", ("충전", "입금"), "man")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.kind, "charge")
        self.assertEqual(parsed.amount_won, 300000)
        self.assertEqual(parsed.delta_won, 300000)

    def test_parse_with_comment_suffix(self) -> None:
        parsed = parse_transaction("염색 4만 : 예치금 차감", ("충전", "입금"), "man")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.label, "염색")
        self.assertEqual(parsed.amount_won, 40000)

    def test_build_event_title(self) -> None:
        title = build_event_title("서하 은호", "이발", 30000, 360000)
        self.assertEqual(title, "서하 은호 이발 (3만) 잔액 360,000원")

    def test_display_label_does_not_duplicate_prefix(self) -> None:
        title = build_display_label("서하 은호", "서하 은호 이발")
        self.assertEqual(title, "서하 은호 이발")

    def test_parse_balance_from_summary_or_description(self) -> None:
        self.assertEqual(parse_balance_from_text("이발 (3만) 잔액 330,000원"), 330000)
        self.assertEqual(parse_balance_from_text("balance_won=290000"), 290000)

    def test_parse_amount_to_won(self) -> None:
        self.assertEqual(parse_amount_to_won("36만"), 360000)
        self.assertEqual(parse_amount_to_won("360000원"), 360000)
        self.assertEqual(parse_amount_to_won("36"), 360000)


if __name__ == "__main__":
    unittest.main()
