from __future__ import annotations

from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from app.business_case import CustomerBusinessInputs, customer_business_case, qbr_business_case
from app.database import connect, initialize


class BusinessCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "business.sqlite"
        connection = connect(self.database)
        initialize(connection)
        connection.executemany(
            "INSERT INTO qbr_facts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("anticipated_signups_first_three_months", "40000", "integer", "QBR", "C3", "Three-month cohort."),
                ("tested_pass_rate_improvement_percentage_points", "9.5", "decimal", "QBR", "C3", "Tested feature uplift."),
                ("first_year_value_per_new_user_usd", "300.00", "decimal_usd", "QBR", "C3", "First-year value, not profit."),
                ("integration_consistency_objection", "true", "boolean", "QBR", "C3", "One integration to maintain."),
            ],
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_customer_and_provider_perspectives_remain_separate(self) -> None:
        result = customer_business_case(CustomerBusinessInputs(
            customer="Example Play",
            delta_verifications=100,
            activation_probability=Decimal("0.5"),
            contribution_per_activated_user_usd=Decimal("10"),
            delta_customer_bill_usd=Decimal("100"),
            incremental_operating_risk_cost_usd=Decimal("50"),
            implementation_cost_usd=Decimal("150"),
            pricing_assumption="Customer billing is a separately negotiated pass-through.",
            delta_customer_revenue_usd=Decimal("120"),
            delta_vendor_spend_usd=Decimal("-40"),
            incremental_delivery_cost_usd=Decimal("20"),
        ))
        self.assertEqual(result["customer_value"]["gross_incremental_value_usd"], "500.00")
        self.assertEqual(result["customer_value"]["incremental_net_value_usd"], "200.00")
        self.assertEqual(result["customer_value"]["break_even_contribution_per_activated_user_usd"], "6.00")
        self.assertEqual(result["provider_value"]["incremental_contribution_usd"], "140.00")
        self.assertEqual(result["assumptions"]["delta_customer_bill_usd"]["classification"], "User assumption")

    def test_no_uplift_has_no_break_even_contribution(self) -> None:
        result = customer_business_case(CustomerBusinessInputs(
            customer="Example Play", delta_verifications=0, activation_probability=Decimal("1"),
            contribution_per_activated_user_usd=Decimal("100"), delta_customer_bill_usd=Decimal("0"),
            incremental_operating_risk_cost_usd=Decimal("0"), implementation_cost_usd=Decimal("1"),
            pricing_assumption="No billing change assumed.", delta_customer_revenue_usd=Decimal("0"),
            delta_vendor_spend_usd=Decimal("0"), incremental_delivery_cost_usd=Decimal("0"),
        ))
        self.assertIsNone(result["customer_value"]["break_even_contribution_per_activated_user_usd"])

    def test_qbr_full_and_partial_realization_are_separate_from_csv_customers(self) -> None:
        full = qbr_business_case(self.database, "1", "0", "test_feature")
        partial = qbr_business_case(self.database, "0.5", "0", "test_feature")
        retained = qbr_business_case(self.database, "1", "0", "retain_existing_setup")
        self.assertTrue(full["is_separate_from_csv_customers"])
        self.assertEqual(full["scenario"]["full_realization_incremental_verifications"], 3800)
        self.assertEqual(full["scenario"]["gross_first_year_value_usd"], "1140000.00")
        self.assertEqual(full["scenario"]["break_even_implementation_budget_usd"], "1140000.00")
        self.assertEqual(partial["scenario"]["gross_first_year_value_usd"], "570000.00")
        self.assertEqual(retained["scenario"]["gross_first_year_value_usd"], "0.00")
        self.assertEqual(retained["scenario_comparison"]["retain_existing_setup"]["incremental_verifications"], "0.0000")
        self.assertEqual(full["scenario_comparison"]["adopt_alaska_feature"]["potential_additional_verifications_at_full_realization"], 3800)


if __name__ == "__main__":
    unittest.main()
