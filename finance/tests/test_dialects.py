from __future__ import annotations

import unittest
from pathlib import Path

from finance import dialects

FIXTURES = Path(__file__).parent / "fixtures"


class ParseAmountTest(unittest.TestCase):
    def test_dot_decimal(self) -> None:
        self.assertEqual(420, dialects.parse_amount("4.20"))

    def test_comma_decimal(self) -> None:
        self.assertEqual(420, dialects.parse_amount("4,20"))

    def test_space_thousands_separator(self) -> None:
        self.assertEqual(2500000, dialects.parse_amount("25 000,00"))

    def test_integer_without_decimals(self) -> None:
        self.assertEqual(2500000, dialects.parse_amount("25000"))

    def test_empty_is_zero(self) -> None:
        self.assertEqual(0, dialects.parse_amount(""))

    def test_rounds_half_up(self) -> None:
        self.assertEqual(767096, dialects.parse_amount("7670.96"))

    def test_non_breaking_space_thousands_separator(self) -> None:
        """U+00A0, not U+0020 — a regular space passing does not prove this."""
        self.assertEqual(2500000, dialects.parse_amount("25\N{NO-BREAK SPACE}000,00"))


class SplitCategoryTest(unittest.TestCase):
    def test_two_level(self) -> None:
        self.assertEqual(
            ("Еда", "Кафе и рестораны"),
            dialects.split_category("Еда / Кафе и рестораны"),
        )

    def test_top_level(self) -> None:
        self.assertEqual((None, "Машина"), dialects.split_category("Машина"))

    def test_embedded_slash_is_not_a_separator(self) -> None:
        self.assertEqual(
            ("Отпуск", "2023 France/Switzeland"),
            dialects.split_category("Отпуск / 2023 France/Switzeland"),
        )


class ReadRowsTest(unittest.TestCase):
    def test_full_dialect_kinds(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        self.assertEqual(5, len(rows))
        self.assertEqual(
            ["outcome", "outcome", "income", "transfer", "outcome"],
            [r.kind for r in rows],
        )

    def test_full_dialect_does_not_classify_everything_as_transfer(self) -> None:
        """Regression: both account names are populated on every row of the
        real full dump, so a presence-based rule marks all rows transfers."""
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        self.assertNotEqual(
            {"transfer"}, {r.kind for r in rows}, "kind must key on amounts"
        )

    def test_month_dialect_kinds_match_full_dialect(self) -> None:
        full = dialects.read_rows(FIXTURES / "full_dialect.csv")
        month = dialects.read_rows(FIXTURES / "month_dialect.csv")
        self.assertEqual([r.kind for r in full], [r.kind for r in month])

    def test_dialects_normalise_identically(self) -> None:
        full = dialects.read_rows(FIXTURES / "full_dialect.csv")
        month = dialects.read_rows(FIXTURES / "month_dialect.csv")
        self.assertEqual(full, month)

    def test_unused_side_is_blanked(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        expense = rows[0]
        self.assertEqual("", expense.income_account)
        self.assertEqual("", expense.income_currency)
        self.assertEqual(0, expense.income_minor)

    def test_income_row_blanks_outcome_side(self) -> None:
        income = dialects.read_rows(FIXTURES / "full_dialect.csv")[2]
        self.assertEqual("", income.outcome_account)
        self.assertEqual(767096, income.income_minor)

    def test_transfer_keeps_both_sides(self) -> None:
        tr = dialects.read_rows(FIXTURES / "full_dialect.csv")[3]
        self.assertEqual(2500000, tr.outcome_minor)
        self.assertEqual("RUB", tr.outcome_currency)
        self.assertEqual(33436, tr.income_minor)
        self.assertEqual("USD", tr.income_currency)


class NormalisationTest(unittest.TestCase):
    def test_collapses_internal_whitespace(self) -> None:
        self.assertEqual("Циан. Занесены", dialects.normalise_text("Циан.  Занесены"))

    def test_trims_and_handles_none(self) -> None:
        self.assertEqual("Zoom", dialects.normalise_text("  Zoom "))
        self.assertEqual("", dialects.normalise_text(None))

    def test_currency_kept_when_the_account_declares_one(self) -> None:
        self.assertEqual("EUR", dialects.account_currency("(EUR) Bunq", "EUR"))

    def test_currency_dropped_when_the_account_is_silent(self) -> None:
        """`Debts` is stamped EUR by one export and RUB by the other."""
        self.assertEqual("", dialects.account_currency("Debts", "EUR"))
        self.assertEqual("", dialects.account_currency("Debts", "RUB"))

    def test_currency_dropped_for_an_empty_account(self) -> None:
        self.assertEqual("", dialects.account_currency("", "EUR"))


if __name__ == "__main__":
    unittest.main()
