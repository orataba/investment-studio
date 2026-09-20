from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import ScreenerQueryRequest
from watchlist_app.api.presenters import present_group_by_options
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.read_models import execute_watchlist_query
from watchlist_app.services.research_projection import (
    RESEARCH_WATCHLIST_FIELD_KEYS,
    build_research_watchlist_attribute_overrides,
    build_risk_watchlist_attribute_overrides,
)
from watchlist_app.services.read_model_freshness import (
    latest_local_market_data_date,
    local_materialization_source_cutoff,
    local_materialization_version,
    schedule_instrument_refreshes_if_stale,
)
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument_summaries,
)
from watchlist_app.services.watchlist_query_contract import (
    WatchlistQueryContractError,
    validate_watchlist_query_contract,
)


router = APIRouter()
read_model_repository = SQLAlchemyReadModelRepository()
watchlist_repository = SQLAlchemyWatchlistRepository()
canonical_recalc_service = CanonicalRecalcService()
field_registry_repository = SQLAlchemyFieldRegistryRepository()


def _validate_query_contract(
    session: Session,
    payload_data: dict[str, object],
    view,
) -> None:
    field_records = list(field_registry_repository.list_fields(session))
    fields = {item.field_key: item for item in field_records}
    selected = (
        payload_data.get("selected_fields")
        if "selected_fields" in payload_data
        else [column.field_key for column in view.columns if column.is_visible] if view else []
    )
    filters = (
        payload_data.get("filters")
        if "filters" in payload_data
        else (view.default_filters_json or {}) if view else {}
    )
    sort_rules = (
        payload_data.get("sort")
        if "sort" in payload_data
        else (view.default_sort_json or []) if view else []
    )
    group_by = (
        payload_data.get("group_by")
        if "group_by" in payload_data
        else view.default_group_by if view else "none"
    )
    available_group_by_codes = {
        item["code"]
        for item in present_group_by_options(field_records)
    }
    if str(group_by or "none") not in available_group_by_codes:
        raise HTTPException(
            status_code=422,
            detail=f"Group by field {group_by!r} is not available for this Watchlist.",
        )
    advanced = (
        payload_data.get("advanced_filters")
        if "advanced_filters" in payload_data
        else view.default_advanced_filter_json if view else None
    )
    try:
        validate_watchlist_query_contract(
            fields,
            selected_fields=selected,
            filters=filters,
            sort_rules=sort_rules,
            group_by=group_by,
            advanced_filters=advanced,
        )
    except WatchlistQueryContractError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _advanced_filter_fields(node: object) -> set[str]:
    if not isinstance(node, dict):
        return set()
    fields = {str(node.get("field") or "")} if node.get("type") == "rule" else set()
    for child in node.get("conditions") or []:
        fields.update(_advanced_filter_fields(child))
    return fields


def _requested_query_fields(
    payload_data: dict[str, object],
    view,
) -> set[str]:
    fields = {str(value) for value in payload_data.get("selected_fields") or []}
    fields.update(str(value) for value in (payload_data.get("filters") or {}).keys())
    fields.update(
        str(rule.get("field") or "")
        for rule in payload_data.get("sort") or []
        if isinstance(rule, dict)
    )
    fields.add(str(payload_data.get("group_by") or ""))
    fields.update(_advanced_filter_fields(payload_data.get("advanced_filters")))
    if view is not None:
        if "selected_fields" not in payload_data:
            fields.update(column.field_key for column in view.columns if column.is_visible)
        if "filters" not in payload_data:
            fields.update((view.default_filters_json or {}).keys())
        if "sort" not in payload_data:
            fields.update(
                str(rule.get("field") or "")
                for rule in view.default_sort_json or []
                if isinstance(rule, dict)
            )
        if "group_by" not in payload_data:
            fields.add(str(view.default_group_by or ""))
        if "advanced_filters" not in payload_data:
            fields.update(_advanced_filter_fields(view.default_advanced_filter_json))
    return fields


