"""Saved scenarios and immutable Phase 5 proposal exports."""
from __future__ import annotations

import html
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .baseline import BaselineInputError, get_baseline
from .comparison import ComparisonInputError, compare_first_two_registries
from .database import connect, initialize


MODEL_VERSION = "0.5.0"


class ProposalInputError(ValueError):
    """Raised for invalid saved-scenario/proposal requests."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_scope(customer: str, from_date: str, to_date: str) -> None:
    if not customer.strip():
        raise ProposalInputError("customer is required.")
    try:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    except ValueError as error:
        raise ProposalInputError("Scenario dates must use ISO format YYYY-MM-DD.") from error
    if start > end:
        raise ProposalInputError("Scenario from date must be on or before its to date.")


def save_scenario(
    database_path: Path,
    name: str,
    scenario_type: str,
    customer: str,
    from_date: str,
    to_date: str,
    payload: dict[str, Any],
    scenario_id: str | None = None,
) -> dict[str, Any]:
    _validate_scope(customer, from_date, to_date)
    if not name.strip():
        raise ProposalInputError("Scenario name is required.")
    if not scenario_type.strip():
        raise ProposalInputError("scenario_type is required.")
    timestamp = _timestamp()
    identifier = scenario_id or str(uuid.uuid4())
    connection = connect(database_path)
    try:
        initialize(connection)
        with connection:
            connection.execute(
                """
                INSERT INTO scenarios(id, name, scenario_type, customer, scope_from, scope_to, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, scenario_type=excluded.scenario_type, customer=excluded.customer,
                  scope_from=excluded.scope_from, scope_to=excluded.scope_to, payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (identifier, name.strip(), scenario_type.strip(), customer, from_date, to_date, json.dumps(payload, sort_keys=True), timestamp, timestamp),
            )
        return get_scenario(database_path, identifier)
    finally:
        connection.close()


def get_scenario(database_path: Path, scenario_id: str) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        initialize(connection)
        row = connection.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ProposalInputError(f"Saved scenario {scenario_id!r} was not found.")
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def create_proposal(database_path: Path, scenario_id: str) -> dict[str, Any]:
    scenario = get_scenario(database_path, scenario_id)
    projected_volume = scenario["payload"].get("projected_eligible_entrants")
    if not isinstance(projected_volume, int) or projected_volume < 0:
        raise ProposalInputError("Saved scenario needs a nonnegative integer projected_eligible_entrants to create a proposal.")
    implementation_cost = scenario["payload"].get("implementation_cost_usd", 0)
    volume_mode = scenario["payload"].get("volume_mode", "custom")
    if volume_mode not in {"recorded", "custom"}:
        raise ProposalInputError("volume_mode must be either recorded or custom.")
    entered_notes = scenario["payload"].get("implementation_notes", [])
    if not isinstance(entered_notes, list) or not all(isinstance(note, str) for note in entered_notes):
        raise ProposalInputError("implementation_notes must be a list of text notes.")
    try:
        baseline = get_baseline(database_path, scenario["customer"], scenario["scope_from"], scenario["scope_to"])
        comparison = compare_first_two_registries(
            database_path, scenario["customer"], scenario["scope_from"], scenario["scope_to"], projected_volume, implementation_cost
        )
    except (BaselineInputError, ComparisonInputError) as error:
        raise ProposalInputError(str(error)) from error
    if baseline["status"] != "available":
        raise ProposalInputError("A proposal cannot be created because the saved scope has no observed baseline.")

    connection = connect(database_path)
    try:
        initialize(connection)
        source_rows = connection.execute(
            "SELECT filename, sha256, imported_at FROM source_files ORDER BY filename"
        ).fetchall()
    finally:
        connection.close()
    comparison_available = comparison["status"] == "available"
    proposed_change = (
        {
            "classification": "Modeled",
            "change": "Swap the first two recorded identity registries for a controlled test.",
            "from_order": [step["vendor_id"] for step in comparison["current_order"]],
            "to_order": [step["vendor_id"] for step in comparison["alternative_order"]],
        }
        if comparison_available else {
            "classification": "Not available",
            "change": "No swap is proposed because the selected scope does not support a valid comparison.",
            "reason": comparison["reason"],
        }
    )
    proposal = {
        "proposal_version": MODEL_VERSION,
        "generated_at": _timestamp(),
        "scenario": scenario,
        "sources": [dict(row) for row in source_rows],
        "baseline": baseline,
        "comparison": comparison,
        "proposed_change": proposed_change,
        "assumptions": {
            "classification": "User assumption",
            "projected_eligible_entrants": projected_volume,
            "volume_mode": volume_mode,
            # Retain the entered pilot cost even when the observed evidence cannot
            # support a pair comparison and therefore has no pilot_economics block.
            "implementation_cost_usd": f"{Decimal(str(implementation_cost)):.2f}",
            "comparison_assumptions": comparison.get("assumptions", []),
        },
        "unknowns": [
            "Calibration equal counts do not prove common membership.",
            "Order-equivalence, timeout behavior, retries, abandonment, fraud outcomes, billing terms, and implementation effort require validation.",
            "The model does not establish production conversion or fraud equivalence.",
        ],
        "pilot_success_conditions": [
            "Lower cost per eligible entrant than the recorded order, with the comparison scope and pricing reconfirmed.",
            "Final verification rate and document step-up remain within pre-agreed conversion guardrails.",
            "Timeout and completion-time guardrails are met, with no unacceptable delayed-fraud signal.",
            "Randomization, sample size, stopping criteria, owners, and rollback criteria are agreed before launch.",
        ],
        "before_implementation": {
            "classification": "To agree with owner",
            "checklist": [
                "Confirm billing, retry charges, commitments, and cached transactions.",
                "Confirm the eligible population and routing constraints with the owner.",
                "Determine whether this can be a configuration change within the existing customer integration.",
                "Agree completion and latency guardrails, experiment assignment, and rollback ownership.",
                "Assess fraud outcomes when enough follow-up time is available.",
            ],
            "editable_notes": entered_notes,
        },
        "product_rationale": "The QBR emphasizes measurable value, implementation effort and reuse of customer work. This workbench applies those principles to vendor-cost decisions using the IDComply data pack.",
    }
    proposal_id = str(uuid.uuid4())
    connection = connect(database_path)
    try:
        initialize(connection)
        with connection:
            connection.execute(
                "INSERT INTO proposal_runs(id, scenario_id, model_version, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (proposal_id, scenario_id, MODEL_VERSION, json.dumps(proposal, sort_keys=True), proposal["generated_at"]),
            )
    finally:
        connection.close()
    return {"id": proposal_id, "created_at": proposal["generated_at"], "model_version": MODEL_VERSION}


