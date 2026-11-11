# IDComply Waterfall Decision Workbench

This repository implements **Phases 1–5**: evidence import/validation, an observed customer-economics baseline, a constrained first-two-registry ordering comparison, a three-step pilot workflow, a separate QBR feature-adoption case, and reviewable proposal exports. It imports the three core synthetic IDComply CSVs into SQLite, preserves a hash-addressed copy of each source, records validation issues, stores the QBR facts separately from customer evidence, and exposes customer/date results through FastAPI and a React screen.

The importer deliberately does not ingest `idcomply_core_signals_weekly_SYNTHETIC.csv`; that file is deferred by the design. The supplied schema document is an input to the validation contract, not a database source. The QBR transcript is not joined to any CSV customer.

## Run the importer

```bash
python3 -m app.importer \
  --source-dir "/path/to/idcomply_case_pack [SYNTHETIC]" \
  --schema-document "/path/to/IDComply Verification _ Support Tickets_.docx" \
  --qbr-transcript "/path/to/Meeting Transcript_ Quarterly Business Review Readout (Notes by Gemini).docx"
```

By default this writes `data/idcomply.sqlite` and `data/validation-report.json`. Each supplied CSV or supporting document is copied once to `data/raw/<sha256>_<filename>`; source files outside this repository are never modified.

```bash
python3 -m unittest discover -s tests -v
```

FastAPI is declared in `pyproject.toml` for the later API surface. The Phase 1 importer and its tests use only the Python standard library, so they run offline before dependencies are installed.

## Run the Phase 2 API and UI

After importing the data and installing Python dependencies, start the API:

```bash
pip install -e .
uvicorn app.main:app --reload
```

The API requires an explicit customer, `from`, and `to` scope:

```text
GET /customers
GET /opportunities
GET /baseline?customer=Rivet%20Play&from=2026-02-02&to=2026-07-27
GET /calibration?customer=Rivet%20Play&from=2026-02-02&to=2026-07-27
POST /compare-pair
POST /business-case
POST /business-case/qbr
POST /scenarios
POST /proposals
GET /proposals/{id}/export
GET /proposals/{id}/export.html
```

`POST /compare-pair` accepts the only two standard pilot inputs: eligible verification volume and a one-time implementation cost. It returns the calculated savings per 1,000, projected savings, implementation-adjusted savings, break-even volume, and descriptive week-by-week consistency.

```json
{
  "customer": "Tidewater Casino",
  "from": "2026-02-02",
  "to": "2026-07-27",
  "projected_eligible_entrants": 10000,
  "implementation_cost_usd": 2500
}
```

The `frontend/` folder contains the minimal React/Vite workspace. Set `VITE_API_BASE_URL` if the API is not served at `http://localhost:8000`.

```bash
cd frontend
npm install
npm run dev
```

## Phase 1 guarantees

- Re-running the same import replaces the observed snapshot instead of duplicating records.
- Invalid rows remain represented in `raw_rows` and `import_issues`, but are excluded from the clean observed tables.
- `calibration` and `waterfall` records remain separate in `vendor_weekly`.
- `ANY_REGISTRY` is accepted only as an informational calibration roll-up; it is not joined to a callable vendor.
- Vendor joins are performed by `vendor_id` only, after validation. The importer never joins vendor rows to funnel rows, so it cannot multiply funnel totals.
- Baseline costs and funnel counts are separately aggregated by identical recorded waterfall configuration. If a selected range spans configurations, the aggregate is explicitly marked mixed and route-specific metrics remain separate.
- Pair ordering is restricted to the first two recorded identity registries. Missing, unequal, or zero calibration samples and changed configurations disable the comparison; phone and document steps are not treated as substitutes.
- The Waterfall opportunity view carries the selected customer, scope, comparison, and pilot inputs through three steps: find an opportunity, compare the recorded and proposed configurations, then evaluate the pilot. It only models a standard registry reorder; billing, delivery, and conversion assumptions are deliberately not default inputs.
- The Betting.com QBR feature-adoption case is deliberately isolated from every CSV customer. Its $300 figure is clearly labeled as assumed first-year user value; gross value minus the entered budget is not presented as profit.
- Proposal exports are immutable run records: JSON and printable HTML preserve the customer, scope, pairs, observed sample, weekly consistency, formula, modeled savings, entered pilot inputs, source hashes, assumptions, missing evidence, and pilot success conditions.
