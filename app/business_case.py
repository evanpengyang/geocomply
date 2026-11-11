"""Phase 4 business-value calculations.

Observed vendor costs and QBR facts stay separate from user-entered commercial
assumptions. This module intentionally makes no claim that vendor savings alter
a customer's bill, or that a modeled reorder creates verification uplift.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .baseline import _decimal_string, _money
from .database import connect, initialize


MONEY_QUANTUM = Decimal("0.01")


class BusinessCaseInputError(ValueError):
    """Raised for invalid user-entered business assumptions."""


def _decimal(value: Decimal | str | int | float, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise BusinessCaseInputError(f"{field} must be numeric.") from error
    if not result.is_finite():
        raise BusinessCaseInputError(f"{field} must be finite.")
    return result


def _assumption(value: Any, unit: str, note: str = "") -> dict[str, Any]:
    return {"classification": "User assumption", "value": value, "unit": unit, "note": note}


@dataclass(frozen=True)
class CustomerBusinessInputs:
    customer: str
    delta_verifications: int
    activation_probability: Decimal
    contribution_per_activated_user_usd: Decimal
    delta_customer_bill_usd: Decimal
    incremental_operating_risk_cost_usd: Decimal
    implementation_cost_usd: Decimal
    pricing_assumption: str
    delta_customer_revenue_usd: Decimal
    delta_vendor_spend_usd: Decimal
    incremental_delivery_cost_usd: Decimal


def customer_business_case(inputs: CustomerBusinessInputs) -> dict[str, Any]:
    if not inputs.customer.strip():
        raise BusinessCaseInputError("customer is required.")
    if inputs.delta_verifications < 0:
        raise BusinessCaseInputError("delta_verifications must be nonnegative.")
    if not Decimal("0") <= inputs.activation_probability <= Decimal("1"):
        raise BusinessCaseInputError("activation_probability must be between 0 and 1.")
    for field, value in {
        "contribution_per_activated_user_usd": inputs.contribution_per_activated_user_usd,
        "implementation_cost_usd": inputs.implementation_cost_usd,
        "incremental_operating_risk_cost_usd": inputs.incremental_operating_risk_cost_usd,
        "incremental_delivery_cost_usd": inputs.incremental_delivery_cost_usd,
    }.items():
        if value < 0:
            raise BusinessCaseInputError(f"{field} must be nonnegative.")

    activated_users = Decimal(inputs.delta_verifications) * inputs.activation_probability
    gross_customer_value = activated_users * inputs.contribution_per_activated_user_usd
    customer_costs = inputs.delta_customer_bill_usd + inputs.incremental_operating_risk_cost_usd + inputs.implementation_cost_usd
    customer_net = gross_customer_value - customer_costs
    provider_contribution = inputs.delta_customer_revenue_usd - inputs.delta_vendor_spend_usd - inputs.incremental_delivery_cost_usd
    break_even = None if activated_users <= 0 else customer_costs / activated_users

    return {
        "classification": "Modeled",
        "scenario_type": "csv_customer",
        "customer": inputs.customer,
        "assumptions": {
            "delta_verifications": _assumption(inputs.delta_verifications, "verifications", "Pure reorders default to 0; any uplift must be entered separately."),
            "activation_probability": _assumption(_decimal_string(inputs.activation_probability, Decimal("0.0001")), "probability"),
            "contribution_per_activated_user_usd": _assumption(_money(inputs.contribution_per_activated_user_usd), "USD"),
            "delta_customer_bill_usd": _assumption(_money(inputs.delta_customer_bill_usd), "USD", "Vendor costs do not become customer billing without this explicit assumption."),
            "incremental_operating_risk_cost_usd": _assumption(_money(inputs.incremental_operating_risk_cost_usd), "USD"),
            "implementation_cost_usd": _assumption(_money(inputs.implementation_cost_usd), "USD"),
            "pricing_assumption": _assumption(inputs.pricing_assumption, "text"),
            "delta_customer_revenue_usd": _assumption(_money(inputs.delta_customer_revenue_usd), "USD"),
            "delta_vendor_spend_usd": _assumption(_money(inputs.delta_vendor_spend_usd), "USD", "Negative values represent vendor-cost savings."),
            "incremental_delivery_cost_usd": _assumption(_money(inputs.incremental_delivery_cost_usd), "USD"),
        },
        "customer_value": {
            "classification": "Modeled",
            "value_generating_users": _decimal_string(activated_users, Decimal("0.0001")),
            "gross_incremental_value_usd": _money(gross_customer_value),
            "incremental_net_value_usd": _money(customer_net),
            "break_even_contribution_per_activated_user_usd": None if break_even is None else _money(break_even),
            "break_even_note": "Not available because delta verifications × activation probability is not positive." if break_even is None else None,
        },
        "provider_value": {
            "classification": "Modeled",
            "incremental_contribution_usd": _money(provider_contribution),
            "formula": "delta customer revenue − delta vendor spend − incremental delivery costs",
        },
    }


def _qbr_fact_map(database_path: Path) -> dict[str, sqlite3.Row]:
    connection = connect(database_path)
    try:
        initialize(connection)
        rows = connection.execute("SELECT * FROM qbr_facts").fetchall()
    finally:
        connection.close()
    values = {row["fact_key"]: row for row in rows}
    required = {
        "anticipated_signups_first_three_months",
        "tested_pass_rate_improvement_percentage_points",
        "first_year_value_per_new_user_usd",
        "integration_consistency_objection",
    }
    missing = required - values.keys()
    if missing:
        raise BusinessCaseInputError(f"QBR facts are unavailable: {', '.join(sorted(missing))}.")
    return values


def qbr_business_case(
    database_path: Path,
    realization_factor: Decimal | str | int | float,
    implementation_budget_usd: Decimal | str | int | float,
    adoption_choice: str,
) -> dict[str, Any]:
    """Evaluate Betting.com's distinct QBR adoption decision, never a CSV customer."""
    realization = _decimal(realization_factor, "realization_factor")
    budget = _decimal(implementation_budget_usd, "implementation_budget_usd")
    if not Decimal("0") <= realization <= Decimal("1"):
        raise BusinessCaseInputError("realization_factor must be between 0 and 1.")
    if budget < 0:
        raise BusinessCaseInputError("implementation_budget_usd must be nonnegative.")
    if adoption_choice not in {"retain_existing_setup", "test_feature"}:
        raise BusinessCaseInputError("adoption_choice must be retain_existing_setup or test_feature.")

    facts = _qbr_fact_map(database_path)
    signups = Decimal(facts["anticipated_signups_first_three_months"]["value"])
    uplift = Decimal(facts["tested_pass_rate_improvement_percentage_points"]["value"]) / Decimal("100")
    value_per_user = Decimal(facts["first_year_value_per_new_user_usd"]["value"])
    full_incremental_verifications = signups * uplift
    adoption_incremental_verifications = full_incremental_verifications * realization
    adoption_gross_value = adoption_incremental_verifications * value_per_user
    adoption_after_budget = adoption_gross_value - budget
    realized_incremental_verifications = adoption_incremental_verifications if adoption_choice == "test_feature" else Decimal("0")
    gross_value = adoption_gross_value if adoption_choice == "test_feature" else Decimal("0")
    net_after_budget = adoption_after_budget if adoption_choice == "test_feature" else Decimal("0")

    observed_facts = {
        key: {
            "classification": "Observed QBR fact",
            "value": row["value"],
            "source_label": row["source_label"],
            "citation": row["citation"],
            "note": row["note"],
        }
        for key, row in facts.items()
    }
    return {
        "classification": "Modeled",
        "scenario_type": "betting_com_qbr",
        "customer": "Betting.com",
        "is_separate_from_csv_customers": True,
        "decision": {
            "classification": "Observed QBR fact",
            "current_choice": "retain_existing_setup",
            "integration_consistency_objection": "Betting.com chose one engineering integration to maintain instead of a distinct Alaska setup.",
            "evaluated_choice": adoption_choice,
        },
        "observed_qbr_facts": observed_facts,
        "assumptions": {
            "realization_factor": _assumption(_decimal_string(realization, Decimal("0.0001")), "probability", "Fraction of incremental verifications that become value-generating users."),
            "implementation_budget_usd": _assumption(_money(budget), "USD", "User-entered budget; the transcript does not provide an implementation estimate."),
        },
        "scenario": {
            "classification": "Modeled",
            "anticipated_signups_first_three_months": int(signups),
            "tested_uplift_percentage_points": _decimal_string(uplift * Decimal("100"), Decimal("0.1")),
            "full_realization_incremental_verifications": int(full_incremental_verifications),
            "incremental_verifications_at_selected_realization": _decimal_string(realized_incremental_verifications, Decimal("0.0001")),
            "gross_first_year_value_usd": _money(gross_value),
            "break_even_implementation_budget_usd": _money(gross_value),
            "net_value_after_entered_budget_usd": _money(net_after_budget),
            "interpretation": (
                "Retaining the existing setup is the recorded QBR choice; no feature-uplift value is modeled."
                if adoption_choice == "retain_existing_setup"
                else "This is a first-year feature-adoption scenario, not profit, realized value, or an annualized signup forecast."
            ),
        },
        "scenario_comparison": {
            "classification": "Modeled",
            "retain_existing_setup": {
                "incremental_verifications": "0.0000",
                "gross_first_year_value_usd": "0.00",
                "value_after_implementation_budget_usd": "0.00",
                "implementation_budget_applied_usd": "0.00",
                "note": "Zero incremental benefit relative to the existing setup.",
            },
            "adopt_alaska_feature": {
                "potential_additional_verifications_at_full_realization": int(full_incremental_verifications),
                "incremental_verifications_at_selected_realization": _decimal_string(adoption_incremental_verifications, Decimal("0.0001")),
                "gross_first_year_value_usd": _money(adoption_gross_value),
                "value_after_implementation_budget_usd": _money(adoption_after_budget),
                "implementation_budget_applied_usd": _money(budget),
                "note": "The entered implementation budget is applied only to the adoption scenario; gross value minus budget is not profit.",
            },
        },
    }
