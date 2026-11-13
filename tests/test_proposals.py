from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.importer import run_import
from app.proposals import create_proposal, get_proposal, get_scenario, printable_proposal_html, save_scenario


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "core_pack"


class ProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source_dir = self.root / "source"
        shutil.copytree(FIXTURE_DIR, self.source_dir)
        self.database = self.root / "workbench.sqlite"
        run_import(self.source_dir, self.database, self.root / "raw", self.root / "report.json")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_saved_scenario_survives_reload_and_can_be_updated(self) -> None:
        saved = save_scenario(
            self.database, "Example test", "pair_comparison", "Example Play",
            "2026-02-02", "2026-02-02", {"projected_eligible_entrants": 1000},
        )
        loaded = get_scenario(self.database, saved["id"])
        self.assertEqual(loaded["payload"]["projected_eligible_entrants"], 1000)
        updated = save_scenario(
            self.database, "Updated test", "pair_comparison", "Example Play",
            "2026-02-02", "2026-02-02", {"projected_eligible_entrants": 2500}, saved["id"],
        )
        self.assertEqual(updated["name"], "Updated test")
        self.assertEqual(updated["payload"]["projected_eligible_entrants"], 2500)

    def test_frozen_proposal_has_sources_and_printable_html(self) -> None:
        scenario = save_scenario(
            self.database, "Example proposal", "pair_comparison", "Example Play",
            "2026-02-02", "2026-02-02", {"projected_eligible_entrants": 1000, "volume_mode": "recorded", "implementation_cost_usd": 20,
            "weighted_first_vendor_ranking": {"priorities": {"cost": 3, "match": 3, "timeout": 3}, "recommended_vendor_ids": ["V02"], "ranked_vendors": [], "note": "Separate from pair-swap economics."}},
        )
        created = create_proposal(self.database, scenario["id"])
        proposal = get_proposal(self.database, created["id"])
        rendered = printable_proposal_html(proposal)
        self.assertEqual(proposal["scenario"]["id"], scenario["id"])
        self.assertEqual(proposal["assumptions"]["projected_eligible_entrants"], 1000)
        self.assertEqual(proposal["assumptions"]["implementation_cost_usd"], "20.00")
        self.assertEqual(proposal["assumptions"]["volume_mode"], "recorded")
        self.assertEqual(proposal["weighted_first_vendor_ranking"]["recommended_vendor_ids"], ["V02"])
        self.assertEqual(len(proposal["sources"]), 3)
        self.assertIn("SHA-256", rendered)
        self.assertIn("Pair-cost formula", rendered)
        self.assertIn("Configuration comparison", rendered)
        self.assertIn("No modeled reorder recommendation", rendered)
        self.assertIn("Pilot success conditions", rendered)
        self.assertIn("Example proposal", rendered)

    def test_printable_available_proposal_contains_evidence_table_and_chart(self) -> None:
        comparison = {
            "status": "available",
            "calibration": {"sample_size": 100, "vendors": [
                {"vendor_id": "V01", "vendor_name": "Northstar", "unit_price_usd": "0.12", "checks_sent": 100, "matched": 70, "match_rate": "0.7000", "timeout": 5, "timeout_rate": "0.0500"},
                {"vendor_id": "V02", "vendor_name": "CivicMatch", "unit_price_usd": "0.07", "checks_sent": 100, "matched": 80, "match_rate": "0.8000", "timeout": 3, "timeout_rate": "0.0300"},
            ]},
            "current_order": [{"vendor_id": "V01", "vendor_name": "Northstar"}, {"vendor_id": "V02", "vendor_name": "CivicMatch"}],
            "alternative_order": [{"vendor_id": "V02", "vendor_name": "CivicMatch"}, {"vendor_id": "V01", "vendor_name": "Northstar"}],
            "model": {"current_order_cost_per_1000_eligible_entrants_usd": "141.00", "alternative_order_cost_per_1000_eligible_entrants_usd": "94.00", "formula": "C_AB = N*c_A + (N-M_A)*c_B; C_BA = N*c_B + (N-M_B)*c_A"},
            "pilot_economics": {"vendor_savings_per_1000_eligible_entrants_usd": "47.00", "projected_vendor_savings_usd": "47.00", "savings_after_implementation_usd": "27.00"},
            "weekly_consistency": {"statement": "The proposed order was cheaper in 1 of 1 comparable weeks.", "weeks": [{"week_starting": "2026-02-02", "savings_per_entrant_usd": "0.0470"}]},
            "decision_summary": {"headline": "Candidate for a controlled pilot", "detail": "Candidate for a controlled pilot.", "limitation": "Validate before testing."},
        }
        proposal = {
            "scenario": {"name": "Available proposal", "customer": "Example Play", "scope_from": "2026-02-02", "scope_to": "2026-02-02"},
            "proposal_version": "test", "generated_at": "2026-02-02T00:00:00+00:00", "comparison": comparison,
            "proposed_change": {"change": "Swap the first two recorded identity registries for a controlled test."},
            "weighted_first_vendor_ranking": {
                "priorities": {"cost": 3, "match": 3, "timeout": 3},
                "normalized_priorities": {"cost": "33.33%", "match": "33.33%", "timeout": "33.33%"},
                "recommended_vendor_ids": ["V03"],
                "ranked_vendors": [{"vendor_id": "V03", "vendor_name": "Meridian", "score": "85.000000"}],
                "note": "This first-vendor ranking is separate from the pair-swap economics and does not model waterfall savings.",
            },
            "assumptions": {"projected_eligible_entrants": 1000, "implementation_cost_usd": "20.00", "comparison_assumptions": ["Prices are linear."]},
            "unknowns": ["Billing requires validation."], "pilot_success_conditions": ["Guardrails agreed."], "sources": [],
        }
        rendered = printable_proposal_html(proposal)
        self.assertIn("Vendor comparison", rendered)
        self.assertIn("weekly-chart", rendered)
        self.assertIn("Decision summary", rendered)
        self.assertIn("Northstar", rendered)
        self.assertIn("Weighted First-Vendor Ranking", rendered)
        self.assertIn("Meridian", rendered)


if __name__ == "__main__":
    unittest.main()
