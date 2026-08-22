from __future__ import annotations

from datetime import date
from typing import cast

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.api.contracts import (
    AnalyticsScopePolicyUpsertRequest,
    AnalyticsScopePolicyRecord,
    AnalyticsTaxonomySelectionRecord,
    DefaultPlanningTaxonomyResponse,
    DefaultPlanningTaxonomyUpdateRequest,
    PortfolioInstrumentUniverseCreateRequest,
    PortfolioInstrumentUniverseRecord,
    TargetSetCreateRequest,
    TargetSetIntegrityIssueRecord,
    TargetSetLineRecord,
    TargetSetRecord,
    TargetSetUpdateRequest,
    TaxonomyAssignmentUpdateRequest,
    TaxonomyAssignmentCreateRequest,
    TaxonomyAssignmentRecord,
    TaxonomyCatalogResponse,
    TaxonomyCreateRequest,
    TaxonomyNodeUpdateRequest,
    TaxonomyNodeCreateRequest,
    TaxonomyNodeRecord,
    TaxonomyRecord,
    TaxonomyUpdateRequest,
)
from portfolio_app.services.analytics_scope import (
    analytics_policy_version,
    list_analytics_taxonomy_selections,
    list_analytics_scope_policies,
    replace_analytics_scope_policy,
)
from portfolio_app.services.instrument_charts import build_instrument_holdings_market_profile
from portfolio_app.services.instrument_registry import InstrumentRegistryError, get_registry_instrument
from portfolio_app.services.portfolio_store import (
    create_taxonomy,
    create_taxonomy_assignment,
    create_taxonomy_node,
    create_target_set,
    delete_portfolio_instrument_universe_record,
    delete_taxonomy,
    delete_taxonomy_assignment,
    delete_taxonomy_node,
    delete_target_set,
    get_portfolio,
    get_taxonomy,
    list_target_set_lines,
    list_target_set_integrity_issues,
    list_target_sets,
    list_portfolio_instrument_universe,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
    set_default_planning_taxonomy,
    update_taxonomy,
    update_taxonomy_assignment,
    update_taxonomy_node,
    update_target_set,
    upsert_portfolio_instrument_universe_record,
)
from portfolio_app.services.risk_basis import calculation_frequency_profile_for_instruments


router = APIRouter()


def _load_registry_instrument_ref(instrument_id: str) -> dict[str, object]:
    try:
        instrument = get_registry_instrument(instrument_id)
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if instrument is None:
        raise HTTPException(status_code=400, detail="Instrument not found in shared registry.")

    return {
        "instrument_id": instrument["instrument_id"],
        "instrument_name": instrument["instrument_name"],
        "instrument_type": instrument["instrument_type"],
        "currency": instrument["currency"],
        "exchange_code": instrument.get("exchange_code"),
        "identifiers": instrument.get("identifiers", []),
    }


def _universe_record_is_cash(record: dict[str, object]) -> bool:
    instrument_id = str(record.get("instrument_id") or "").strip().lower()
    instrument_ref = record.get("instrument_ref") if isinstance(record.get("instrument_ref"), dict) else {}
    instrument_type = str((instrument_ref or {}).get("instrument_type") or "").strip().lower()
    return instrument_type == "cash" or instrument_id.startswith("cash:")


