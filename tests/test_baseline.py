from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from app.baseline import _rate, get_baseline, list_customers
from app.importer import VENDOR_FUNNEL_FILE, VERIFICATION_FUNNEL_FILE, run_import


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "core_pack"


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source_dir = self.root / "source"
        shutil.copytree(FIXTURE_DIR, self.source_dir)
        self.database = self.root / "idcomply.sqlite"
        self.import_fixture()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def import_fixture(self) -> None:
        run_import(self.source_dir, self.database, self.root / "raw", self.root / "report.json")

    def test_baseline_matches_hand_calculated_customer_week(self) -> None:
        result = get_baseline(self.database, "Example Play", "2026-02-02", "2026-02-02")
        metrics = result["summary"]["metrics"]
        self.assertEqual(result["summary"]["configuration_count"], 1)
        self.assertFalse(result["summary"]["is_mixed_configuration"])
        self.assertEqual(metrics["vendor_spend_usd"], "4.00")
        self.assertEqual(metrics["cost_per_started_session_usd"], "0.0400")
        self.assertEqual(metrics["cost_per_completed_verification_usd"], "0.0500")
        self.assertEqual(metrics["funnel_rates"]["weighted_verification_pass"]["rate"], "0.8000")
        self.assertEqual(metrics["funnel_rates"]["document_completion"]["rate"], "0.8571")
        self.assertEqual(result["configurations"][0]["recorded_sequence"][0]["vendor_id"], "V06")

    def test_changed_configuration_is_not_hidden_in_an_average(self) -> None:
        verification_path = self.source_dir / VERIFICATION_FUNNEL_FILE
        with verification_path.open(newline="") as source:
            verification_rows = list(csv.DictReader(source))
        second_week = dict(verification_rows[0], week_starting="2026-02-09")
        verification_rows.append(second_week)
        with verification_path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=verification_rows[0].keys())
            writer.writeheader()
            writer.writerows(verification_rows)

        vendor_path = self.source_dir / VENDOR_FUNNEL_FILE
        with vendor_path.open(newline="") as source:
            vendor_rows = list(csv.DictReader(source))
        vendor_rows.append({
            "week_starting": "2026-02-09", "customer": "Example Play", "region": "US-NJ",
            "cohort": "waterfall", "step_no": "0", "vendor_id": "V01", "checks_sent": "100",
            "matched": "5", "no_match": "95", "timeout": "0", "cost_usd": "12.00",
        })
        with vendor_path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=vendor_rows[0].keys())
            writer.writeheader()
            writer.writerows(vendor_rows)

        self.import_fixture()
        result = get_baseline(self.database, "Example Play", "2026-02-02", "2026-02-09")
        self.assertTrue(result["summary"]["is_mixed_configuration"])
        self.assertEqual(result["summary"]["configuration_count"], 2)
        self.assertEqual(result["summary"]["metrics"]["vendor_spend_usd"], "16.00")
        self.assertEqual(
            [configuration["metrics"]["vendor_spend_usd"] for configuration in result["configurations"]],
            ["4.00", "12.00"],
        )

    def test_unavailable_scope_and_zero_denominator_are_explicit(self) -> None:
        result = get_baseline(self.database, "Example Play", "2025-01-01", "2025-01-31")
        self.assertEqual(result["status"], "not_available")
        self.assertIsNone(_rate(3, 0)["rate"])
        self.assertEqual(list_customers(self.database)[0]["customer"], "Example Play")


if __name__ == "__main__":
    unittest.main()
