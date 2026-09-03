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
