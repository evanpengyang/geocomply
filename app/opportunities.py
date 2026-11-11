"""Deterministic opportunity ranking for the constrained pair model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .baseline import list_customers
from .comparison import ComparisonInputError, compare_first_two_registries


def list_opportunities(database_path: Path) -> dict[str, Any]:
    opportunities: list[dict[str, Any]] = []
    for customer in list_customers(database_path):
        try:
            comparison = compare_first_two_registries(
                database_path, customer["customer"], customer["available_from"], customer["available_to"], 0
            )
        except ComparisonInputError as error:
            comparison = {"status": "unavailable", "reason": str(error)}
        if comparison["status"] != "available":
            opportunities.append({
                "classification": "Insufficient data",
                "customer": customer["customer"],
                "scope": {"from": customer["available_from"], "to": customer["available_to"]},
                "result": "Insufficient data",
                "reason": comparison["reason"],
                "sort_value": None,
            })
            continue
        per_thousand = comparison["pilot_economics"]["vendor_savings_per_1000_eligible_entrants_usd"]
        saving = float(per_thousand)
        opportunities.append({
            "classification": "Modeled",
            "customer": customer["customer"],
            "scope": {"from": customer["available_from"], "to": customer["available_to"]},
            "current_pair": " → ".join(step["vendor_id"] for step in comparison["current_order"]),
            "proposed_pair": " → ".join(step["vendor_id"] for step in comparison["alternative_order"]),
            "modeled_savings_per_1000_eligible_entrants_usd": per_thousand,
            "calibration_sample_size": comparison["calibration"]["sample_size"],
            "proposed_order_cheaper_week_count": comparison["weekly_consistency"]["proposed_order_cheaper_week_count"],
            "comparable_week_count": comparison["weekly_consistency"]["comparable_week_count"],
            "result": "Potential saving" if saving > 0 else "Higher cost" if saving < 0 else "No difference",
            "reason": comparison["model"]["explanation"],
            "sort_value": saving,
        })
    opportunities.sort(key=lambda item: (item["sort_value"] is None, -(item["sort_value"] or 0), item["customer"]))
    return {"classification": "Modeled", "opportunities": opportunities}
