from __future__ import annotations

import unittest
from pathlib import Path

from finance import dialects, fingerprint

FIXTURES = Path(__file__).parent / "fixtures"


class CanonicalTest(unittest.TestCase):
    def test_excludes_changed_at(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        moved = dialects.RawRow(**{**base.__dict__, "changed_at": "2099-01-01 00:00:00"})
        self.assertEqual(fingerprint.canonical(base), fingerprint.canonical(moved))

    def test_excludes_category(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        recategorised = dialects.RawRow(**{**base.__dict__, "category": "Другое"})
        self.assertEqual(
            fingerprint.canonical(base), fingerprint.canonical(recategorised)
        )

    def test_amount_change_produces_different_canonical(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        cheaper = dialects.RawRow(**{**base.__dict__, "outcome_minor": 1})
        self.assertNotEqual(fingerprint.canonical(base), fingerprint.canonical(cheaper))

    def test_separator_in_a_field_cannot_forge_another_rows_identity(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[0]
        a = dialects.RawRow(**{**base.__dict__, "payee": "Foo|Bar", "comment": "Baz"})
        b = dialects.RawRow(**{**base.__dict__, "payee": "Foo", "comment": "Bar|Baz"})
        self.assertNotEqual(fingerprint.canonical(a), fingerprint.canonical(b))

    def test_debts_style_row_still_has_a_blank_currency_field(self) -> None:
        """Task 7b: `RawRow.outcome_currency` now holds a real inferred
        currency ("RUB", not "") for storage, but canonical() must re-blank
        it for identity exactly as before — assert the *exact* canonical
        string, not just that it differs from something."""
        row = dialects.read_rows(FIXTURES / "currency_full.csv")[0]
        self.assertEqual("RUB", row.outcome_currency, "sanity: storage is inferred")
        self.assertEqual(
            "2024-01-01|||Debts|100000||(RUB) Тинькофф Карта|100000|RUB",
            fingerprint.canonical(row),
        )

    def test_cross_dialect_identity_survives_currency_inference(self) -> None:
        """The same Debts<->declared-account transfer, stamped EUR by the
        full export and RUB by the month export (today's real dialect
        disagreement) — both must still hash to the same id, even though
        `_infer_stored_currency` now resolves them to different *stored*
        currencies along the way (RUB either way here, but for the general
        case storage is allowed to disagree across dialects; identity is
        not)."""
        full = dialects.read_rows(FIXTURES / "currency_full.csv")[-1]
        month = dialects.read_rows(FIXTURES / "currency_month.csv")[0]
        self.assertEqual(fingerprint.canonical(full), fingerprint.canonical(month))
        full_id = fingerprint.assign_ids([full])[0][0]
        month_id = fingerprint.assign_ids([month])[0][0]
        self.assertEqual(full_id, month_id)


class AssignIdsTest(unittest.TestCase):
    def test_identical_rows_get_distinct_stable_ids(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        ids = [i for i, _ in fingerprint.assign_ids(rows)]
        self.assertEqual(len(ids), len(set(ids)), "no id collisions")

    def test_ids_are_stable_across_runs(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        first = [i for i, _ in fingerprint.assign_ids(rows)]
        second = [i for i, _ in fingerprint.assign_ids(rows)]
        self.assertEqual(first, second)

    def test_ids_are_stable_when_input_order_changes(self) -> None:
        """created_at is unique per fixture row, so it identifies a row across
        orderings. Assigning ordinals by file position would fail this."""
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        forward = {r.created_at: i for i, r in fingerprint.assign_ids(rows)}
        backward = {
            r.created_at: i
            for i, r in fingerprint.assign_ids(list(reversed(rows)))
        }
        self.assertEqual(forward, backward)

    def test_ids_match_across_dialects(self) -> None:
        full = fingerprint.assign_ids(dialects.read_rows(FIXTURES / "full_dialect.csv"))
        month = fingerprint.assign_ids(
            dialects.read_rows(FIXTURES / "month_dialect.csv")
        )
        self.assertEqual([i for i, _ in full], [i for i, _ in month])


if __name__ == "__main__":
    unittest.main()
