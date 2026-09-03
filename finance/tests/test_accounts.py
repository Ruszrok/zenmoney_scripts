from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from finance import accounts, db, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class GuessKindTest(unittest.TestCase):
    def test_savings_from_russian_keywords(self) -> None:
        self.assertEqual("savings", accounts.guess_kind("(RUB) Тинькофф Накопительный"))
        self.assertEqual("savings", accounts.guess_kind("(RUB) Тинькофф депозит 8"))
        self.assertEqual("savings", accounts.guess_kind("Вклад промсвязьбанк"))

    def test_investment(self) -> None:
        self.assertEqual("investment", accounts.guess_kind("Брокерский счет"))
        self.assertEqual("investment", accounts.guess_kind("ИИС"))
        self.assertEqual("investment", accounts.guess_kind("Interactive brokers"))

    def test_credit_beats_spending(self) -> None:
        self.assertEqual("credit", accounts.guess_kind("(RUB) Тинькофф кредитка"))

    def test_cash(self) -> None:
        self.assertEqual("cash", accounts.guess_kind("(EUR) cash"))
        self.assertEqual("cash", accounts.guess_kind("(RUB) Наличные"))
        self.assertEqual("cash", accounts.guess_kind("(RUB) Домашняя кубышка"))

    def test_debt(self) -> None:
        self.assertEqual("debt", accounts.guess_kind("Debts"))

    def test_default_is_spending(self) -> None:
        self.assertEqual("spending", accounts.guess_kind("(EUR) Bunq"))

    def test_every_guess_is_a_valid_kind(self) -> None:
        for name in ("(EUR) Bunq", "ИИС", "Debts", "(EUR) cash"):
            self.assertIn(accounts.guess_kind(name), accounts.KINDS)


class TomlRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_seed_produces_parseable_toml_for_every_account(self) -> None:
        text = accounts.seed_toml(self.conn)
        parsed = tomllib.loads(text)
        self.assertIn("(EUR) Bunq", parsed["accounts"])
        self.assertEqual("spending", parsed["accounts"]["(EUR) Bunq"]["kind"])

    def test_apply_sets_kinds(self) -> None:
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        row = self.conn.execute(
            "SELECT kind FROM accounts WHERE name = ?", ("(RUB) Тинькофф депозит 8",)
        ).fetchone()
        self.assertEqual("savings", row["kind"])

    def test_apply_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            accounts.apply_toml(
                self.conn, '[accounts."(EUR) Bunq"]\nkind = "nonsense"\n'
            )

    def test_alias_of_links_accounts(self) -> None:
        accounts.apply_toml(
            self.conn,
            '[accounts."(EUR) Bunq"]\nkind = "spending"\n'
            '[accounts."(USD) Wise"]\nkind = "spending"\nalias_of = "(EUR) Bunq"\n',
        )
        row = self.conn.execute(
            "SELECT alias_of FROM accounts WHERE name = ?", ("(USD) Wise",)
        ).fetchone()
        target = self.conn.execute(
            "SELECT id FROM accounts WHERE name = ?", ("(EUR) Bunq",)
        ).fetchone()
        self.assertEqual(target["id"], row["alias_of"])

    def test_reseeding_preserves_hand_edited_fields(self) -> None:
        """seed -> hand-edit -> apply -> re-seed -> re-apply must not lose data."""
        accounts.apply_toml(
            self.conn,
            '[accounts."(EUR) Bunq"]\nkind = "spending"\n'
            '[accounts."(USD) Wise"]\nkind = "savings"\n'
            'alias_of = "(EUR) Bunq"\n'
            'opening_balance_minor = 123456\n'
            'opening_date = "2026-01-01"\n',
        )
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        row = self.conn.execute(
            "SELECT kind, alias_of, opening_balance_minor, opening_date "
            "FROM accounts WHERE name = ?", ("(USD) Wise",)
        ).fetchone()
        target = self.conn.execute(
            "SELECT id FROM accounts WHERE name = ?", ("(EUR) Bunq",)
        ).fetchone()
        self.assertEqual("savings", row["kind"])
        self.assertEqual(target["id"], row["alias_of"])
        self.assertEqual(123456, row["opening_balance_minor"])
        self.assertEqual("2026-01-01", row["opening_date"])

    def test_seed_escapes_special_characters_in_account_name(self) -> None:
        weird_name = 'Weird "Name" \\ Account'
        self.conn.execute(
            "INSERT INTO accounts (name, currency, kind) VALUES (?, ?, ?)",
            (weird_name, "EUR", "spending"),
        )
        self.conn.commit()
        text = accounts.seed_toml(self.conn)
        parsed = tomllib.loads(text)
        self.assertIn(weird_name, parsed["accounts"])
        self.assertEqual("spending", parsed["accounts"][weird_name]["kind"])

    def test_bad_alias_of_leaves_db_unmodified(self) -> None:
        before = [
            tuple(row)
            for row in self.conn.execute(
                "SELECT name, kind, alias_of, opening_balance_minor, opening_date "
                "FROM accounts ORDER BY name"
            ).fetchall()
        ]
        with self.assertRaises(ValueError):
            accounts.apply_toml(
                self.conn,
                '[accounts."(EUR) Bunq"]\nkind = "spending"\n'
                'alias_of = "Does Not Exist"\n',
            )
        after = [
            tuple(row)
            for row in self.conn.execute(
                "SELECT name, kind, alias_of, opening_balance_minor, opening_date "
                "FROM accounts ORDER BY name"
            ).fetchall()
        ]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