def _merge_attribute_overrides(
    *overrides: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for override in overrides:
        for instrument_id, values in override.items():
            merged.setdefault(instrument_id, {}).update(values)
    return merged


@router.post("/query")
def run_screener_query(
    payload: ScreenerQueryRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if watchlist_repository.get(session, payload.watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    payload_data = payload.model_dump(exclude_unset=True)
    view = None
    if payload.view_id:
        view = read_model_repository.get_view(
            session,
            watchlist_id=payload.watchlist_id,
            view_id=payload.view_id,
        )
        from studio_identity import current_principal
        if view is None or (not current_principal().local_unrestricted and view.author_user_id not in {None, current_principal().user_id}):
            raise HTTPException(status_code=404, detail="Watchlist view not found")
    rows = read_model_repository.list_watchlist_rows(session, payload.watchlist_id)
    _validate_query_contract(session, payload_data, view)
    requested_fields = _requested_query_fields(payload_data, view)
    charts = read_model_repository.list_screener_charts(
        session,
        [row.instrument_id for row in rows],
    )
    missing_projections = [chart.instrument_id for chart in charts if chart.payload_json is None]
    if missing_projections:
        # An interrupted/manual upgrade must enqueue its repair before failing;
        # exception responses do not run FastAPI's normal BackgroundTasks.
        schedule_instrument_refreshes_if_stale(
            targets=[{"instrument_id": instrument_id, "local_materialization_version": None}
                     for instrument_id in missing_projections],
            trigger_ref_type="screener_projection_missing",
            trigger_ref_id=payload.watchlist_id,
            raise_on_error=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Watchlist list data is being rebuilt. Please retry shortly.",
            headers={"Retry-After": "1"},
        )
    identity_overrides: dict[str, dict[str, object]] = {}
    if "currency" in requested_fields:
        instrument_ids = [row.instrument_id for row in rows]
        try:
            shared_instruments = get_shared_instrument_summaries(instrument_ids)
        except SharedInstrumentRegistryError as error:
            raise HTTPException(
                status_code=502,
                detail=f"Instrument currency is unavailable: {error}",
            ) from error
        missing_currency_ids = [
            instrument_id
            for instrument_id in instrument_ids
            if not str((shared_instruments.get(instrument_id) or {}).get("currency") or "").strip()
        ]
        if missing_currency_ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Canonical currency is missing for Watchlist instruments: "
                    f"{', '.join(missing_currency_ids)}."
                ),
            )
        identity_overrides = {
            instrument_id: {
                "currency": str(shared_instruments[instrument_id]["currency"]).strip().upper()
            }
            for instrument_id in instrument_ids
        }
    try:
        peer_attribute_overrides = (
            canonical_recalc_service.peer_watchlist_attribute_overrides(
                session,
                instrument_ids=[row.instrument_id for row in rows],
            )
            if any(field.startswith("attr.peer_") for field in requested_fields)
            else {}
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Live peer comparison is unavailable: {error}",
        ) from error
    if requested_fields.intersection(RESEARCH_WATCHLIST_FIELD_KEYS - {"attr.risk_attention"}):
        research_attribute_overrides = build_research_watchlist_attribute_overrides(
            session,
            instrument_ids=[row.instrument_id for row in rows],
        )
    elif "attr.risk_attention" in requested_fields:
        research_attribute_overrides = build_risk_watchlist_attribute_overrides(
            session,
            instrument_ids=[row.instrument_id for row in rows],
        )
    else:
        research_attribute_overrides = {}
    attribute_overrides = _merge_attribute_overrides(
        peer_attribute_overrides,
        research_attribute_overrides,
    )
    response = execute_watchlist_query(
        rows=rows,
        charts=charts,
        row_overrides=identity_overrides or None,
        attribute_overrides=attribute_overrides or None,
        payload=payload_data,
        view=view,
    )
    chart_map = {item.instrument_id: item for item in charts}
    row_map = {item.instrument_id: item for item in rows}
    stale_check_targets: list[dict[str, object]] = []
    for item in response.get("rows", []):
        instrument_id = str(item.get("instrument_id") or "").strip()
        if not instrument_id:
            continue
        chart_record = chart_map.get(instrument_id)
        row_record = row_map.get(instrument_id)
        stale_check_targets.append(
            {
                "instrument_id": instrument_id,
                "local_latest_date": latest_local_market_data_date(
                    chart_payload=getattr(chart_record, "payload_json", None),
                    fallback_values=(
                        getattr(row_record, "last_nav_date", None),
                        item.get("latest_quote_date"),
                        item.get("metric_as_of_date"),
                    ),
                ),
                "local_source_cutoff_at": local_materialization_source_cutoff(
                    getattr(chart_record, "source_cutoff_at", None),
                    getattr(row_record, "last_fact_update_at", None),
                ),
                "local_materialization_version": local_materialization_version(
                    getattr(chart_record, "materialization_version", None),
                    getattr(row_record, "materialization_version", None),
                ),
                "local_data_freshness_status": (
                    getattr(chart_record, "data_freshness_status", None)
                    or getattr(row_record, "data_freshness_status", None)
                ),
            }
        )
    if stale_check_targets:
        background_tasks.add_task(
            schedule_instrument_refreshes_if_stale,
            targets=stale_check_targets,
            trigger_ref_type="screener_query",
            trigger_ref_id=payload.watchlist_id,
        )
    return response
