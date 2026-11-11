"""Observed customer-economics calculations for Phase 2.

The functions in this module read each source grain independently. In
particular, vendor costs are never SQL-joined to verification funnel rows,
which prevents a multi-vendor weekly sequence from multiplying funnel counts.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from .database import connect, initialize


RATE_QUANTUM = Decimal("0.0001")
COST_QUANTUM = Decimal("0.0001")


class BaselineInputError(ValueError):
    """Raised when a requested customer/date scope is not meaningful."""


FUNNEL_FIELDS = (
    "session_started",
    "data_submitted",
    "passed_on_data",
    "stepped_up_to_doc",
    "doc_uploaded",
    "doc_passed",
    "verification_passed",
    "manual_review_queued",
)


def _decimal_string(value: Decimal, quantum: Decimal) -> str:
    return str(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _money(value: Decimal) -> str:
    return _decimal_string(value, Decimal("0.01"))


def _rate(numerator: int, denominator: int) -> dict[str, int | str | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": None if denominator == 0 else _decimal_string(Decimal(numerator) / Decimal(denominator), RATE_QUANTUM),
    }


def _metrics(verification_rows: Iterable[sqlite3.Row], vendor_rows: Iterable[sqlite3.Row]) -> dict[str, Any]:
    verification_rows = list(verification_rows)
    vendor_rows = list(vendor_rows)
    totals = {field: sum(int(row[field]) for row in verification_rows) for field in FUNNEL_FIELDS}
    has_vendor_spend = bool(vendor_rows)
    spend = sum((Decimal(row["cost_usd"]) for row in vendor_rows), Decimal("0"))

    return {
        "classification": "Observed",
        "funnel_counts": totals,
        "vendor_spend_usd": _money(spend) if has_vendor_spend else None,
        "cost_per_started_session_usd": (
            _decimal_string(spend / Decimal(totals["session_started"]), COST_QUANTUM)
            if has_vendor_spend and totals["session_started"] else None
        ),
        "cost_per_completed_verification_usd": (
            _decimal_string(spend / Decimal(totals["verification_passed"]), COST_QUANTUM)
            if has_vendor_spend and totals["verification_passed"] else None
        ),
        "funnel_rates": {
            "data_submission": _rate(totals["data_submitted"], totals["session_started"]),
            "pass_on_submitted_data": _rate(totals["passed_on_data"], totals["data_submitted"]),
            "document_step_up": _rate(totals["stepped_up_to_doc"], totals["data_submitted"]),
            "document_completion": _rate(totals["doc_uploaded"], totals["stepped_up_to_doc"]),
            "document_pass": _rate(totals["doc_passed"], totals["doc_uploaded"]),
            "weighted_verification_pass": _rate(totals["verification_passed"], totals["session_started"]),
            "manual_review_queued": _rate(totals["manual_review_queued"], totals["session_started"]),
        },
    }


def _contiguous_ranges(weeks: list[str]) -> list[dict[str, str]]:
    """Return weekly date ranges without hiding a configuration change-and-revert."""
    if not weeks:
        return []
    ordered = sorted(weeks)
    ranges: list[dict[str, str]] = []
    start = end = date.fromisoformat(ordered[0])
    for value in ordered[1:]:
        current = date.fromisoformat(value)
        if current == end + timedelta(days=7):
            end = current
            continue
        ranges.append({"from": start.isoformat(), "to": end.isoformat()})
        start = end = current
    ranges.append({"from": start.isoformat(), "to": end.isoformat()})
    return ranges


def list_customers(database_path: Path) -> list[dict[str, str]]:
    connection = connect(database_path)
    try:
        initialize(connection)
        rows = connection.execute(
            """
            SELECT customer, MIN(week_starting) AS available_from, MAX(week_starting) AS available_to
            FROM verification_weekly
            GROUP BY customer
            ORDER BY customer
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_baseline(database_path: Path, customer: str, from_date: str, to_date: str) -> dict[str, Any]:
    """Calculate an observed baseline for one explicit customer/date scope."""
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError as error:
        raise BaselineInputError("Dates must use ISO format YYYY-MM-DD.") from error
    if start > end:
        raise BaselineInputError("from must be on or before to.")

    connection = connect(database_path)
    try:
        initialize(connection)
        known_customer = connection.execute(
            "SELECT 1 FROM verification_weekly WHERE customer = ? LIMIT 1", (customer,)
        ).fetchone()
        if known_customer is None:
            raise BaselineInputError(f"Customer {customer!r} is not available in the observed verification funnel.")

        verification_rows = connection.execute(
            """
            SELECT * FROM verification_weekly
            WHERE customer = ? AND week_starting >= ? AND week_starting <= ?
            ORDER BY region, week_starting
            """,
            (customer, from_date, to_date),
        ).fetchall()
        if not verification_rows:
            return {
                "classification": "Observed",
                "status": "not_available",
                "reason": "No observed verification funnel rows exist for this customer and date range.",
                "customer": customer,
                "scope": {"from": from_date, "to": to_date},
                "configurations": [],
            }

        vendor_rows = connection.execute(
            """
            SELECT * FROM vendor_weekly_with_vendor
            WHERE customer = ? AND cohort = 'waterfall' AND week_starting >= ? AND week_starting <= ?
            ORDER BY region, week_starting, step_no, vendor_id
            """,
            (customer, from_date, to_date),
        ).fetchall()
    finally:
        connection.close()

    vendors_by_week: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in vendor_rows:
        vendors_by_week[(row["region"], row["week_starting"])].append(row)

    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in verification_rows:
        week_key = (row["region"], row["week_starting"])
        steps = vendors_by_week.get(week_key, [])
        step_data = [
            {
                "step_no": vendor["step_no"],
                "vendor_id": vendor["vendor_id"],
                "vendor_name": vendor["vendor_name"],
                "check_type": vendor["check_type"],
            }
            for vendor in steps
        ]
        signature = (row["region"], tuple((step["step_no"], step["vendor_id"]) for step in step_data))
        group = groups.setdefault(signature, {
            "region": row["region"], "steps": step_data, "weeks": [], "verification_rows": [], "vendor_rows": [],
        })
        group["weeks"].append(row["week_starting"])
        group["verification_rows"].append(row)
        group["vendor_rows"].extend(steps)

    configurations: list[dict[str, Any]] = []
    for index, group in enumerate(sorted(groups.values(), key=lambda item: min(item["weeks"])), start=1):
        # `vendor_rows` above contains sqlite rows; no aggregation was performed through a join.
        configuration_metrics = _metrics(group["verification_rows"], group["vendor_rows"])
        configuration_is_complete = bool(group["steps"])
        configurations.append({
            "id": f"configuration-{index}",
            "classification": "Observed",
            "status": "available" if configuration_is_complete else "not_available",
            "reason": None if configuration_is_complete else "No recorded waterfall rows exist for this verification-funnel period.",
            "region": group["region"],
            "week_count": len(group["weeks"]),
            "date_ranges": _contiguous_ranges(group["weeks"]),
            "recorded_sequence": group["steps"],
            "metrics": configuration_metrics,
        })

    summary = _metrics(verification_rows, vendor_rows)
    mixed = len(configurations) > 1
    return {
        "classification": "Observed",
        "status": "available",
        "customer": customer,
        "scope": {"from": from_date, "to": to_date},
        "summary": {
            "classification": "Observed",
            "configuration_count": len(configurations),
            "is_mixed_configuration": mixed,
            "interpretation": (
                "Observed aggregate across multiple recorded configurations; use the configuration sections below for route-specific economics."
                if mixed else "Observed economics for one recorded configuration."
            ),
            "metrics": summary,
        },
        "configurations": configurations,
    }
