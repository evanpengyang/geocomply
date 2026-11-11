from __future__ import annotations

import csv
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.importer import (
    VENDOR_FUNNEL_FILE,
    VENDORS_FILE,
    VERIFICATION_FUNNEL_FILE,
    run_import,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "core_pack"


class EvidenceImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source_dir = self.root / "source"
        shutil.copytree(FIXTURE_DIR, self.source_dir)
        self.database = self.root / "idcomply.sqlite"
        self.raw_dir = self.root / "raw"
        self.report = self.root / "report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_import(self):
        return run_import(self.source_dir, self.database, self.raw_dir, self.report)

    def test_import_is_idempotent_and_keeps_calibration_separate(self) -> None:
        first = self.run_import()
        second = self.run_import()
        self.assertEqual(first.clean_row_counts, second.clean_row_counts)
        self.assertEqual(first.source_row_counts, second.source_row_counts)
        self.assertEqual(first.clean_row_counts["vendors"], 3)
        self.assertEqual(first.clean_row_counts["verification_weekly"], 1)
        self.assertEqual(first.clean_row_counts["vendor_weekly"], 3)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0], 8)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendor_weekly WHERE cohort = 'calibration'").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendor_weekly WHERE vendor_id = 'ANY_REGISTRY'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendor_weekly_with_vendor").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM qbr_facts").fetchone()[0], 4)
        finally:
            connection.close()

    def test_invalid_vendor_row_is_recorded_and_excluded(self) -> None:
        path = self.source_dir / VENDOR_FUNNEL_FILE
        with path.open(newline="") as source:
            rows = list(csv.DictReader(source))
        rows[0]["timeout"] = "2"  # 10 != 7 + 2 + 0
        with path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        result = self.run_import()
        self.assertTrue(any(issue["code"] == "check_count_mismatch" for issue in result.issues))
        self.assertEqual(result.clean_row_counts["vendor_weekly"], 2)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM import_issues WHERE code = 'check_count_mismatch'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT is_valid FROM raw_rows WHERE source_file = ? AND source_line = 2", (VENDOR_FUNNEL_FILE,)).fetchone()[0], 0)
        finally:
            connection.close()

    def test_no_join_is_used_to_multiply_funnel_spend(self) -> None:
        result = self.run_import()
        self.assertEqual(result.clean_row_counts["verification_weekly"], 1)
        connection = sqlite3.connect(self.database)
        try:
            spend = connection.execute("SELECT SUM(CAST(cost_usd AS REAL)) FROM vendor_weekly WHERE cohort = 'waterfall'").fetchone()[0]
            self.assertEqual(spend, 4.0)
            # The validation model stores independent grains; no customer-level join exists.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendor_weekly WHERE cohort = 'waterfall'").fetchone()[0], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
