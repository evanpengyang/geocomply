from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.comparison import compare_first_two_registries
from app.database import connect, initialize
from app.opportunities import list_opportunities


class PairComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "comparison.sqlite"
        self.seed_database()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_database(self, reverse: bool = False) -> None:
        connection = connect(self.database)
        initialize(connection)
        vendors = [
            ("V01", "Northstar", "identity_registry", '["US-NJ"]', "0.12", 400, "", "fixture", 1),
            ("V02", "CivicMatch", "identity_registry", '["US-NJ"]', "0.07", 600, "", "fixture", 2),
            ("V04", "TelcoMatch", "phone_cfa", '["US-NJ"]', "0.15", 300, "", "fixture", 3),
            ("V06", "SanctionScan", "sanctions_pep", '["US-NJ"]', "0.04", 200, "", "fixture", 4),
        ]
        connection.executemany("INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", vendors)
        connection.execute(
            """INSERT INTO verification_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2026-02-02", "Example Play", "US-NJ", 100, 90, 70, 20, 15, 12, 80, 2, "80", "fixture", 1),
        )
        first, second = ("V02", "V01") if reverse else ("V01", "V02")
        waterfall = [
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 0, "V06", 100, 2, 98, 0, "4.00", "fixture", 1),
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 1, first, 100, 70, 30, 0, "12.00", "fixture", 2),
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 2, second, 30, 20, 10, 0, "2.10", "fixture", 3),
            ("2026-02-02", "Example Play", "US-NJ", "waterfall", 3, "V04", 10, 6, 4, 0, "1.50", "fixture", 4),
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V01", 100, 70, 25, 5, "12.00", "fixture", 5),
            ("2026-02-02", "Example Play", "US-NJ", "calibration", None, "V02", 100, 80, 17, 3, "7.00", "fixture", 6),
        ]
        connection.executemany("INSERT INTO vendor_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", waterfall)
        connection.commit()
        connection.close()

    def test_hand_calculated_pair_cost_and_projection(self) -> None:
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000, 20)
        self.assertEqual(result["status"], "available")
        self.assertEqual([item["vendor_id"] for item in result["current_order"]], ["V01", "V02"])
        self.assertEqual(result["calibration"]["sample_size"], 100)
        self.assertEqual(result["model"]["current_order_cost_for_calibration_usd"], "14.10")
        self.assertEqual(result["model"]["alternative_order_cost_for_calibration_usd"], "9.40")
        self.assertEqual(result["model"]["savings_from_switch_for_calibration_usd"], "4.70")
        self.assertEqual(result["model"]["per_entrant_savings_usd"], "0.0470")
        self.assertEqual(result["model"]["estimated_future_savings_usd"], "47.00")
        self.assertEqual(result["model"]["current_order_cost_per_1000_eligible_entrants_usd"], "141.00")
        self.assertEqual(result["model"]["alternative_order_cost_per_1000_eligible_entrants_usd"], "94.00")
        self.assertEqual(result["weekly_consistency"]["statement"], "The proposed order was cheaper in 1 of 1 comparable weeks.")
        self.assertEqual(result["pilot_economics"]["vendor_savings_per_1000_eligible_entrants_usd"], "47.00")
        self.assertEqual(result["pilot_economics"]["projected_vendor_savings_usd"], "47.00")
        self.assertEqual(result["pilot_economics"]["savings_after_implementation_usd"], "27.00")
        self.assertEqual(result["pilot_economics"]["break_even_eligible_volume"], 426)
        self.assertIn("Candidate for a controlled pilot", result["decision_summary"]["detail"])
        self.assertIn("27.00", result["decision_summary"]["detail"])
        self.assertEqual(result["model"]["pair_cost_reduction_pct"], "33.333333")
        self.assertEqual(result["pilot_economics"]["recorded_period"]["eligible_verification_volume"], 100)
        self.assertEqual([item["vendor_id"] for item in result["preserved_configuration"]["mandatory_screening"]], ["V06"])
        self.assertEqual([item["vendor_id"] for item in result["preserved_configuration"]["remaining_configured_steps"]], ["V04"])

    def test_reversing_order_reverses_savings_sign(self) -> None:
        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "comparison.sqlite"
        self.seed_database(reverse=True)
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(result["model"]["savings_from_switch_for_calibration_usd"], "-4.70")
        self.assertEqual(result["model"]["estimated_future_savings_usd"], "-47.00")
        self.assertIn("Retain the current order", result["decision_summary"]["detail"])

    def test_unequal_calibration_samples_disable_comparison(self) -> None:
        connection = connect(self.database)
        connection.execute("UPDATE vendor_weekly SET checks_sent = 99 WHERE cohort = 'calibration' AND vendor_id = 'V02'")
        connection.commit()
        connection.close()
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "calibration_sample_count_mismatch")

    def test_zero_calibration_sample_disables_comparison(self) -> None:
        connection = connect(self.database)
        connection.execute(
            """UPDATE vendor_weekly
               SET checks_sent = 0, matched = 0, no_match = 0, timeout = 0, cost_usd = '0.00'
               WHERE cohort = 'calibration'"""
        )
        connection.commit()
        connection.close()
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "zero_calibration_sample")

    def test_single_registry_configuration_is_unavailable(self) -> None:
        connection = connect(self.database)
        connection.execute("DELETE FROM vendor_weekly WHERE cohort = 'waterfall' AND vendor_id = 'V02'")
        connection.commit()
        connection.close()
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "two_registry_pair_unavailable")
        self.assertEqual(result["decision_summary"]["headline"], "No modeled reorder recommendation is available for this scope.")

    def test_equal_modeled_cost_has_neutral_summary(self) -> None:
        connection = connect(self.database)
        connection.execute("UPDATE vendor_weekly SET matched = 60 WHERE cohort = 'calibration' AND vendor_id = 'V01'")
        connection.execute("UPDATE vendor_weekly SET matched = 35 WHERE cohort = 'calibration' AND vendor_id = 'V02'")
        connection.commit()
        connection.close()
        result = compare_first_two_registries(self.database, "Example Play", "2026-02-02", "2026-02-02", 1000)
        self.assertEqual(result["model"]["savings_from_switch_for_calibration_usd"], "0.00")
        self.assertEqual(result["decision_summary"]["headline"], "No modeled cost difference")

    def test_opportunity_ranking_exposes_decision_fields(self) -> None:
        opportunities = list_opportunities(self.database)["opportunities"]
        self.assertEqual(len(opportunities), 1)
        row = opportunities[0]
        self.assertEqual(row["customer"], "Example Play")
        self.assertEqual(row["current_pair"], "V01 → V02")
        self.assertEqual(row["proposed_pair"], "V02 → V01")
        self.assertEqual(row["modeled_savings_per_1000_eligible_entrants_usd"], "47.00")
        self.assertEqual(row["proposed_order_cheaper_week_count"], 1)
        self.assertEqual(row["comparable_week_count"], 1)


if __name__ == "__main__":
    unittest.main()