def get_proposal(database_path: Path, proposal_id: str) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        initialize(connection)
        row = connection.execute("SELECT payload_json FROM proposal_runs WHERE id = ?", (proposal_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ProposalInputError(f"Proposal {proposal_id!r} was not found.")
    return json.loads(row["payload_json"])


def _vendor_metrics_table(vendors: list[dict[str, Any]]) -> str:
    """Return the observed calibration evidence as a print-safe table."""
    def value(item: dict[str, Any], key: str, suffix: str = "") -> str:
        raw = item.get(key)
        if raw is None:
            return "Not available"
        if key in {"match_rate", "timeout_rate"}:
            return f"{Decimal(str(raw)) * Decimal('100'):.1f}%"
        return f"{html.escape(str(raw))}{suffix}"

    headers = "".join(
        f"<th>{html.escape(vendor['vendor_name'])}<small>{html.escape(vendor['vendor_id'])}</small></th>"
        for vendor in vendors
    )
    rows = (
        ("Listed cost per check", "unit_price_usd", " USD"),
        ("Calibration match rate", "match_rate", ""),
        ("Calibration timeout rate", "timeout_rate", ""),
        ("Calibration checks", "checks_sent", ""),
    )
    body = "".join(
        f"<tr><th>{label}</th>{''.join(f'<td>{value(vendor, key, suffix)}</td>' for vendor in vendors)}</tr>"
        for label, key, suffix in rows
    )
    return f"<table><thead><tr><th>Observed metric</th>{headers}</tr></thead><tbody>{body}</tbody></table>"


def _weekly_chart_svg(weeks: list[dict[str, Any]]) -> str:
    """Render the frozen weekly comparison without relying on browser JS or CSS charts."""
    if not weeks:
        return "<p>No comparable weekly calculations are available.</p>"
    values = [Decimal(str(week["savings_per_entrant_usd"])) * Decimal("1000") for week in weeks]
    maximum = max(max(abs(value) for value in values), Decimal("1"))
    width = max(760, 46 + len(weeks) * 28)
    height, zero_y, usable = 230, 106, 76
    bars: list[str] = []
    labels: list[str] = []
    for index, (week, value) in enumerate(zip(weeks, values)):
        x = 42 + index * 28
        bar_height = float(abs(value) / maximum * usable)
        y = zero_y - bar_height if value >= 0 else zero_y
        colour = "#22875d" if value >= 0 else "#bd5c40"
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="14" height="{bar_height:.1f}" rx="2" fill="{colour}"/>')
        labels.append(f'<text x="{x + 7:.1f}" y="205" text-anchor="end" transform="rotate(-55 {x + 7:.1f} 205)">{html.escape(week["week_starting"][5:])}</text>')
    return (
        f'<svg class="weekly-chart" viewBox="0 0 {width} {height}" role="img" aria-label="Weekly modeled savings per 1,000 eligible entrants">'
        '<text x="10" y="17" font-size="11">Modeled savings per 1,000 eligible entrants (USD)</text>'
        f'<line x1="42" x2="{width - 8}" y1="{zero_y}" y2="{zero_y}" stroke="#64756d" stroke-width="1"/>'
        f'<text x="12" y="{zero_y + 4}" font-size="10">0</text>{"".join(bars)}{"".join(labels)}</svg>'
    )


def printable_proposal_html(proposal: dict[str, Any]) -> str:
    """Render an offline, complete, print-safe representation of a frozen proposal."""
    scenario = proposal["scenario"]
    comparison = proposal["comparison"]
    if comparison["status"] == "available":
        model = comparison["model"]
        economics_data = comparison["pilot_economics"]
        weekly = comparison["weekly_consistency"]
        current_pair = " → ".join(f"{step['vendor_name']} ({step['vendor_id']})" for step in comparison["current_order"])
        proposed_pair = " → ".join(f"{step['vendor_name']} ({step['vendor_id']})" for step in comparison["alternative_order"])
        decision = comparison["decision_summary"]
        pair_cost_reduction = model.get("pair_cost_reduction_pct")
        pair_cost_reduction_display = "Not available" if pair_cost_reduction is None else f"{pair_cost_reduction}%"
        comparison_section = f"""
<section class="keep"><h2>Configuration comparison <span class="tag">Modeled</span></h2>
<p><b>Current:</b> {html.escape(current_pair)}<br><b>Proposed:</b> {html.escape(proposed_pair)}</p>
<p><b>Observed calibration sample:</b> {comparison['calibration']['sample_size']:,} comparable calibration entrants, grouped by customer, region, and week.</p>
<h3>Vendor comparison <span class="tag">Observed</span></h3>
{_vendor_metrics_table(comparison['calibration']['vendors'])}
<div class="economics"><p><b>Current pair cost / 1,000:</b> {html.escape(model['current_order_cost_per_1000_eligible_entrants_usd'])} USD</p>
<p><b>Proposed pair cost / 1,000:</b> {html.escape(model['alternative_order_cost_per_1000_eligible_entrants_usd'])} USD</p>
<p><b>Savings or additional cost / 1,000:</b> {html.escape(economics_data['vendor_savings_per_1000_eligible_entrants_usd'])} USD</p></div>
<p><b>Pair-cost reduction:</b> {html.escape(pair_cost_reduction_display)} of the compared pair cost only. Screening, document, and other downstream costs are outside this percentage.</p>
<h3>Weekly consistency <span class="tag">Modeled</span></h3><p>{html.escape(weekly['statement'])} This is descriptive consistency, not statistical confidence.</p>
{_weekly_chart_svg(weekly['weeks'])}
</section>
<section class="card keep"><h2>Decision summary <span class="tag">Modeled</span></h2><p><b>{html.escape(decision['headline'])}</b></p><p>{html.escape(decision['detail'])}</p><p>{html.escape(decision['limitation'])}</p></section>
<section class="keep"><h2>Pilot economics <span class="tag">Assumed inputs + modeled result</span></h2><ul>
<li>Eligible verification volume: {html.escape(str(proposal['assumptions']['projected_eligible_entrants']))} ({html.escape(str(proposal['assumptions'].get('volume_mode', 'custom')))} scenario)</li>
<li>One-time implementation cost: {html.escape(str(proposal['assumptions']['implementation_cost_usd']))} USD</li>
<li>Projected vendor savings: {html.escape(str(economics_data['projected_vendor_savings_usd']))} USD</li>
<li>Savings after implementation: {html.escape(str(economics_data['savings_after_implementation_usd']))} USD</li></ul>
<p>{html.escape(economics_data.get('beneficiary_label', 'Potential GeoComply contribution benefit if customer revenue is unchanged and vendor costs fall.'))}</p>
<p><b>How this is calculated:</b> {html.escape(model['formula'])}</p></section>"""
    else:
        decision = comparison["decision_summary"]
        comparison_section = f"""
<section class="card keep"><h2>Configuration comparison <span class="tag">Not available</span></h2><p>{html.escape(comparison['reason'])}</p></section>
<section class="card keep"><h2>Decision summary <span class="tag">Not available</span></h2><p><b>{html.escape(decision['headline'])}</b></p><p>{html.escape(decision['detail'])}</p><p>{html.escape(decision['limitation'])}</p></section>
<section class="keep"><h2>Pilot inputs <span class="tag">Assumed</span></h2><p>Eligible verification volume: {html.escape(str(proposal['assumptions']['projected_eligible_entrants']))}<br>One-time implementation cost: {html.escape(str(proposal['assumptions']['implementation_cost_usd']))} USD</p><p><b>Pair-cost formula:</b> Not available because the selected scope has no valid two-registry comparison.</p></section>"""
    source_items = "".join(
        f"<li>{html.escape(source['filename'])} — SHA-256 {html.escape(source['sha256'])}</li>"
        for source in proposal["sources"]
    )
    unknowns = "".join(f"<li>{html.escape(item)}</li>" for item in proposal["unknowns"])
    conditions = "".join(f"<li>{html.escape(item)}</li>" for item in proposal["pilot_success_conditions"])
    before_implementation = proposal.get("before_implementation", {})
    before_items = "".join(f"<li>{html.escape(item)}</li>" for item in before_implementation.get("checklist", []))
    note_items = "".join(f"<li>{html.escape(item)}</li>" for item in before_implementation.get("editable_notes", [])) or "<li>No owner notes entered.</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>IDComply proposal — {html.escape(scenario['name'])}</title>
<style>body{{font-family:Arial,sans-serif;color:#17251f;max-width:920px;margin:42px auto;padding:0 24px;line-height:1.45}}h1{{font-size:30px}}h2{{margin-top:32px}}h3{{margin:22px 0 6px}}.tag{{color:#176247;font-weight:bold;text-transform:uppercase;font-size:11px;letter-spacing:.08em}}.card{{background:#edf5f0;padding:18px;border-radius:10px}}.economics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.economics p{{background:#f1f6f2;padding:12px;margin:6px 0}}table{{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px}}th,td{{border:1px solid #bfcfc4;padding:8px;text-align:left;overflow-wrap:anywhere}}thead th{{background:#e8f0ea}}th small{{display:block;color:#50695b;font-weight:normal}}.weekly-chart{{display:block;width:100%;height:auto;border:1px solid #c5d3c9}}li{{margin:7px 0}}.keep{{break-inside:avoid;page-break-inside:avoid}}@media print{{body{{margin:0;max-width:none}}table{{font-size:11px}}.economics{{gap:6px}}}}</style></head>
<body><p class="tag">IDComply decision workbench · frozen proposal</p><h1>{html.escape(scenario['name'])}</h1>
<p><b>Customer:</b> {html.escape(scenario['customer'])}<br><b>Scope:</b> {html.escape(scenario['scope_from'])} to {html.escape(scenario['scope_to'])}<br><b>Model version:</b> {html.escape(proposal['proposal_version'])}<br><b>Generated:</b> {html.escape(proposal['generated_at'])}</p>
<section class="card"><h2>Proposed test</h2><p>{html.escape(proposal['proposed_change']['change'])}</p></section>
{comparison_section}
<section class="keep"><h2>Assumptions and limitations</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in proposal['assumptions']['comparison_assumptions'])}</ul></section>
<section class="keep"><h2>Before implementation <span class="tag">To agree with owner</span></h2><ul>{before_items}</ul><p><b>Owner notes:</b></p><ul>{note_items}</ul></section>
<section class="keep"><h2>Product rationale</h2><p>{html.escape(proposal.get('product_rationale', ''))}</p></section>
<h2>Unknowns to resolve</h2><ul>{unknowns}</ul><h2>Pilot success conditions</h2><ul>{conditions}</ul><h2>Evidence sources</h2><ul>{source_items}</ul></body></html>"""
