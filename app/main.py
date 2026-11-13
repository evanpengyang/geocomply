"""HTTP API for the IDComply Waterfall Decision Workbench."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as error:  # Keeps the Phase 1 importer usable in a dependency-free environment.
    raise RuntimeError("Install project dependencies with `pip install -e .` to run the API.") from error

from .baseline import BaselineInputError, get_baseline, list_customers
from .comparison import ComparisonInputError, compare_first_two_registries
from .business_case import BusinessCaseInputError, CustomerBusinessInputs, customer_business_case, qbr_business_case
from .proposals import ProposalInputError, create_proposal, get_proposal, get_scenario, printable_proposal_html, save_scenario
from .opportunities import list_opportunities
from .database import connect, initialize
from .vendor_recommendation import RecommendationInputError, recommend_first_vendor


DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "idcomply.sqlite"
app = FastAPI(title="IDComply Waterfall Decision Workbench", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    # The UI reads baseline data and submits JSON to /compare-pair. Starlette
    # handles the browser's OPTIONS preflight when POST is an allowed method.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)
        rows = connection.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
        return {"status": "ok", "metadata": {row["key"]: row["value"] for row in rows}}
    finally:
        connection.close()


@app.get("/customers")
def customers() -> dict[str, object]:
    return {"classification": "Observed", "customers": list_customers(DATABASE_PATH)}


@app.get("/baseline")
def baseline(
    customer: Annotated[str, Query(min_length=1)],
    from_date: Annotated[str, Query(alias="from")],
    to_date: Annotated[str, Query(alias="to")],
) -> dict[str, object]:
    try:
        return get_baseline(DATABASE_PATH, customer, from_date, to_date)
    except BaselineInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class PairComparisonRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    customer: str = Field(min_length=1)
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    projected_eligible_entrants: int = Field(ge=0)
    implementation_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)


class CustomerBusinessCaseRequest(BaseModel):
    customer: str = Field(min_length=1)
    delta_verifications: int = Field(ge=0)
    activation_probability: Decimal = Field(ge=0, le=1)
    contribution_per_activated_user_usd: Decimal = Field(ge=0)
    delta_customer_bill_usd: Decimal
    incremental_operating_risk_cost_usd: Decimal = Field(ge=0)
    implementation_cost_usd: Decimal = Field(ge=0)
    pricing_assumption: str = Field(min_length=1)
    delta_customer_revenue_usd: Decimal
    delta_vendor_spend_usd: Decimal
    incremental_delivery_cost_usd: Decimal = Field(ge=0)


class QbrBusinessCaseRequest(BaseModel):
    realization_factor: Decimal = Field(ge=0, le=1)
    implementation_budget_usd: Decimal = Field(ge=0)
    adoption_choice: str


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    scenario_type: str = Field(min_length=1, max_length=80)
    customer: str = Field(min_length=1)
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    payload: dict[str, object]


class ProposalRequest(BaseModel):
    scenario_id: str = Field(min_length=1)


@app.get("/calibration")
def calibration(
    customer: Annotated[str, Query(min_length=1)],
    from_date: Annotated[str, Query(alias="from")],
    to_date: Annotated[str, Query(alias="to")],
) -> dict[str, object]:
    try:
        return compare_first_two_registries(DATABASE_PATH, customer, from_date, to_date)
    except ComparisonInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/opportunities")
def opportunities() -> dict[str, object]:
    return list_opportunities(DATABASE_PATH)


@app.get("/vendor-recommendation")
def vendor_recommendation(
    customer: Annotated[str, Query(min_length=1)],
    from_date: Annotated[str, Query(alias="from")],
    to_date: Annotated[str, Query(alias="to")],
    cost_priority: Annotated[Decimal, Query(ge=0, le=5)] = Decimal("3"),
    match_priority: Annotated[Decimal, Query(ge=0, le=5)] = Decimal("3"),
    timeout_priority: Annotated[Decimal, Query(ge=0, le=5)] = Decimal("3"),
) -> dict[str, object]:
    try:
        return recommend_first_vendor(
            DATABASE_PATH, customer, from_date, to_date,
            cost_priority, match_priority, timeout_priority,
        )
    except RecommendationInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/compare-pair")
def compare_pair(request: PairComparisonRequest) -> dict[str, object]:
    try:
        return compare_first_two_registries(
            DATABASE_PATH,
            request.customer,
            request.from_date,
            request.to_date,
            request.projected_eligible_entrants,
            request.implementation_cost_usd,
        )
    except ComparisonInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/business-case")
def business_case(request: CustomerBusinessCaseRequest) -> dict[str, object]:
    try:
        return customer_business_case(CustomerBusinessInputs(**request.model_dump()))
    except BusinessCaseInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/business-case/qbr")
def qbr_business_case_endpoint(request: QbrBusinessCaseRequest) -> dict[str, object]:
    try:
        return qbr_business_case(
            DATABASE_PATH,
            request.realization_factor,
            request.implementation_budget_usd,
            request.adoption_choice,
        )
    except BusinessCaseInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/scenarios")
def scenarios(request: ScenarioRequest) -> dict[str, object]:
    try:
        return save_scenario(
            DATABASE_PATH, request.name, request.scenario_type, request.customer,
            request.from_date, request.to_date, request.payload, request.id,
        )
    except ProposalInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/scenarios/{scenario_id}")
def scenario(scenario_id: str) -> dict[str, object]:
    try:
        return get_scenario(DATABASE_PATH, scenario_id)
    except ProposalInputError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/proposals")
def proposals(request: ProposalRequest) -> dict[str, object]:
    try:
        created = create_proposal(DATABASE_PATH, request.scenario_id)
        return {
            **created,
            "json_export_url": f"/proposals/{created['id']}/export",
            "html_export_url": f"/proposals/{created['id']}/export.html",
        }
    except ProposalInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/proposals/{proposal_id}/export")
def proposal_export(proposal_id: str) -> dict[str, object]:
    try:
        return get_proposal(DATABASE_PATH, proposal_id)
    except ProposalInputError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/proposals/{proposal_id}/export.html", response_class=HTMLResponse)
def proposal_html_export(proposal_id: str) -> HTMLResponse:
    try:
        return HTMLResponse(printable_proposal_html(get_proposal(DATABASE_PATH, proposal_id)))
    except ProposalInputError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
