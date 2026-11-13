"""Scoped first-vendor ranking for an IDComply pilot.

This deliberately evaluates only the first identity-registry lookup.  It does
not reuse or alter the two-vendor waterfall-cost model.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .database import connect, initialize


SCORE_QUANTUM = Decimal("0.000001")


class RecommendationInputError(ValueError):
    """Raised for invalid recommendation inputs."""


def _decimal(value: int | str | Decimal) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:  # pragma: no cover - Decimal's text varies by Python version.
        raise RecommendationInputError("Priority values must be numeric.") from error
    if result < 0:
        raise RecommendationInputError("Priority values cannot be negative.")
    return result


def _rate(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator)


def _metric_score(value: Decimal, low: Decimal, high: Decimal, higher_is_better: bool) -> Decimal:
    if high == low:
        return Decimal("1")
    return ((value - low) if higher_is_better else (high - value)) / (high - low)


def _number(value: Decimal) -> str:
    return str(value.quantize(SCORE_QUANTUM))


def recommend_first_vendor(
    database_path: Path,
    customer: str,
    from_date: str,
    to_date: str,
    cost_priority: int | str | Decimal = 3,
    match_priority: int | str | Decimal = 3,
    timeout_priority: int | str | Decimal = 3,
) -> dict[str, Any]:
    """Rank scoped registry vendors using aggregate calibration counts.

    Counts are intentionally added before rates are calculated; weekly rates
    are never averaged.  Scores and ties retain Decimal precision until the
    final response is serialized.
    """
    weights = {
        "cost": _decimal(cost_priority),
        "match": _decimal(match_priority),
        "timeout": _decimal(timeout_priority),
    }
    total_weight = sum(weights.values(), Decimal("0"))
    scope = {"from": from_date, "to": to_date}

    connection = connect(database_path)
    try:
        initialize(connection)
        scope_regions = [
            row["region"] for row in connection.execute(
                """SELECT DISTINCT region FROM verification_weekly
                   WHERE customer = ? AND week_starting >= ? AND week_starting <= ?
                   ORDER BY region""",
                (customer, from_date, to_date),
            ).fetchall()
        ]
        vendor_rows = connection.execute(
            """SELECT vendor_id, vendor_name, regions_covered_json, cost_per_check_usd
               FROM vendors WHERE check_type = 'identity_registry' ORDER BY vendor_name, vendor_id"""
        ).fetchall()
        calibration_rows = connection.execute(
            """SELECT vendor_id, region, checks_sent, matched, timeout
               FROM vendor_weekly
               WHERE customer = ? AND cohort = 'calibration'
                 AND week_starting >= ? AND week_starting <= ?
               ORDER BY vendor_id""",
            (customer, from_date, to_date),
        ).fetchall()
        waterfall_rows = connection.execute(
            """SELECT vw.week_starting, vw.region, vw.step_no, vw.vendor_id, v.check_type
               FROM vendor_weekly AS vw INNER JOIN vendors AS v ON v.vendor_id = vw.vendor_id
               WHERE vw.customer = ? AND vw.cohort = 'waterfall'
                 AND vw.week_starting >= ? AND vw.week_starting <= ?
               ORDER BY vw.week_starting, vw.region, vw.step_no""",
            (customer, from_date, to_date),
        ).fetchall()
    finally:
        connection.close()

    base = {
        "classification": "Observed",
        "customer": customer,
        "scope": scope,
        "regions": scope_regions,
        "priorities": {key: str(value) for key, value in weights.items()},
        "normalized_priorities": (
            {key: _number(value / total_weight) for key, value in weights.items()}
            if total_weight else {key: "0.000000" for key in weights}
        ),
        "current_first_vendor": None,
        "eligible_vendors": [],
        "unavailable_options": [],
        "note": "This score reflects your priorities across the available vendors. It is not a predicted verification rate or savings estimate.",
    }
    if not scope_regions:
        return {**base, "status": "unavailable", "reason": "No observed customer-region evidence exists for this date range."}

    required_regions = set(scope_regions)
    catalogue: dict[str, Any] = {}
    unavailable: list[dict[str, str]] = []
    for row in vendor_rows:
        covered = set(json.loads(row["regions_covered_json"]))
        if required_regions.issubset(covered):
            catalogue[row["vendor_id"]] = row
        else:
            unavailable.append({
                "vendor_id": row["vendor_id"], "vendor_name": row["vendor_name"],
                "reason_code": "insufficient_region_coverage",
                "reason": "Does not cover every customer region in the selected scope.",
            })

    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"checks_sent": 0, "matched": 0, "timeout": 0})
    for row in calibration_rows:
        if row["vendor_id"] == "ANY_REGISTRY":
            unavailable.append({
                "vendor_id": "ANY_REGISTRY", "vendor_name": "ANY_REGISTRY",
                "reason_code": "aggregate_rollup",
                "reason": "Aggregate calibration roll-up; it is not a callable first-step vendor.",
            })
            continue
        if row["vendor_id"] in catalogue and row["region"] in required_regions:
            aggregate = totals[row["vendor_id"]]
            aggregate["checks_sent"] += int(row["checks_sent"])
            aggregate["matched"] += int(row["matched"])
            aggregate["timeout"] += int(row["timeout"])

    candidates: list[dict[str, Any]] = []
    for vendor_id, vendor in catalogue.items():
        aggregate = totals.get(vendor_id)
        if aggregate is None:
            unavailable.append({
                "vendor_id": vendor_id, "vendor_name": vendor["vendor_name"],
                "reason_code": "missing_calibration_evidence",
                "reason": "No calibration evidence exists for this customer, region, and date range.",
            })
            continue
        if aggregate["checks_sent"] == 0:
            unavailable.append({
                "vendor_id": vendor_id, "vendor_name": vendor["vendor_name"],
                "reason_code": "zero_calibration_sample",
                "reason": "Calibration evidence has zero checks sent in this scope.",
            })
            continue
        candidates.append({
            "vendor_id": vendor_id,
            "vendor_name": vendor["vendor_name"],
            "price_per_check_usd": Decimal(vendor["cost_per_check_usd"]),
            **aggregate,
            "match_rate": _rate(aggregate["matched"], aggregate["checks_sent"]),
            "timeout_rate": _rate(aggregate["timeout"], aggregate["checks_sent"]),
        })

    first_registry_ids: set[str] = set()
    by_period: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in waterfall_rows:
        by_period[(row["region"], row["week_starting"])].append(row)
    for rows in by_period.values():
        first = next((row for row in rows if row["check_type"] == "identity_registry"), None)
        if first:
            first_registry_ids.add(first["vendor_id"])
    if len(first_registry_ids) == 1:
        current_id = next(iter(first_registry_ids))
        current = next((candidate for candidate in candidates if candidate["vendor_id"] == current_id), None)
        base["current_first_vendor"] = current_id if current is None else current_id

    if not candidates:
        return {**base, "status": "unavailable", "unavailable_options": unavailable, "reason": "No identity-registry vendor has usable calibration evidence for this scope."}

    prices = [item["price_per_check_usd"] for item in candidates]
    matches = [item["match_rate"] for item in candidates]
    timeouts = [item["timeout_rate"] for item in candidates]
    for candidate in candidates:
        metric_scores = {
            "cost": _metric_score(candidate["price_per_check_usd"], min(prices), max(prices), False),
            "match": _metric_score(candidate["match_rate"], min(matches), max(matches), True),
            "timeout": _metric_score(candidate["timeout_rate"], min(timeouts), max(timeouts), False),
        }
        contributions = {
            key: (Decimal("0") if total_weight == 0 else weights[key] / total_weight * metric_scores[key])
            for key in weights
        }
        score = sum(contributions.values(), Decimal("0")) * Decimal("100")
        candidate["metric_scores"] = {key: _number(value) for key, value in metric_scores.items()}
        candidate["score_contributions"] = {key: _number(value * Decimal("100")) for key, value in contributions.items()}
        candidate["score"] = _number(score)
        candidate["_raw_score"] = score
        candidate["price_per_check_usd"] = f"{candidate['price_per_check_usd']:.2f}"
        candidate["match_rate"] = _number(candidate["match_rate"])
        candidate["timeout_rate"] = _number(candidate["timeout_rate"])

    ranked = sorted(candidates, key=lambda item: item["_raw_score"], reverse=True)
    top_score = ranked[0]["_raw_score"]
    recommendations = [item["vendor_id"] for item in ranked if item["_raw_score"] == top_score] if total_weight else []
    for candidate in ranked:
        del candidate["_raw_score"]
    return {
        **base,
        "status": "available",
        "eligible_vendors": ranked,
        "unavailable_options": unavailable,
        "recommendation": {
            "vendor_ids": recommendations,
            "joint": len(recommendations) > 1,
            "reason": "Choose at least one priority." if total_weight == 0 else None,
        },
        "how_it_works": {
            "formula": "Score = 100 × (normalized cost priority × cost score + normalized match priority × match score + normalized timeout priority × timeout score).",
            "normalization": "Each metric is normalized only against vendors available in this customer and date range. Equal metric values receive a score of 1.",
        },
    }
