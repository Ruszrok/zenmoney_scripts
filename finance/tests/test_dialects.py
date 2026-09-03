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

    def test_identity_regex_does_not_pick_up_the_broadened_czech_prefix(self) -> None:
        """account_currency() is IDENTITY ONLY and must stay on the old,
        narrower regex — see the module docstring. If this ever starts
        matching "(Czech)(USD) Чехия" too, ids for that account will change."""
        self.assertEqual("", dialects.account_currency("(Czech)(USD) Чехия", "USD"))


class CurrencyInferenceTest(unittest.TestCase):
    """Storage currency inference (`_infer_stored_currency`, exercised via
    `read_rows`) — distinct from `account_currency`'s identity-only rule."""

    def setUp(self) -> None:
        self.rows = {
            r.date: r for r in dialects.read_rows(FIXTURES / "currency_full.csv")
        }

    def test_equal_amount_transfer_inherits_a_declared_counterpart(self) -> None:
        """Debts (no prefix, raw EUR) vs. a declared (RUB) account, equal
        amounts: Debts must store RUB, not its own mislabelled raw EUR."""
        row = self.rows["2024-01-01"]
        self.assertEqual("RUB", row.outcome_currency)
        self.assertEqual("RUB", row.income_currency)

    def test_equal_amount_transfer_between_two_undeclared_accounts_swaps(self) -> None:
        """Neither Debts nor "Карточка - Альфа" declares a prefix, so there is
        no authoritative side to anchor on; each borrows the other's raw
        value. This still fixes Debts (EUR -> RUB) but, as a disclosed
        residual quirk on this specific undeclared/undeclared combination,
        flips the counterpart the other way (RUB -> EUR) for this one row.
        See dialects.py's module docstring and the task-7b report."""
        row = self.rows["2024-01-02"]
        self.assertEqual("RUB", row.outcome_currency, "Debts is fixed")
        self.assertEqual("EUR", row.income_currency, "documented residual quirk")

    def test_unequal_amount_transfer_falls_back_to_raw_currency(self) -> None:
        """Брокерский счет (no prefix) vs. a declared (EUR) account, but the
        amounts differ — a real cross-currency transfer, not a mislabel — so
        no inheritance happens; each side keeps its own raw CSV currency."""
        row = self.rows["2024-01-03"]
        self.assertEqual("RUB", row.outcome_currency)
        self.assertEqual("EUR", row.income_currency)

    def test_czech_account_declares_via_the_second_parenthesised_token(self) -> None:
        """"(Czech)(USD) Чехия" — the regex fix from task 7b: a currency
        token anywhere in the leading parenthesised run counts, not just the
        very first token."""
        row = self.rows["2024-01-04"]
        self.assertEqual("USD", row.outcome_currency)

    def test_undeclared_non_transfer_row_keeps_its_raw_currency(self) -> None:
        """Брокерский счет income, not a transfer: no counterpart to borrow
        from, so it simply keeps the raw CSV currency."""
        row = self.rows["2024-01-05"]
        self.assertEqual("RUB", row.income_currency)


if __name__ == "__main__":
    unittest.main()
