from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.comparison import compare_first_two_registries
from app.database import connect, initialize
from app.vendor_recommendation import recommend_first_vendor


class VendorRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "recommendation.sqlite"
        connection = connect(self.database)
        initialize(connection)
        connection.executemany("INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("V01", "Current Registry", "identity_registry", '["US-NJ"]', "0.12", 400, "", "fixture", 1),
            ("V02", "Low Cost Registry", "identity_registry", '["US-NJ"]', "0.07", 500, "", "fixture", 2),
            ("V03", "High Match Registry", "identity_registry", '["US-NJ"]', "0.18", 500, "", "fixture", 3),
            ("V04", "No Evidence Registry", "identity_registry", '["US-NJ"]', "0.10", 500, "", "fixture", 4),
            ("V05", "Other Region Registry", "identity_registry", '["US-PA"]', "0.01", 500, "", "fixture", 5),
            ("V06", "Zero Sample Registry", "identity_registry", '["US-NJ"]', "0.09", 500, "", "fixture", 6),
        ])
        connection.executemany("INSERT INTO verification_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("2026-02-02", "Example Play", "US-NJ", 100, 90, 70, 20, 15, 12, 80, 2, "80", "fixture", 1),
            ("2026-02-09", "Example Play", "US-NJ", 100, 90, 70, 20, 15, 12, 80, 2, "80", "fixture", 2),
        ])
        rows = [
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 1, "V01", 100, 70, 25, 5, "12.00", "fixture", 1),
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 2, "V02", 30, 20, 8, 2, "2.10", "fixture", 2),
            ("2026-02-09", "Example Play", "US-NJ", "waterfall", 1, "V01", 100, 70, 25, 5, "12.00", "fixture", 3),
            ("2026-02-09", "Example Play", "US-NJ", "waterfall", 2, "V02", 30, 20, 8, 2, "2.10", "fixture", 4),
            # Totals, not the weekly percentage average, must determine these rates.
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V01", 10, 7, 2, 1, "1.20", "fixture", 5),
            ("2026-02-09", "Example Play", "US-NJ", "calibration", None, "V01", 90, 45, 36, 9, "10.80", "fixture", 6),
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V02", 10, 6, 3, 1, "0.70", "fixture", 7),
            ("2026-02-09", "Example Play", "US-NJ", "calibration", None, "V02", 90, 54, 27, 9, "6.30", "fixture", 8),
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V03", 10, 10, 0, 0, "1.80", "fixture", 9),
            ("2026-02-09", "Example Play", "US-NJ", "calibration", None, "V03", 90, 81, 9, 0, "16.20", "fixture", 10),
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V06", 0, 0, 0, 0, "0.00", "fixture", 11),
        ]
        connection.executemany("INSERT INTO vendor_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def result(self, cost: int, match: int, timeout: int) -> dict:
        return recommend_first_vendor(self.database, "Example Play", "2026-02-02", "2026-02-09", cost, match, timeout)

    def test_single_metric_priorities_pick_the_expected_vendor(self) -> None:
        self.assertEqual(self.result(5, 0, 0)["recommendation"]["vendor_ids"], ["V02"])
        self.assertEqual(self.result(0, 5, 0)["recommendation"]["vendor_ids"], ["V03"])
        self.assertEqual(self.result(0, 0, 5)["recommendation"]["vendor_ids"], ["V03"])

    def test_aggregate_counts_before_rates_and_report_unavailable_options(self) -> None:
        result = self.result(3, 3, 3)
        current = next(item for item in result["eligible_vendors"] if item["vendor_id"] == "V01")
        self.assertEqual(current["checks_sent"], 100)
        self.assertEqual(current["match_rate"], "0.520000")  # 52 / 100, not (70% + 50%) / 2.
        reasons = {item["vendor_id"]: item["reason_code"] for item in result["unavailable_options"]}
        self.assertEqual(reasons["V04"], "missing_calibration_evidence")
        self.assertEqual(reasons["V05"], "insufficient_region_coverage")
        self.assertEqual(reasons["V06"], "zero_calibration_sample")

    def test_all_zero_priorities_do_not_invent_a_recommendation(self) -> None:
        result = self.result(0, 0, 0)
        self.assertEqual(result["recommendation"]["vendor_ids"], [])
        self.assertEqual(result["recommendation"]["reason"], "Choose at least one priority.")

    def test_equal_metric_values_and_ties_are_joint_recommendations(self) -> None:
        connection = connect(self.database)
        connection.execute("UPDATE vendors SET cost_per_check_usd = '0.10' WHERE vendor_id IN ('V01', 'V02', 'V03')")
        connection.commit()
        connection.close()
        result = self.result(5, 0, 0)
        self.assertEqual(set(result["recommendation"]["vendor_ids"]), {"V01", "V02", "V03"})
        self.assertTrue(result["recommendation"]["joint"])
        self.assertTrue(all(item["metric_scores"]["cost"] == "1.000000" for item in result["eligible_vendors"]))

    def test_recommendation_does_not_change_existing_pair_cost_calculation(self) -> None:
        before = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.result(0, 5, 0)
        after = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(before["model"], after["model"])


if __name__ == "__main__":
    unittest.main()