def _enrich_universe_market_profiles(
    records: list[dict[str, object]],
    *,
    as_of_date: date,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    instrument_ids = [
        str(record.get("instrument_id") or "").strip()
        for record in records
        if str(record.get("status") or "active") == "active"
        and str(record.get("instrument_id") or "").strip()
        and not _universe_record_is_cash(record)
    ]
    risk_basis_profile = calculation_frequency_profile_for_instruments(
        instrument_ids,
        end_date=as_of_date,
    )
    calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
    enriched_records: list[dict[str, object]] = []
    for record in records:
        enriched = dict(record)
        instrument_id = str(enriched.get("instrument_id") or "").strip()
        if instrument_id and instrument_id in instrument_ids:
            profile = build_instrument_holdings_market_profile(
                instrument_id,
                as_of_date=as_of_date,
                calculation_frequency=calculation_frequency,
            )
            enriched["instrument_trend_basis"] = profile.get("instrument_trend_basis")
            enriched["instrument_risk_frequency"] = profile.get("instrument_risk_frequency")
            enriched["instrument_return_series_all"] = profile.get("instrument_return_series_all")
        enriched_records.append(enriched)
    return enriched_records, risk_basis_profile


@router.get("/{portfolio_id}/taxonomies", response_model=TaxonomyCatalogResponse)
def get_portfolio_taxonomies(
    portfolio_id: str,
    include_market_profile: bool = False,
    as_of_date: date | None = None,
) -> TaxonomyCatalogResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    universe_records = list_portfolio_instrument_universe(portfolio_id)
    risk_basis_profile = None
    if include_market_profile:
        resolved_as_of_date = (
            as_of_date
            or (
                date.fromisoformat(str(portfolio.get("as_of_date"))[:10])
                if portfolio.get("as_of_date")
                else None
            )
            or date.today()
        )
        try:
            universe_records, risk_basis_profile = _enrich_universe_market_profiles(
                universe_records,
                as_of_date=resolved_as_of_date,
            )
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    return TaxonomyCatalogResponse(
        portfolio_id=portfolio_id,
        default_planning_taxonomy_id=portfolio.get("default_planning_taxonomy_id"),
        risk_basis=risk_basis_profile,
        taxonomies=[TaxonomyRecord.model_validate(item) for item in list_taxonomies(portfolio_id)],
        taxonomy_nodes=[TaxonomyNodeRecord.model_validate(item) for item in list_taxonomy_nodes(portfolio_id)],
        taxonomy_assignments=[
            TaxonomyAssignmentRecord.model_validate(item)
            for item in list_taxonomy_assignments(portfolio_id)
        ],
        analytics_scope_policy_version=analytics_policy_version(portfolio_id),
        analytics_scope_policies=[
            AnalyticsScopePolicyRecord.model_validate(item)
            for item in list_analytics_scope_policies(portfolio_id)
        ],
        analytics_taxonomy_selections=[
            AnalyticsTaxonomySelectionRecord.model_validate(item)
            for item in list_analytics_taxonomy_selections(portfolio_id)
        ],
        instrument_universe=[
            PortfolioInstrumentUniverseRecord.model_validate(item)
            for item in universe_records
        ],
        target_sets=[TargetSetRecord.model_validate(item) for item in list_target_sets(portfolio_id)],
        target_set_lines=[TargetSetLineRecord.model_validate(item) for item in list_target_set_lines(portfolio_id)],
        target_set_integrity_issues=[
            TargetSetIntegrityIssueRecord.model_validate(item)
            for item in list_target_set_integrity_issues(portfolio_id)
        ],
    )


@router.put("/{portfolio_id}/taxonomies/default-planning", response_model=DefaultPlanningTaxonomyResponse)
def update_default_planning_taxonomy(
    portfolio_id: str,
    payload: DefaultPlanningTaxonomyUpdateRequest,
) -> DefaultPlanningTaxonomyResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        updated_portfolio = set_default_planning_taxonomy(
            portfolio_id,
            payload.taxonomy_id,
            effective_from=payload.effective_from,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if updated_portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    return DefaultPlanningTaxonomyResponse(
        portfolio_id=portfolio_id,
        default_planning_taxonomy_id=updated_portfolio.get("default_planning_taxonomy_id"),
    )


@router.post("/{portfolio_id}/taxonomies/instrument-universe", response_model=PortfolioInstrumentUniverseRecord)
def add_portfolio_instrument_universe_record(
    portfolio_id: str,
    payload: PortfolioInstrumentUniverseCreateRequest,
) -> PortfolioInstrumentUniverseRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    instrument_ref = _load_registry_instrument_ref(payload.instrument_id)
    try:
        record = upsert_portfolio_instrument_universe_record(
            portfolio_id=portfolio_id,
            instrument_id=payload.instrument_id,
            instrument_ref=instrument_ref,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioInstrumentUniverseRecord.model_validate(record)


@router.delete("/{portfolio_id}/taxonomies/instrument-universe/{instrument_id}")
def delete_portfolio_instrument_universe(
    portfolio_id: str,
    instrument_id: str,
) -> dict[str, object]:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        deleted = delete_portfolio_instrument_universe_record(portfolio_id, instrument_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Instrument universe record not found")
    return {"portfolio_id": portfolio_id, "instrument_id": instrument_id, "deleted": True}


@router.post("/{portfolio_id}/taxonomies", response_model=TaxonomyRecord)
def create_portfolio_taxonomy(
    portfolio_id: str,
    payload: TaxonomyCreateRequest,
) -> TaxonomyRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    record = create_taxonomy(
        portfolio_id=portfolio_id,
        effective_from=payload.effective_from,
        name=payload.name,
        taxonomy_type=payload.taxonomy_type,
        purpose=payload.purpose,
        primary_assignment_scope=payload.primary_assignment_scope,
        planning_enabled=payload.planning_enabled,
        budgeting_level=payload.budgeting_level,
        root_default_target_dimension=payload.root_default_target_dimension,
        status=payload.status,
        source_template_ref=payload.source_template_ref,
    )
    return TaxonomyRecord.model_validate(record)


@router.patch("/{portfolio_id}/taxonomies/{taxonomy_id}", response_model=TaxonomyRecord)
def update_portfolio_taxonomy(
    portfolio_id: str,
    taxonomy_id: str,
    payload: TaxonomyUpdateRequest,
) -> TaxonomyRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = update_taxonomy(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TaxonomyRecord.model_validate(record)


@router.delete("/{portfolio_id}/taxonomies/{taxonomy_id}")
def delete_portfolio_taxonomy(
    portfolio_id: str,
    taxonomy_id: str,
    effective_from: date = Query(...),
) -> dict[str, object]:
    deleted = delete_taxonomy(
        portfolio_id,
        taxonomy_id,
        effective_from=effective_from,
    )
    if not deleted:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        raise HTTPException(status_code=404, detail="Taxonomy not found")
    return {"portfolio_id": portfolio_id, "taxonomy_id": taxonomy_id, "deleted": True}


@router.post("/{portfolio_id}/taxonomies/{taxonomy_id}/nodes", response_model=TaxonomyNodeRecord)
def create_portfolio_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    payload: TaxonomyNodeCreateRequest,
) -> TaxonomyNodeRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = create_taxonomy_node(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            effective_from=payload.effective_from,
            parent_taxonomy_node_id=payload.parent_taxonomy_node_id,
            node_name=payload.node_name,
            node_code=payload.node_code,
            sort_order=payload.sort_order,
            is_terminal=payload.is_terminal,
            default_target_dimension=payload.default_target_dimension,
            status=payload.status,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TaxonomyNodeRecord.model_validate(record)


@router.patch("/{portfolio_id}/taxonomies/{taxonomy_id}/nodes/{taxonomy_node_id}", response_model=TaxonomyNodeRecord)
def update_portfolio_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    payload: TaxonomyNodeUpdateRequest,
) -> TaxonomyNodeRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = update_taxonomy_node(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            taxonomy_node_id=taxonomy_node_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TaxonomyNodeRecord.model_validate(record)


@router.delete("/{portfolio_id}/taxonomies/{taxonomy_id}/nodes/{taxonomy_node_id}")
def delete_portfolio_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    effective_from: date = Query(...),
) -> dict[str, object]:
    try:
        deleted = delete_taxonomy_node(
            portfolio_id,
            taxonomy_id,
            taxonomy_node_id,
            effective_from=effective_from,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not deleted:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if get_taxonomy(portfolio_id, taxonomy_id) is None:
            raise HTTPException(status_code=404, detail="Taxonomy not found")
        raise HTTPException(status_code=404, detail="Taxonomy node not found")
    return {
        "portfolio_id": portfolio_id,
        "taxonomy_id": taxonomy_id,
        "taxonomy_node_id": taxonomy_node_id,
        "deleted": True,
    }


@router.post("/{portfolio_id}/taxonomies/{taxonomy_id}/assignments", response_model=TaxonomyAssignmentRecord)
def create_portfolio_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    payload: TaxonomyAssignmentCreateRequest,
) -> TaxonomyAssignmentRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = create_taxonomy_assignment(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            effective_from=payload.effective_from,
            target_scope=payload.target_scope,
            target_entity_id=payload.target_entity_id,
            taxonomy_node_id=payload.taxonomy_node_id,
            status=payload.status,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TaxonomyAssignmentRecord.model_validate(record)


@router.patch("/{portfolio_id}/taxonomies/{taxonomy_id}/assignments/{assignment_id}", response_model=TaxonomyAssignmentRecord)
def update_portfolio_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    assignment_id: str,
    payload: TaxonomyAssignmentUpdateRequest,
) -> TaxonomyAssignmentRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = update_taxonomy_assignment(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            assignment_id=assignment_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TaxonomyAssignmentRecord.model_validate(record)


@router.delete("/{portfolio_id}/taxonomies/{taxonomy_id}/assignments/{assignment_id}")
def delete_portfolio_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    assignment_id: str,
    effective_from: date = Query(...),
) -> dict[str, object]:
    deleted = delete_taxonomy_assignment(
        portfolio_id,
        taxonomy_id,
        assignment_id,
        effective_from=effective_from,
    )
    if not deleted:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if get_taxonomy(portfolio_id, taxonomy_id) is None:
            raise HTTPException(status_code=404, detail="Taxonomy not found")
        raise HTTPException(status_code=404, detail="Taxonomy assignment not found")
    return {
        "portfolio_id": portfolio_id,
        "taxonomy_id": taxonomy_id,
        "assignment_id": assignment_id,
        "deleted": True,
    }


@router.post("/{portfolio_id}/taxonomies/{taxonomy_id}/target-sets", response_model=TargetSetRecord)
def create_portfolio_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    payload: TargetSetCreateRequest,
) -> TargetSetRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = create_target_set(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            effective_from=payload.effective_from,
            comparator_taxonomy_node_id=payload.comparator_taxonomy_node_id,
            target_set_type=payload.target_set_type,
            name=payload.name,
            weight_enabled=payload.weight_enabled,
            risk_budget_enabled=payload.risk_budget_enabled,
            status=payload.status,
            notes=payload.notes,
            lines=[item.model_dump() for item in payload.lines],
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TargetSetRecord.model_validate(record)


@router.patch("/{portfolio_id}/taxonomies/{taxonomy_id}/target-sets/{target_set_id}", response_model=TargetSetRecord)
def update_portfolio_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    target_set_id: str,
    payload: TargetSetUpdateRequest,
) -> TargetSetRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")

    try:
        record = update_target_set(
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            target_set_id=target_set_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TargetSetRecord.model_validate(record)


@router.delete("/{portfolio_id}/taxonomies/{taxonomy_id}/target-sets/{target_set_id}")
def delete_portfolio_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    target_set_id: str,
    effective_from: date = Query(...),
) -> dict[str, object]:
    deleted = delete_target_set(
        portfolio_id,
        taxonomy_id,
        target_set_id,
        effective_from=effective_from,
    )
    if not deleted:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if get_taxonomy(portfolio_id, taxonomy_id) is None:
            raise HTTPException(status_code=404, detail="Taxonomy not found")
        raise HTTPException(status_code=404, detail="Target set not found")
    return {
        "portfolio_id": portfolio_id,
        "taxonomy_id": taxonomy_id,
        "target_set_id": target_set_id,
        "deleted": True,
    }


@router.put(
    "/{portfolio_id}/taxonomies/{taxonomy_id}/analytics-scope-policies/{taxonomy_node_id}",
    response_model=AnalyticsScopePolicyRecord,
)
def replace_portfolio_analytics_scope_policy(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    payload: AnalyticsScopePolicyUpsertRequest,
) -> AnalyticsScopePolicyRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if get_taxonomy(portfolio_id, taxonomy_id) is None:
        raise HTTPException(status_code=404, detail="Taxonomy not found")
    try:
        record = replace_analytics_scope_policy(
            portfolio_id,
            taxonomy_id=taxonomy_id,
            taxonomy_node_id=taxonomy_node_id,
            **payload.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return AnalyticsScopePolicyRecord.model_validate(record)
