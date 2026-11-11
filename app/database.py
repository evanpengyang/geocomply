from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "phase-5.0"


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_files (
            filename TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            original_path TEXT NOT NULL,
            preserved_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            rows_seen INTEGER NOT NULL,
            rows_valid INTEGER NOT NULL,
            rows_invalid INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS raw_rows (
            source_file TEXT NOT NULL,
            source_line INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            is_valid INTEGER NOT NULL CHECK (is_valid IN (0, 1)),
            issue_count INTEGER NOT NULL,
            PRIMARY KEY (source_file, source_line),
            FOREIGN KEY (source_file) REFERENCES source_files(filename)
        );

        CREATE TABLE IF NOT EXISTS import_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_line INTEGER,
            severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vendors (
            vendor_id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            check_type TEXT NOT NULL,
            regions_covered_json TEXT NOT NULL,
            cost_per_check_usd TEXT NOT NULL,
            p50_latency_ms INTEGER NOT NULL,
            notes TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_line INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS verification_weekly (
            week_starting TEXT NOT NULL,
            customer TEXT NOT NULL,
            region TEXT NOT NULL,
            session_started INTEGER NOT NULL,
            data_submitted INTEGER NOT NULL,
            passed_on_data INTEGER NOT NULL,
            stepped_up_to_doc INTEGER NOT NULL,
            doc_uploaded INTEGER NOT NULL,
            doc_passed INTEGER NOT NULL,
            verification_passed INTEGER NOT NULL,
            manual_review_queued INTEGER NOT NULL,
            pass_rate_pct TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_line INTEGER NOT NULL,
            PRIMARY KEY (week_starting, customer, region)
        );

        CREATE TABLE IF NOT EXISTS vendor_weekly (
            week_starting TEXT NOT NULL,
            customer TEXT NOT NULL,
            region TEXT NOT NULL,
            cohort TEXT NOT NULL CHECK (cohort IN ('waterfall', 'calibration')),
            step_no INTEGER,
            vendor_id TEXT NOT NULL,
            checks_sent INTEGER NOT NULL,
            matched INTEGER NOT NULL,
            no_match INTEGER NOT NULL,
            timeout INTEGER NOT NULL,
            cost_usd TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_line INTEGER NOT NULL,
            PRIMARY KEY (week_starting, customer, region, cohort, step_no, vendor_id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
        );

        CREATE TABLE IF NOT EXISTS qbr_facts (
            fact_key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            value_type TEXT NOT NULL,
            source_label TEXT NOT NULL,
            citation TEXT NOT NULL,
            note TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scenarios (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scenario_type TEXT NOT NULL,
            customer TEXT NOT NULL,
            scope_from TEXT NOT NULL,
            scope_to TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proposal_runs (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (scenario_id) REFERENCES scenarios(id)
        );

        CREATE INDEX IF NOT EXISTS idx_vendor_weekly_scope
            ON vendor_weekly(customer, week_starting, cohort, region);
        CREATE INDEX IF NOT EXISTS idx_import_issues_source
            ON import_issues(source_file, source_line);
        CREATE INDEX IF NOT EXISTS idx_scenarios_customer
            ON scenarios(customer, updated_at DESC);

        -- This is the only vendor-name enrichment. It is one-to-one by validated
        -- vendor_id and intentionally never joins customer funnel aggregates.
        DROP VIEW IF EXISTS vendor_weekly_with_vendor;
        CREATE VIEW vendor_weekly_with_vendor AS
            SELECT
                vw.week_starting,
                vw.customer,
                vw.region,
                vw.cohort,
                vw.step_no,
                vw.vendor_id,
                v.vendor_name,
                v.check_type,
                v.cost_per_check_usd,
                vw.checks_sent,
                vw.matched,
                vw.no_match,
                vw.timeout,
                vw.cost_usd,
                vw.source_file,
                vw.source_line
            FROM vendor_weekly AS vw
            INNER JOIN vendors AS v ON v.vendor_id = vw.vendor_id;
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    connection.commit()
