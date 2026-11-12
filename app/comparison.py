"""Constrained, two-vendor ordering comparison for Phase 3.

This deliberately compares only the first two *identity-registry* calls in a
recorded waterfall. It does not estimate conversion, fraud, or downstream
effects, and it disables the result when the required customer/region/week
calibration checks cannot support the model.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any

from .baseline import _decimal_string, _money
from .database import connect, initialize
from .price_threshold import price_threshold


RATE_QUANTUM = Decimal("0.0001")
COST_QUANTUM = Decimal("0.0001")
PERCENT_QUANTUM = Decimal("0.000001")


class ComparisonInputError(ValueError):
    """Raised only for malformed comparison requests, not unsupported data."""


ASSUMPTIONS = [
    "Either registry match meets the same identity requirement.",
    "Everyone unresolved at the first registry reaches the second registry.",
    "Registry outcomes and timeouts are stable under reordering; retries and abandonment are not modeled.",
    "Unit pricing is linear and the matched calibration sample represents the modeled population.",
    "Equal weekly calibration counts support, but do not prove, common sample membership.",
    "Mandatory screening and all remaining configured steps are unchanged; downstream cost cancels only under these assumptions.",
    "This cost model does not establish production conversion or fraud equivalence.",
]


def _rate(numerator: int, denominator: int) -> str | None:
    return None if denominator == 0 else _decimal_string(Decimal(numerator) / Decimal(denominator), RATE_QUANTUM)


def _step(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "step_no": row["step_no"],
        "vendor_id": row["vendor_id"],
        "vendor_name": row["vendor_name"],
        "check_type": row["check_type"],
    }


def _unavailable(customer: str, from_date: str, to_date: str, code: str, reason: str) -> dict[str, Any]:
    return {
        "classification": "Observed",
        "status": "unavailable",
        "customer": customer,
        "scope": {"from": from_date, "to": to_date},
        "reason_code": code,
        "reason": reason,
        "decision_summary": {
            "classification": "Not available",
            "headline": "No modeled reorder recommendation is available for this scope.",
            "detail": reason,
            "limitation": "This model does not estimate additional successful verifications. Validate billing, eligibility, and routing rules before testing.",
        },
        "assumptions": ASSUMPTIONS,
    }


def _explanation(vendor_a: dict[str, Any], vendor_b: dict[str, Any], savings: Decimal) -> str:
    """Explain the deterministic pair calculation without inferring conversion."""
    price_a, price_b = Decimal(vendor_a["unit_price_usd"]), Decimal(vendor_b["unit_price_usd"])
    rate_a, rate_b = Decimal(vendor_a["match_rate"] or "0"), Decimal(vendor_b["match_rate"] or "0")
    if savings == 0:
        return "The observed match rates and listed prices yield equal modeled pair costs in either order."
    winner, other = (vendor_b, vendor_a) if savings > 0 else (vendor_a, vendor_b)
    winner_price, other_price = (price_b, price_a) if savings > 0 else (price_a, price_b)
    winner_rate, other_rate = (rate_b, rate_a) if savings > 0 else (rate_a, rate_b)
    order = "proposed" if savings > 0 else "current"
    if winner_price > other_price and winner_rate > other_rate:
        return (
            f"The {order} order is lower cost: {winner['vendor_name']} costs more per request but its higher observed "
            f"calibration match rate ({winner_rate * 100:.1f}% versus {other_rate * 100:.1f}%) avoids enough second checks."
        )
    if winner_price < other_price:
        return (
            f"The {order} order is lower cost: {winner['vendor_name']} has the lower listed price, and its observed "
            f"calibration match rate ({winner_rate * 100:.1f}% versus {other_rate * 100:.1f}%) yields a lower modeled pair cost."
        )
    return (
        f"The {order} order is lower cost under the listed prices and observed calibration match rates: "
        f"{winner['vendor_name']} is {winner_rate * 100:.1f}% versus {other['vendor_name']} at {other_rate * 100:.1f}%."
    )


def _decision_summary(
    vendor_a: dict[str, Any],
    vendor_b: dict[str, Any],
    per_entrant: Decimal,
    weekly_cheaper: int,
    weekly_total: int,
    projected: Decimal | None,
    after_implementation: Decimal | None,
    implementation_cost: Decimal,
) -> dict[str, str]:
    """State a bounded, reproducible recommendation from the pair-cost output."""
    current = f"{vendor_a['vendor_name']} ({vendor_a['vendor_id']}) → {vendor_b['vendor_name']} ({vendor_b['vendor_id']})"
    proposed = f"{vendor_b['vendor_name']} ({vendor_b['vendor_id']}) → {vendor_a['vendor_name']} ({vendor_a['vendor_id']})"
    per_thousand = _money(abs(per_entrant) * Decimal("1000"))
    limitation = "This model does not estimate additional successful verifications. Validate billing, eligibility, and routing rules before testing."
    if per_entrant > 0:
        detail = (
            f"Candidate for a controlled pilot. {proposed} models {per_thousand} lower vendor cost per 1,000 eligible entrants. "
            f"It was cheaper in {weekly_cheaper} of {weekly_total} comparable weeks."
        )
        if after_implementation is not None:
            detail += f" At the entered volume, savings after implementation cost are {_money(after_implementation)}."
        if projected is not None and implementation_cost > projected:
            detail += " The entered pilot volume does not recover the implementation cost."
        headline = "Candidate for a controlled pilot"
        if projected is not None and implementation_cost > projected:
            headline = "Potential unit-cost saving; entered volume does not recover implementation cost"
        return {"classification": "Modeled", "headline": headline, "detail": detail, "limitation": limitation}
    if per_entrant < 0:
        return {
            "classification": "Modeled",
            "headline": "Retain the current order on modeled cost grounds",
            "detail": f"Retain the current order on modeled cost grounds. Current order: {current}. The proposed order adds {per_thousand} per 1,000 eligible entrants.",
            "limitation": limitation,
        }
    return {
        "classification": "Modeled",
        "headline": "No modeled cost difference",
        "detail": "The current and proposed orders have equal modeled vendor cost in the selected comparable calibration cohorts.",
        "limitation": limitation,
    }


def compare_first_two_registries(
    database_path: Path,
    customer: str,
    from_date: str,
    to_date: str,
    projected_eligible_entrants: int | None = None,
    implementation_cost_usd: Decimal | str | int | float = Decimal("0"),
    include_weekly_consistency: bool = True,
) -> dict[str, Any]:
    """Model A→B vs B→A for a validated, stable observed configuration."""
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError as error:
        raise ComparisonInputError("Dates must use ISO format YYYY-MM-DD.") from error
    if start > end:
        raise ComparisonInputError("from must be on or before to.")
    if projected_eligible_entrants is not None and projected_eligible_entrants < 0:
        raise ComparisonInputError("projected_eligible_entrants must be nonnegative.")
    try:
        implementation_cost = Decimal(str(implementation_cost_usd))
    except Exception as error:
        raise ComparisonInputError("implementation_cost_usd must be numeric.") from error
    if not implementation_cost.is_finite() or implementation_cost < 0:
        raise ComparisonInputError("implementation_cost_usd must be nonnegative.")

    connection = connect(database_path)
    try:
        initialize(connection)
        if connection.execute("SELECT 1 FROM verification_weekly WHERE customer = ? LIMIT 1", (customer,)).fetchone() is None:
            raise ComparisonInputError(f"Customer {customer!r} is not available in observed evidence.")
        verification_periods = connection.execute(
            """
            SELECT region, week_starting FROM verification_weekly
            WHERE customer = ? AND week_starting >= ? AND week_starting <= ?
            ORDER BY region, week_starting
            """,
            (customer, from_date, to_date),
        ).fetchall()
        if not verification_periods:
            return _unavailable(customer, from_date, to_date, "no_verification_scope", "No observed verification-funnel rows exist for this customer and date range.")
        waterfall_rows = connection.execute(
            """
            SELECT * FROM vendor_weekly_with_vendor
            WHERE customer = ? AND cohort = 'waterfall' AND week_starting >= ? AND week_starting <= ?
            ORDER BY region, week_starting, step_no, vendor_id
            """,
            (customer, from_date, to_date),
        ).fetchall()
        calibration_rows = connection.execute(
            """
            SELECT vw.*, v.vendor_name, v.check_type, v.cost_per_check_usd
            FROM vendor_weekly AS vw
            INNER JOIN vendors AS v ON v.vendor_id = vw.vendor_id
            WHERE vw.customer = ? AND vw.cohort = 'calibration'
              AND vw.week_starting >= ? AND vw.week_starting <= ?
            ORDER BY vw.region, vw.week_starting, vw.vendor_id
            """,
            (customer, from_date, to_date),
        ).fetchall()
    finally:
        connection.close()

    waterfall_by_period: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in waterfall_rows:
        waterfall_by_period[(row["region"], row["week_starting"])].append(row)

    required_periods = [(row["region"], row["week_starting"]) for row in verification_periods]
    configurations: dict[tuple[tuple[int, str], ...], list[tuple[str, str]]] = defaultdict(list)
    for period in required_periods:
        sequence = waterfall_by_period.get(period, [])
        if not sequence:
            return _unavailable(customer, from_date, to_date, "missing_waterfall", "At least one verification-funnel period has no recorded waterfall configuration.")
        signature = tuple((int(row["step_no"]), row["vendor_id"]) for row in sequence)
        configurations[signature].append(period)
    if len(configurations) != 1:
        return _unavailable(
            customer, from_date, to_date, "configuration_changes",
            "The selected range contains more than one recorded waterfall configuration; narrow the date range before modeling a swap.",
        )

    current_sequence = waterfall_by_period[required_periods[0]]
    registry_positions = [(index, row) for index, row in enumerate(current_sequence) if row["check_type"] == "identity_registry"]
    if len(registry_positions) < 2:
        return _unavailable(
            customer, from_date, to_date, "two_registry_pair_unavailable",
            "The recorded waterfall does not contain two eligible identity-registry vendors to swap.",
        )
    first_position, vendor_a = registry_positions[0]
    second_position, vendor_b = registry_positions[1]
    vendor_a_id, vendor_b_id = vendor_a["vendor_id"], vendor_b["vendor_id"]

    # The first recorded registry receives one check for each eligible entrant in
    # this supplied case structure. This is not an assertion about production
    # retries; it is made explicit in the result so callers can choose a custom
    # scenario volume instead.
    first_registry_rows: list[sqlite3.Row] = []
    for period in required_periods:
        row = next(
            (candidate for candidate in waterfall_by_period[period]
             if candidate["step_no"] == vendor_a["step_no"] and candidate["vendor_id"] == vendor_a_id),
            None,
        )
        if row is None:
            return _unavailable(
                customer, from_date, to_date, "missing_first_registry_coverage",
                "A recorded first-registry row is missing for at least one comparable customer, region, and week.",
            )
        first_registry_rows.append(row)
    recorded_period_volume = sum(int(row["checks_sent"]) for row in first_registry_rows)

    calibration_by_period: dict[tuple[str, str], dict[str, sqlite3.Row]] = defaultdict(dict)
    for row in calibration_rows:
        if row["vendor_id"] in {vendor_a_id, vendor_b_id}:
            calibration_by_period[(row["region"], row["week_starting"])][row["vendor_id"]] = row

    calibration_a: list[sqlite3.Row] = []
    calibration_b: list[sqlite3.Row] = []
    for region, week in required_periods:
        samples = calibration_by_period[(region, week)]
        if vendor_a_id not in samples or vendor_b_id not in samples:
            return _unavailable(
                customer, from_date, to_date, "missing_calibration",
                f"Calibration is missing {vendor_a_id if vendor_a_id not in samples else vendor_b_id} for {region} in week {week}.",
            )
        row_a, row_b = samples[vendor_a_id], samples[vendor_b_id]
        if row_a["checks_sent"] != row_b["checks_sent"]:
            return _unavailable(
                customer, from_date, to_date, "calibration_sample_count_mismatch",
                f"Calibration counts differ for {region} in week {week}: {vendor_a_id}={row_a['checks_sent']}, {vendor_b_id}={row_b['checks_sent']}.",
            )
        calibration_a.append(row_a)
        calibration_b.append(row_b)

    sample_size = sum(int(row["checks_sent"]) for row in calibration_a)
    if sample_size == 0:
        return _unavailable(customer, from_date, to_date, "zero_calibration_sample", "The matched calibration sample contains zero entrants.")
    matches_a = sum(int(row["matched"]) for row in calibration_a)
    matches_b = sum(int(row["matched"]) for row in calibration_b)
    timeouts_a = sum(int(row["timeout"]) for row in calibration_a)
    timeouts_b = sum(int(row["timeout"]) for row in calibration_b)
    price_a = Decimal(vendor_a["cost_per_check_usd"])
    price_b = Decimal(vendor_b["cost_per_check_usd"])

    cost_ab = Decimal(sample_size) * price_a + Decimal(sample_size - matches_a) * price_b
    cost_ba = Decimal(sample_size) * price_b + Decimal(sample_size - matches_b) * price_a
    savings = cost_ab - cost_ba
    per_entrant = savings / Decimal(sample_size)
    projection = None if projected_eligible_entrants is None else Decimal(projected_eligible_entrants) * per_entrant
    if savings > 0:
        direction = f"Switching {vendor_a_id} → {vendor_b_id} to {vendor_b_id} → {vendor_a_id} lowers modeled pair cost."
    elif savings < 0:
        direction = f"Switching {vendor_a_id} → {vendor_b_id} to {vendor_b_id} → {vendor_a_id} increases modeled pair cost."
    else:
        direction = "The two modeled orders have equal pair cost."

    def vendor_summary(row: sqlite3.Row, matches: int, timeouts: int, price: Decimal) -> dict[str, Any]:
        return {
            "vendor_id": row["vendor_id"],
            "vendor_name": row["vendor_name"],
            "unit_price_usd": _money(price),
            "checks_sent": sample_size,
            "matched": matches,
            "match_rate": _rate(matches, sample_size),
            "timeout": timeouts,
            "timeout_rate": _rate(timeouts, sample_size),
        }

    vendors = [vendor_summary(vendor_a, matches_a, timeouts_a, price_a), vendor_summary(vendor_b, matches_b, timeouts_b, price_b)]
    weekly: list[dict[str, Any]] = []
    if include_weekly_consistency:
        for region, week in required_periods:
            week_result = compare_first_two_registries(
                database_path, customer, week, week, None, Decimal("0"), include_weekly_consistency=False
            )
            if week_result["status"] == "available":
                week_saving = Decimal(week_result["model"]["per_entrant_savings_usd"])
                weekly.append({
                    "classification": "Modeled",
                    "region": region,
                    "week_starting": week,
                    "savings_per_entrant_usd": _decimal_string(week_saving, COST_QUANTUM),
                    "result": "Potential saving" if week_saving > 0 else "Higher cost" if week_saving < 0 else "No difference",
                })
    cheaper_weeks = sum(1 for item in weekly if Decimal(item["savings_per_entrant_usd"]) > 0)
    projected = Decimal(projected_eligible_entrants or 0) * per_entrant
    after_implementation = projected - implementation_cost
    break_even = (
        int((implementation_cost / per_entrant).to_integral_value(rounding=ROUND_CEILING))
        if per_entrant > 0 else None
    )
    pilot_economics = {
        "classification": "Modeled",
        "eligible_verification_volume": projected_eligible_entrants,
        "implementation_cost_usd": _money(implementation_cost),
        "vendor_savings_per_1000_eligible_entrants_usd": _money(per_entrant * Decimal("1000")),
        "pair_cost_reduction_pct": (
            None if cost_ab == 0 else _decimal_string(Decimal("100") * savings / cost_ab, PERCENT_QUANTUM)
        ),
        "projected_vendor_savings_usd": None if projected_eligible_entrants is None else _money(projected),
        "savings_after_implementation_usd": None if projected_eligible_entrants is None else _money(after_implementation),
        "break_even_eligible_volume": break_even,
        "break_even_note": None if break_even is not None else "Implementation cost cannot be recovered through this change’s vendor savings.",
        "result": "Potential saving" if per_entrant > 0 else "Higher cost" if per_entrant < 0 else "No difference",
        "beneficiary_label": "Potential GeoComply contribution benefit if customer revenue is unchanged and vendor costs fall.",
        "recorded_period": {
            "classification": "Observed",
            "eligible_verification_volume": recorded_period_volume,
            "scope": {"from": from_date, "to": to_date},
            "coverage": {"comparable_period_count": len(required_periods), "first_registry_row_count": len(first_registry_rows)},
            "assumption": "Checks are treated as eligible entrants only because this supplied case has one first-registry check per eligible session. Production retries would invalidate this assumption.",
        },
    }
    decision_summary = _decision_summary(
        vendors[0], vendors[1], per_entrant, cheaper_weeks, len(weekly),
        None if projected_eligible_entrants is None else projected,
        None if projected_eligible_entrants is None else after_implementation,
        implementation_cost,
    )
    return {
        "classification": "Modeled",
        "status": "available",
        "customer": customer,
        "scope": {"from": from_date, "to": to_date},
        "calibration": {
            "classification": "Observed",
            "sample_size": sample_size,
            "matched_within_customer_region_week": True,
            "common_membership_assumption": "Equal counts are necessary but do not independently prove identical people were sent to both registries.",
            "vendors": vendors,
        },
        "current_order": [_step(vendor_a), _step(vendor_b)],
        "alternative_order": [_step(vendor_b), _step(vendor_a)],
        "preserved_configuration": {
            "mandatory_screening": [_step(row) for row in current_sequence[:first_position]],
            "remaining_configured_steps": [_step(row) for row in current_sequence[second_position + 1:]],
        },
        "model": {
            "classification": "Modeled",
            "current_order_cost_for_calibration_usd": _money(cost_ab),
            "alternative_order_cost_for_calibration_usd": _money(cost_ba),
            "current_order_cost_per_entrant_usd": _decimal_string(cost_ab / Decimal(sample_size), COST_QUANTUM),
            "alternative_order_cost_per_entrant_usd": _decimal_string(cost_ba / Decimal(sample_size), COST_QUANTUM),
            "current_order_cost_per_1000_eligible_entrants_usd": _money(cost_ab / Decimal(sample_size) * Decimal("1000")),
            "alternative_order_cost_per_1000_eligible_entrants_usd": _money(cost_ba / Decimal(sample_size) * Decimal("1000")),
            "pair_cost_reduction_pct": None if cost_ab == 0 else _decimal_string(Decimal("100") * savings / cost_ab, PERCENT_QUANTUM),
            "savings_from_switch_for_calibration_usd": _money(savings),
            "per_entrant_savings_usd": _decimal_string(per_entrant, COST_QUANTUM),
            "projected_eligible_entrants": projected_eligible_entrants,
            "estimated_future_savings_usd": None if projection is None else _money(projection),
            "interpretation": direction,
            "explanation": _explanation(vendors[0], vendors[1], savings),
            "formula": "C_AB = N*c_A + (N-M_A)*c_B; C_BA = N*c_B + (N-M_B)*c_A",
        },
        "weekly_consistency": {
            "classification": "Modeled descriptive consistency",
            "weeks": weekly,
            "comparable_week_count": len(weekly),
            "proposed_order_cheaper_week_count": cheaper_weeks,
            "statement": f"The proposed order was cheaper in {cheaper_weeks} of {len(weekly)} comparable weeks." if weekly else "No comparable weekly calculations are available.",
        },
        "pilot_economics": pilot_economics,
        "decision_summary": decision_summary,
        "price_threshold": price_threshold(vendor_a["vendor_name"], vendor_b["vendor_name"],
                                           matches_a, matches_b, price_a, price_b),
        "assumptions": ASSUMPTIONS,
    }
