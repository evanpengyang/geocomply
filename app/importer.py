"""Reproducible Phase 1 importer for the supplied IDComply evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from .database import connect, initialize


VENDORS_FILE = "idcomply_vendors_SYNTHETIC.csv"
VENDOR_FUNNEL_FILE = "idcomply_vendor_funnel_weekly_SYNTHETIC.csv"
VERIFICATION_FUNNEL_FILE = "idcomply_verification_funnel_SYNTHETIC_v2.csv"
REQUIRED_FILES = (VENDORS_FILE, VENDOR_FUNNEL_FILE, VERIFICATION_FUNNEL_FILE)

VENDOR_COLUMNS = (
    "vendor_id", "vendor_name", "check_type", "regions_covered",
    "cost_per_check_usd", "p50_latency_ms", "notes",
)
VENDOR_FUNNEL_COLUMNS = (
    "week_starting", "customer", "region", "cohort", "step_no", "vendor_id",
    "checks_sent", "matched", "no_match", "timeout", "cost_usd",
)
VERIFICATION_FUNNEL_COLUMNS = (
    "week_starting", "customer", "region", "session_started", "data_submitted",
    "passed_on_data", "stepped_up_to_doc", "doc_uploaded", "doc_passed",
    "verification_passed", "manual_review_queued", "pass_rate_pct",
)

QBR_FACTS = (
    (
        "anticipated_signups_first_three_months", "40000", "integer",
        "Meeting Transcript: Quarterly Business Review Readout", "C3; 00:04:05",
        "Betting.com anticipated signups for the first three months; do not annualize without support.",
    ),
    (
        "tested_pass_rate_improvement_percentage_points", "9.5", "decimal",
        "Meeting Transcript: Quarterly Business Review Readout", "C3; 00:04:05",
        "Tested Alaska feature result, not a modeled waterfall reorder uplift.",
    ),
    (
        "first_year_value_per_new_user_usd", "300.00", "decimal_usd",
        "Meeting Transcript: Quarterly Business Review Readout", "C3; 00:04:05",
        "Customer-provided first-year value context; it is not profit, contribution, or lifetime value.",
    ),
    (
        "integration_consistency_objection", "true", "boolean",
        "Meeting Transcript: Quarterly Business Review Readout", "C3; 00:04:05",
        "Betting.com chose its existing setup in Alaska because engineering preferred one integration to maintain.",
    ),
)


@dataclass(frozen=True)
class Issue:
    source_file: str
    source_line: int | None
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ImportResult:
    database_path: str
    report_path: str
    imported_at: str
    source_hashes: dict[str, str]
    source_row_counts: dict[str, dict[str, int]]
    clean_row_counts: dict[str, int]
    issues: list[dict[str, Any]]


class RowValidator:
    def __init__(self, source_file: str, source_line: int, row: dict[str, str]):
        self.source_file = source_file
        self.source_line = source_line
        self.row = row
        self.issues: list[Issue] = []

    def error(self, code: str, message: str) -> None:
        self.issues.append(Issue(self.source_file, self.source_line, "error", code, message))

    def warning(self, code: str, message: str) -> None:
        self.issues.append(Issue(self.source_file, self.source_line, "warning", code, message))

    def text(self, key: str) -> str:
        value = (self.row.get(key) or "").strip()
        if not value:
            self.error("missing_value", f"{key} is required.")
        return value

    def whole_number(self, key: str, *, optional: bool = False) -> int | None:
        value = (self.row.get(key) or "").strip()
        if not value and optional:
            return None
        if not value:
            self.error("missing_value", f"{key} is required.")
            return None
        try:
            parsed = int(value)
        except ValueError:
            self.error("invalid_integer", f"{key} must be an integer, got {value!r}.")
            return None
        if parsed < 0:
            self.error("negative_value", f"{key} must be nonnegative, got {parsed}.")
        return parsed

    def decimal(self, key: str) -> Decimal | None:
        value = (self.row.get(key) or "").strip()
        if not value:
            self.error("missing_value", f"{key} is required.")
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            self.error("invalid_decimal", f"{key} must be numeric, got {value!r}.")
            return None
        if not parsed.is_finite():
            self.error("invalid_decimal", f"{key} must be finite.")
        elif parsed < 0:
            self.error("negative_value", f"{key} must be nonnegative, got {parsed}.")
        return parsed

    def iso_date(self, key: str) -> str:
        value = self.text(key)
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                self.error("invalid_date", f"{key} must be ISO YYYY-MM-DD, got {value!r}.")
        return value

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _issue_dict(issue: Issue) -> dict[str, Any]:
    return asdict(issue)


class EvidenceImporter:
    def __init__(self, source_dir: Path, database_path: Path, raw_dir: Path, supporting_sources: tuple[Path, ...] = ()):
        self.source_dir = source_dir
        self.database_path = database_path
        self.raw_dir = raw_dir
        self.supporting_sources = supporting_sources
        self.now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.issues: list[Issue] = []
        self.source_hashes: dict[str, str] = {}
        self.source_counts: dict[str, dict[str, int]] = {}
        self.raw_row_records: list[tuple[str, int, str, int, int]] = []
        self.vendor_catalogue: dict[str, dict[str, Any]] = {}
        self.known_vendor_ids: set[str] = set()

    def run(self, report_path: Path) -> ImportResult:
        missing = [name for name in REQUIRED_FILES if not (self.source_dir / name).is_file()]
        if missing:
            names = ", ".join(missing)
            raise FileNotFoundError(f"Missing required source file(s): {names}")

        connection = connect(self.database_path)
        initialize(connection)
        try:
            with connection:
                self._clear_observed_snapshot(connection)
                self._prepare_sources(connection)
                self._import_vendors(connection)
                self._import_verification_funnel(connection)
                self._import_vendor_funnel(connection)
                self._insert_qbr_facts(connection)
                self._persist_issues(connection)
                clean_counts = self._clean_counts(connection)
                self._set_metadata(connection, "last_imported_at", self.now)
                self._set_metadata(connection, "last_import_status", "completed")
        finally:
            connection.close()

        result = ImportResult(
            database_path=str(self.database_path),
            report_path=str(report_path),
            imported_at=self.now,
            source_hashes=self.source_hashes,
            source_row_counts=self.source_counts,
            clean_row_counts=clean_counts,
            issues=[_issue_dict(issue) for issue in self.issues],
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    def _clear_observed_snapshot(self, connection: sqlite3.Connection) -> None:
        # A replacement snapshot makes a repeated import idempotent and avoids stale joins.
        for table in (
            "import_issues", "raw_rows", "source_files", "vendor_weekly",
            "verification_weekly", "vendors", "qbr_facts",
        ):
            connection.execute(f"DELETE FROM {table}")

    def _prepare_sources(self, connection: sqlite3.Connection) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        for filename in REQUIRED_FILES:
            self._preserve_source(connection, filename, self.source_dir / filename)
        for source_path in self.supporting_sources:
            if not source_path.is_file():
                raise FileNotFoundError(f"Supporting source does not exist: {source_path}")
            if source_path.name in REQUIRED_FILES:
                raise ValueError(f"Supporting source name collides with a required CSV: {source_path.name}")
            self._preserve_source(connection, source_path.name, source_path)

    def _preserve_source(self, connection: sqlite3.Connection, filename: str, source_path: Path) -> None:
            source_hash = sha256_file(source_path)
            preserved_path = self.raw_dir / f"{source_hash}_{filename}"
            if not preserved_path.exists():
                shutil.copy2(source_path, preserved_path)
            self.source_hashes[filename] = source_hash
            self.source_counts.setdefault(filename, {"seen": 0, "valid": 0, "invalid": 0})
            connection.execute(
                """
                INSERT INTO source_files(
                    filename, sha256, original_path, preserved_path, imported_at,
                    rows_seen, rows_valid, rows_invalid
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0)
                """,
                (filename, source_hash, str(source_path), str(preserved_path), self.now),
            )

    def _read_rows(self, filename: str, expected_columns: tuple[str, ...], handler: Callable[[int, dict[str, str]], None]) -> None:
        path = self.source_dir / filename
        with path.open("r", newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            actual_columns = tuple(reader.fieldnames or ())
            if actual_columns != expected_columns:
                self.issues.append(Issue(
                    filename, None, "error", "invalid_schema",
                    f"Expected columns {list(expected_columns)}, got {list(actual_columns)}.",
                ))
                self.source_counts[filename] = {"seen": 0, "valid": 0, "invalid": 0}
                return
            seen = valid = invalid = 0
            for line, row in enumerate(reader, start=2):
                seen += 1
                before = len(self.issues)
                handler(line, row)
                row_issues = self.issues[before:]
                is_valid = not any(issue.severity == "error" for issue in row_issues)
                if is_valid:
                    valid += 1
                else:
                    invalid += 1
                # This insert is deferred until source_files already exists.
                self.raw_row_records.append((filename, line, json.dumps(row, sort_keys=True), int(is_valid), len(row_issues)))
            self.source_counts[filename] = {"seen": seen, "valid": valid, "invalid": invalid}

    def _import_vendors(self, connection: sqlite3.Connection) -> None:
        cleaned: list[tuple[Any, ...]] = []
        seen_ids: set[str] = set()

        def handle(line: int, row: dict[str, str]) -> None:
            validator = RowValidator(VENDORS_FILE, line, row)
            vendor_id = validator.text("vendor_id")
            vendor_name = validator.text("vendor_name")
            check_type = validator.text("check_type")
            regions = [region.strip() for region in validator.text("regions_covered").split(";") if region.strip()]
            cost = validator.decimal("cost_per_check_usd")
            latency = validator.whole_number("p50_latency_ms")
            notes = validator.text("notes")
            if vendor_id in seen_ids:
                validator.error("duplicate_key", f"vendor_id {vendor_id!r} occurs more than once.")
            else:
                seen_ids.add(vendor_id)
            self.issues.extend(validator.issues)
            if validator.valid and cost is not None and latency is not None:
                self.vendor_catalogue[vendor_id] = {
                    "regions": set(regions), "price": cost,
                }
                cleaned.append((vendor_id, vendor_name, check_type, json.dumps(regions), canonical_money(cost), latency, notes, VENDORS_FILE, line))

        self._read_rows(VENDORS_FILE, VENDOR_COLUMNS, handle)
        self.known_vendor_ids = set(self.vendor_catalogue)
        connection.executemany(
            """INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", cleaned
        )

    def _import_verification_funnel(self, connection: sqlite3.Connection) -> None:
        cleaned: list[tuple[Any, ...]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        def handle(line: int, row: dict[str, str]) -> None:
            validator = RowValidator(VERIFICATION_FUNNEL_FILE, line, row)
            week = validator.iso_date("week_starting")
            customer = validator.text("customer")
            region = validator.text("region")
            numeric = {key: validator.whole_number(key) for key in VERIFICATION_FUNNEL_COLUMNS[3:-1]}
            pass_rate = validator.decimal("pass_rate_pct")
            key = (week, customer, region)
            if key in seen_keys:
                validator.error("duplicate_key", f"Duplicate verification key {key}.")
            else:
                seen_keys.add(key)
            self._validate_funnel_relationships(validator, numeric, pass_rate)
            self.issues.extend(validator.issues)
            if validator.valid and pass_rate is not None and all(value is not None for value in numeric.values()):
                cleaned.append((week, customer, region, *numeric.values(), str(pass_rate), VERIFICATION_FUNNEL_FILE, line))

        self._read_rows(VERIFICATION_FUNNEL_FILE, VERIFICATION_FUNNEL_COLUMNS, handle)
        connection.executemany(
            """INSERT INTO verification_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", cleaned
        )

    @staticmethod
    def _validate_funnel_relationships(validator: RowValidator, numeric: dict[str, int | None], pass_rate: Decimal | None) -> None:
        def exceeds(child: str, parent: str) -> None:
            if numeric[child] is not None and numeric[parent] is not None and numeric[child] > numeric[parent]:
                validator.warning("funnel_non_monotonic", f"{child} ({numeric[child]}) exceeds {parent} ({numeric[parent]}).")

        exceeds("data_submitted", "session_started")
        exceeds("passed_on_data", "data_submitted")
        exceeds("stepped_up_to_doc", "data_submitted")
        exceeds("doc_uploaded", "stepped_up_to_doc")
        exceeds("doc_passed", "doc_uploaded")
        exceeds("verification_passed", "session_started")
        exceeds("manual_review_queued", "session_started")
        if numeric["session_started"] and pass_rate is not None:
            calculated = Decimal(numeric["verification_passed"] or 0) * Decimal("100") / Decimal(numeric["session_started"])
            if abs(calculated - pass_rate) > Decimal("0.02"):
                validator.warning("pass_rate_mismatch", "pass_rate_pct does not reconcile to verification_passed/session_started within 0.02 percentage points.")

    def _import_vendor_funnel(self, connection: sqlite3.Connection) -> None:
        cleaned: list[tuple[Any, ...]] = []
        seen_keys: set[tuple[str, str, str, str, int | None, str]] = set()

        def handle(line: int, row: dict[str, str]) -> None:
            validator = RowValidator(VENDOR_FUNNEL_FILE, line, row)
            week = validator.iso_date("week_starting")
            customer = validator.text("customer")
            region = validator.text("region")
            cohort = validator.text("cohort")
            step_no = validator.whole_number("step_no", optional=True)
            vendor_id = validator.text("vendor_id")
            checks_sent = validator.whole_number("checks_sent")
            matched = validator.whole_number("matched")
            no_match = validator.whole_number("no_match")
            timeout = validator.whole_number("timeout")
            cost = validator.decimal("cost_usd")
            if cohort not in {"waterfall", "calibration"}:
                validator.error("unsupported_cohort", f"cohort must be waterfall or calibration, got {cohort!r}.")
            if cohort == "waterfall" and step_no is None:
                validator.error("missing_step", "Waterfall records require step_no.")
            if vendor_id == "ANY_REGISTRY":
                if cohort != "calibration":
                    validator.error("rollup_not_calibration", "ANY_REGISTRY is permitted only for calibration records.")
            elif vendor_id not in self.known_vendor_ids:
                validator.error("unknown_vendor", f"vendor_id {vendor_id!r} is not in the validated vendor catalogue.")
            elif region not in self.vendor_catalogue[vendor_id]["regions"]:
                validator.error("unsupported_region", f"{vendor_id} does not cover {region} according to the vendor catalogue.")
            if None not in (checks_sent, matched, no_match, timeout) and checks_sent != matched + no_match + timeout:
                validator.error("check_count_mismatch", "checks_sent must equal matched + no_match + timeout.")
            if vendor_id in self.vendor_catalogue and checks_sent is not None and cost is not None:
                expected = Decimal(checks_sent) * self.vendor_catalogue[vendor_id]["price"]
                if abs(expected - cost) > Decimal("0.01"):
                    validator.error("cost_reconciliation_failed", f"cost_usd {cost} differs from checks_sent × listed price {expected} by more than $0.01.")
            key = (week, customer, region, cohort, step_no, vendor_id)
            if key in seen_keys:
                validator.error("duplicate_key", f"Duplicate vendor-funnel key {key}.")
            else:
                seen_keys.add(key)
            self.issues.extend(validator.issues)
            if validator.valid and None not in (checks_sent, matched, no_match, timeout, cost):
                if vendor_id == "ANY_REGISTRY":
                    # Keep the raw record and issue trail, but never make a roll-up callable/joinable.
                    return
                cleaned.append((week, customer, region, cohort, step_no, vendor_id, checks_sent, matched, no_match, timeout, canonical_money(cost), VENDOR_FUNNEL_FILE, line))

        self._read_rows(VENDOR_FUNNEL_FILE, VENDOR_FUNNEL_COLUMNS, handle)
        connection.executemany(
            """INSERT INTO vendor_weekly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", cleaned
        )

    def _insert_qbr_facts(self, connection: sqlite3.Connection) -> None:
        connection.executemany("INSERT INTO qbr_facts VALUES (?, ?, ?, ?, ?, ?)", QBR_FACTS)

    def _persist_issues(self, connection: sqlite3.Connection) -> None:
        connection.executemany(
            """INSERT INTO raw_rows(source_file, source_line, raw_json, is_valid, issue_count)
               VALUES (?, ?, ?, ?, ?)""",
            self.raw_row_records,
        )
        for filename, counts in self.source_counts.items():
            connection.execute(
                """UPDATE source_files SET rows_seen = ?, rows_valid = ?, rows_invalid = ? WHERE filename = ?""",
                (counts["seen"], counts["valid"], counts["invalid"], filename),
            )
        connection.executemany(
            """INSERT INTO import_issues(source_file, source_line, severity, code, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(issue.source_file, issue.source_line, issue.severity, issue.code, issue.message, self.now) for issue in self.issues],
        )

    @staticmethod
    def _set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", (key, value))

    @staticmethod
    def _clean_counts(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("vendors", "verification_weekly", "vendor_weekly", "qbr_facts")
        }


def run_import(
    source_dir: Path,
    database_path: Path,
    raw_dir: Path,
    report_path: Path,
    supporting_sources: tuple[Path, ...] = (),
) -> ImportResult:
    """Import one complete source snapshot and produce a portable validation report."""
    return EvidenceImporter(source_dir, database_path, raw_dir, supporting_sources).run(report_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory containing the three core CSVs.")
    parser.add_argument("--database", type=Path, default=Path("data/idcomply.sqlite"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--report", type=Path, default=Path("data/validation-report.json"))
    parser.add_argument("--schema-document", type=Path, help="Schema document preserved and hashed as supporting evidence.")
    parser.add_argument("--qbr-transcript", type=Path, help="QBR transcript preserved and hashed as supporting evidence.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supporting_sources = tuple(path for path in (args.schema_document, args.qbr_transcript) if path is not None)
    result = run_import(args.source_dir, args.database, args.raw_dir, args.report, supporting_sources)
    errors = sum(1 for issue in result.issues if issue["severity"] == "error")
    warnings = sum(1 for issue in result.issues if issue["severity"] == "warning")
    print(f"Imported {result.clean_row_counts} to {result.database_path}")
    print(f"Validation report: {result.report_path} ({errors} errors, {warnings} warnings)")
    if args.qbr_transcript:
        print(f"QBR facts encoded separately with citations; transcript preserved: {args.qbr_transcript}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
