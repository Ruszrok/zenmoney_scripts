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


if __name__ == "__main__":
    unittest.main()
