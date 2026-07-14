"""Published-only Portfolio overview and holdings workspace.

Both surfaces are projections of one immutable Portfolio Daily publication.
They never ensure, refresh, cache, or reconstruct financial values from mutable
ledger/quote facts during a request.  Display configuration comes from the
same sealed manifest as the financial output, so a response cannot mix an old
publication with newly edited portfolio metadata.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_app.api.contracts import PortfolioWorkspaceSummaryResponse
from portfolio_app.api.published_portfolio_daily import (
    calculation_not_ready,
    read_published_latest,
)
from portfolio_app.calculations.numeric import (
    canonical_decimal,
    exact_decimal_subtract,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_config_input,
    portfolio_daily_instrument_input,
)
from portfolio_app.calculations.portfolio_daily.published_views import (
    publication_metadata_response,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyContribution,
    PortfolioDailyHolding,
    PortfolioDailyLot,
)
from portfolio_app.core.operating_profiles import require_portfolio_operating_profile
from portfolio_app.db.session import get_db_session


router = APIRouter()


def _published_integrity_error(
    portfolio_id: str,
    *,
    reason: str,
    **detail: object,
) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "published_calculation_integrity_error",
            "portfolio_id": portfolio_id,
            "reason": reason,
            **detail,
        },
    )


def _required_text(
    row: dict[str, object],
    field_name: str,
    *,
    portfolio_id: str,
    record_type: str,
) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_taxonomy_structure_invalid",
            record_type=record_type,
            field_name=field_name,
        )
    return value


def _optional_text(
    row: dict[str, object],
    field_name: str,
    *,
    portfolio_id: str,
    record_type: str,
) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    return _required_text(
        row,
        field_name,
        portfolio_id=portfolio_id,
        record_type=record_type,
    )


def _sealed_record_list(
    taxonomy: dict[str, object],
    field_name: str,
    *,
    portfolio_id: str,
) -> tuple[dict[str, object], ...]:
    value = taxonomy.get(field_name)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_taxonomy_structure_invalid",
            field_name=f"taxonomy.{field_name}",
        )
    return tuple(value)


def _sealed_taxonomy_display_config(
    config: dict[str, object],
    *,
    portfolio_id: str,
) -> dict[str, object]:
    """Project and validate taxonomy display state from the sealed manifest.

    Empty taxonomy arrays are a valid explicit state (for example, an ETF-only
    portfolio).  A missing or malformed taxonomy snapshot is not: silently
    reading the live catalog would mix two different knowledge cutoffs.
    """

    taxonomy_value = config.get("taxonomy")
    if not isinstance(taxonomy_value, dict):
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_taxonomy_snapshot_missing",
        )
    taxonomy_rows = _sealed_record_list(
        taxonomy_value,
        "taxonomies",
        portfolio_id=portfolio_id,
    )
    node_rows = _sealed_record_list(
        taxonomy_value,
        "nodes",
        portfolio_id=portfolio_id,
    )
    assignment_rows = _sealed_record_list(
        taxonomy_value,
        "assignments",
        portfolio_id=portfolio_id,
    )

    taxonomies: list[dict[str, object]] = []
    taxonomy_ids: set[str] = set()
    for row in taxonomy_rows:
        record_type = "taxonomy"
        taxonomy_id = _required_text(
            row,
            "taxonomy_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        row_portfolio_id = _required_text(
            row,
            "portfolio_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        if row_portfolio_id != portfolio_id or taxonomy_id in taxonomy_ids:
            raise _published_integrity_error(
                portfolio_id,
                reason="sealed_taxonomy_referential_integrity_invalid",
                record_type=record_type,
                taxonomy_id=taxonomy_id,
            )
        taxonomy_ids.add(taxonomy_id)
        primary_scope = _required_text(
            row,
            "primary_assignment_scope",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        target_dimension = _required_text(
            row,
            "root_default_target_dimension",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        planning_enabled = row.get("planning_enabled")
        if (
            primary_scope not in {"instrument", "account", "cash_bucket"}
            or target_dimension not in {"weight", "risk_budget"}
            or not isinstance(planning_enabled, bool)
        ):
            raise _published_integrity_error(
                portfolio_id,
                reason="sealed_taxonomy_structure_invalid",
                record_type=record_type,
                taxonomy_id=taxonomy_id,
            )
        taxonomies.append(
            {
                "taxonomy_id": taxonomy_id,
                "portfolio_id": row_portfolio_id,
                "name": _required_text(
                    row,
                    "name",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
                "taxonomy_type": _required_text(
                    row,
                    "taxonomy_type",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
                "purpose": _optional_text(
                    row,
                    "purpose",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
                "primary_assignment_scope": primary_scope,
                "planning_enabled": planning_enabled,
                "budgeting_level": _optional_text(
                    row,
                    "budgeting_level",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
                "root_default_target_dimension": target_dimension,
                "status": _required_text(
                    row,
                    "status",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
                "source_template_ref": _optional_text(
                    row,
                    "source_template_ref",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
            }
        )

    nodes: list[dict[str, object]] = []
    nodes_by_id: dict[str, dict[str, object]] = {}
    for row in node_rows:
        record_type = "taxonomy_node"
        node_id = _required_text(
            row,
            "taxonomy_node_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        taxonomy_id = _required_text(
            row,
            "taxonomy_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        sort_order = row.get("sort_order")
        is_terminal = row.get("is_terminal")
        target_dimension = _required_text(
            row,
            "default_target_dimension",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        if (
            taxonomy_id not in taxonomy_ids
            or node_id in nodes_by_id
            or not isinstance(sort_order, int)
            or isinstance(sort_order, bool)
            or not isinstance(is_terminal, bool)
            or target_dimension not in {"weight", "risk_budget"}
        ):
            raise _published_integrity_error(
                portfolio_id,
                reason="sealed_taxonomy_referential_integrity_invalid",
                record_type=record_type,
                taxonomy_id=taxonomy_id,
                taxonomy_node_id=node_id,
            )
        normalized = {
            "taxonomy_node_id": node_id,
            "taxonomy_id": taxonomy_id,
            "parent_taxonomy_node_id": _optional_text(
                row,
                "parent_taxonomy_node_id",
                portfolio_id=portfolio_id,
                record_type=record_type,
            ),
            "node_name": _required_text(
                row,
                "node_name",
                portfolio_id=portfolio_id,
                record_type=record_type,
            ),
            "node_code": _optional_text(
                row,
                "node_code",
                portfolio_id=portfolio_id,
                record_type=record_type,
            ),
            "sort_order": sort_order,
            "is_terminal": is_terminal,
            "default_target_dimension": target_dimension,
            "status": _required_text(
                row,
                "status",
                portfolio_id=portfolio_id,
                record_type=record_type,
            ),
        }
        nodes.append(normalized)
        nodes_by_id[node_id] = normalized

    for node in nodes:
        parent_id = node["parent_taxonomy_node_id"]
        if parent_id is None:
            continue
        parent = nodes_by_id.get(str(parent_id))
        if parent is None or parent["taxonomy_id"] != node["taxonomy_id"]:
            raise _published_integrity_error(
                portfolio_id,
                reason="sealed_taxonomy_referential_integrity_invalid",
                record_type="taxonomy_node",
                taxonomy_node_id=node["taxonomy_node_id"],
            )

    assignments: list[dict[str, object]] = []
    assignment_ids: set[str] = set()
    assignment_targets: set[tuple[str, str, str]] = set()
    for row in assignment_rows:
        record_type = "taxonomy_assignment"
        assignment_id = _required_text(
            row,
            "assignment_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        taxonomy_id = _required_text(
            row,
            "taxonomy_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        node_id = _required_text(
            row,
            "taxonomy_node_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        target_scope = _required_text(
            row,
            "target_scope",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        target_entity_id = _required_text(
            row,
            "target_entity_id",
            portfolio_id=portfolio_id,
            record_type=record_type,
        )
        target_key = (taxonomy_id, target_scope, target_entity_id)
        node = nodes_by_id.get(node_id)
        if (
            taxonomy_id not in taxonomy_ids
            or node is None
            or node["taxonomy_id"] != taxonomy_id
            or target_scope not in {"instrument", "account", "cash_bucket"}
            or assignment_id in assignment_ids
            or target_key in assignment_targets
        ):
            raise _published_integrity_error(
                portfolio_id,
                reason="sealed_taxonomy_referential_integrity_invalid",
                record_type=record_type,
                assignment_id=assignment_id,
            )
        assignment_ids.add(assignment_id)
        assignment_targets.add(target_key)
        assignments.append(
            {
                "assignment_id": assignment_id,
                "taxonomy_id": taxonomy_id,
                "target_scope": target_scope,
                "target_entity_id": target_entity_id,
                "taxonomy_node_id": node_id,
                "status": _required_text(
                    row,
                    "status",
                    portfolio_id=portfolio_id,
                    record_type=record_type,
                ),
            }
        )

    default_taxonomy = config.get("default_planning_taxonomy_id")
    if default_taxonomy is not None and (
        not isinstance(default_taxonomy, str)
        or not default_taxonomy.strip()
        or default_taxonomy != default_taxonomy.strip()
        or default_taxonomy not in taxonomy_ids
    ):
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_taxonomy_referential_integrity_invalid",
            record_type="default_planning_taxonomy",
        )

    return {
        "default_planning_taxonomy_id": default_taxonomy,
        "taxonomies": taxonomies,
        "taxonomy_nodes": nodes,
        "taxonomy_assignments": assignments,
    }


def _sealed_portfolio_config(
    session: Session,
    *,
    manifest_id: object,
    portfolio_id: str,
) -> dict[str, object]:
    row = (
        session.execute(
            select(
                portfolio_daily_config_input.c.portfolio_id,
                portfolio_daily_config_input.c.base_currency,
                portfolio_daily_config_input.c.valuation_timezone,
                portfolio_daily_config_input.c.operating_profile,
                portfolio_daily_config_input.c.canonical_config,
            ).where(
                portfolio_daily_config_input.c.manifest_id == manifest_id,
                portfolio_daily_config_input.c.portfolio_id == portfolio_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not isinstance(row["canonical_config"], dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "published_calculation_integrity_error",
                "portfolio_id": portfolio_id,
                "reason": "sealed_portfolio_config_missing",
            },
        )
    canonical_config = dict(row["canonical_config"])
    try:
        operating_profile = require_portfolio_operating_profile(
            row["operating_profile"],
            context="Sealed portfolio config",
        )
    except ValueError as error:
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_operating_profile_invalid",
        ) from error
    if canonical_config.get("operating_profile") != operating_profile:
        raise _published_integrity_error(
            portfolio_id,
            reason="sealed_operating_profile_mismatch",
        )
    return {
        "portfolio_id": str(row["portfolio_id"]),
        "base_currency": str(row["base_currency"]),
        "valuation_timezone": str(row["valuation_timezone"]),
        **canonical_config,
        "operating_profile": operating_profile,
    }


def _decimal_or_none(value: object, *, field_name: str) -> str | None:
    return (
        None if value is None else canonical_decimal(value, field_name=field_name)  # type: ignore[arg-type]
    )


def _sealed_instrument_configs(
    session: Session,
    *,
    manifest_id: object,
    instrument_ids: set[str],
) -> dict[str, dict[str, object]]:
    if not instrument_ids:
        return {}
    rows = session.execute(
        select(
            portfolio_daily_instrument_input.c.instrument_id,
            portfolio_daily_instrument_input.c.canonical_instrument,
        ).where(
            portfolio_daily_instrument_input.c.manifest_id == manifest_id,
            portfolio_daily_instrument_input.c.instrument_id.in_(
                sorted(instrument_ids)
            ),
        )
    ).mappings()
    result = {
        str(row["instrument_id"]): dict(row["canonical_instrument"])
        for row in rows
        if isinstance(row["canonical_instrument"], dict)
    }
    missing = sorted(instrument_ids - set(result))
    if missing:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "published_calculation_integrity_error",
                "reason": "sealed_instrument_config_missing",
                "instrument_ids": missing,
            },
        )
    return result


def _sum_optional(
    rows: tuple[PortfolioDailyHolding, ...],
    field_name: str,
) -> Decimal | None:
    values = tuple(getattr(row, field_name) for row in rows)
    if any(value is None for value in values):
        return None
    return exact_decimal_sum(
        tuple(value for value in values if isinstance(value, Decimal))
    )


def _sum_lot_exact(
    rows: tuple[PortfolioDailyLot, ...],
    field_name: str,
) -> Decimal | None:
    values = tuple(getattr(row, field_name) for row in rows)
    if any(value is None for value in values):
        return None
    return exact_decimal_sum(
        tuple(value for value in values if isinstance(value, Decimal))
    )


def _instrument_contributions(
    publication: CurrentPortfolioDailyPublication,
    *,
    as_of_date: date,
) -> dict[str, PortfolioDailyContribution]:
    result: dict[str, PortfolioDailyContribution] = {}
    for row in publication.contributions:
        if row.as_of_date != as_of_date or row.axis != "instrument":
            continue
        if row.group_key in result:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "published_calculation_integrity_error",
                    "reason": "duplicate_instrument_contribution",
                    "instrument_id": row.group_key,
                },
            )
        result[row.group_key] = row
    return result


def _uniform_optional(
    rows: tuple[PortfolioDailyHolding, ...],
    field_name: str,
) -> object | None:
    values = {getattr(row, field_name) for row in rows}
    return next(iter(values)) if len(values) == 1 else None


def _published_holdings_workspace(
    session: Session,
    publication: CurrentPortfolioDailyPublication,
    *,
    instrument_id: str | None = None,
) -> dict[str, object]:
    if len(publication.snapshots) != 1:
        raise calculation_not_ready(
            publication.metadata.portfolio_id,
            end_date=publication.requested_range_end,
            reason="published_snapshot_missing",
        )
    snapshot = publication.snapshots[0]
    config = _sealed_portfolio_config(
        session,
        manifest_id=publication.metadata.manifest_id,
        portfolio_id=publication.metadata.portfolio_id,
    )
    sealed_taxonomy = _sealed_taxonomy_display_config(
        config,
        portfolio_id=publication.metadata.portfolio_id,
    )
    selected_holdings = tuple(
        row
        for row in publication.holdings
        if instrument_id is None or row.instrument_id == instrument_id
    )
    instruments = _sealed_instrument_configs(
        session,
        manifest_id=publication.metadata.manifest_id,
        instrument_ids={row.instrument_id for row in selected_holdings},
    )
    lots_by_instrument: dict[str, list[PortfolioDailyLot]] = defaultdict(list)
    for lot in publication.lots:
        lots_by_instrument[lot.instrument_id].append(lot)
    contributions = _instrument_contributions(
        publication,
        as_of_date=snapshot.as_of_date,
    )
    grouped: dict[str, list[PortfolioDailyHolding]] = defaultdict(list)
    for holding in selected_holdings:
        grouped[holding.instrument_id].append(holding)

    rows: list[dict[str, object]] = []
    for resolved_instrument_id in sorted(grouped):
        group = tuple(grouped[resolved_instrument_id])
        quantity = exact_decimal_sum(tuple(row.quantity_exact for row in group))
        market_value_local = _sum_optional(group, "market_value_local_exact")
        market_value_base = _sum_optional(group, "market_value_base_exact")
        instrument_lots = tuple(lots_by_instrument[resolved_instrument_id])
        cost_basis_local = (
            _sum_lot_exact(instrument_lots, "cost_basis_local_exact")
            if instrument_lots
            else None
        )
        cost_basis_base = (
            _sum_lot_exact(instrument_lots, "cost_basis_base_exact")
            if instrument_lots
            and all(row.measured_base_cost for row in instrument_lots)
            else None
        )
        unrealized_pnl_local = (
            exact_decimal_subtract(market_value_local, cost_basis_local)
            if market_value_local is not None and cost_basis_local is not None
            else None
        )
        unrealized_pnl_base = (
            exact_decimal_subtract(market_value_base, cost_basis_base)
            if market_value_base is not None and cost_basis_base is not None
            else None
        )
        contribution = contributions.get(resolved_instrument_id)
        economic_pnl = (
            contribution.economic_pnl_exact
            if contribution is not None and contribution.measured
            else None
        )
        return_contribution = (
            exact_decimal_sum(
                (
                    contribution.contribution_method50,
                    contribution.contribution_division_adjustment_exact,
                )
            )
            if contribution is not None
            and contribution.measured
            and contribution.contribution_method50 is not None
            else None
        )
        allocation = _sum_optional(group, "portfolio_weight")
        coverage_rank = {"complete": 0, "partial": 1, "unavailable": 2}
        coverage = max(
            (row.valuation_coverage_state for row in group),
            key=coverage_rank.__getitem__,
        )
        reason_codes = list(
            dict.fromkeys(
                reason
                for row in group
                for reason in (
                    *row.valuation_coverage_reason_codes,
                    *row.valuation_reason_codes,
                )
            )
        )
        if contribution is None:
            reason_codes.append("instrument_contribution_missing")
        elif not contribution.measured:
            reason_codes.extend(contribution.reason_codes)
        reason_codes = list(dict.fromkeys(reason_codes))
        rows.append(
            {
                "line_id": resolved_instrument_id,
                "instrument_core": instruments[resolved_instrument_id],
                "quantity": canonical_decimal(quantity, field_name="quantity"),
                "last_price": _decimal_or_none(
                    _uniform_optional(group, "adopted_price_exact"),
                    field_name="adopted_price_exact",
                ),
                "quote_as_of_date": snapshot.as_of_date.isoformat(),
                "quote_status": _uniform_optional(
                    group,
                    "valuation_endpoint_status",
                ),
                "market_value": _decimal_or_none(
                    market_value_local,
                    field_name="market_value_local_exact",
                ),
                "market_value_base": _decimal_or_none(
                    market_value_base,
                    field_name="market_value_base_exact",
                ),
                "day_change_pct": None,
                # Instrument attribution is published in portfolio base
                # currency.  Do not mislabel it as an instrument-local amount.
                "day_change_value": None,
                "day_change_value_base": _decimal_or_none(
                    economic_pnl,
                    field_name="instrument_economic_pnl_exact",
                ),
                "portfolio_return_contribution": _decimal_or_none(
                    return_contribution,
                    field_name="instrument_contribution_effective",
                ),
                "cost_basis_method": None,
                "cost_basis": _decimal_or_none(
                    cost_basis_local,
                    field_name="cost_basis_local",
                ),
                "cost_basis_base": _decimal_or_none(
                    cost_basis_base,
                    field_name="cost_basis_base",
                ),
                "unrealized_pnl": _decimal_or_none(
                    unrealized_pnl_local,
                    field_name="unrealized_pnl_local_exact",
                ),
                "unrealized_pnl_base": _decimal_or_none(
                    unrealized_pnl_base,
                    field_name="unrealized_pnl_ending_base",
                ),
                "unrealized_return": None,
                "allocation": _decimal_or_none(
                    allocation,
                    field_name="portfolio_weight",
                ),
                "price_chart_1m": [],
                "price_chart_3m": [],
                "price_chart_6m": [],
                "price_chart_1y": [],
                "coverage_status": coverage,
                "coverage_reason_codes": reason_codes,
                "account_ids": sorted({row.account_id for row in group}),
                "account_count": len({row.account_id for row in group}),
                "open_position_lot_count": len(instrument_lots),
            }
        )

    position_market_value = _sum_optional(
        selected_holdings,
        "market_value_base_exact",
    )
    pending_settlement = (
        exact_decimal_subtract(
            snapshot.pending_receivable,
            snapshot.pending_payable,
        )
        if snapshot.pending_receivable is not None
        and snapshot.pending_payable is not None
        else None
    )
    selected_lots = tuple(
        lot
        for lot in publication.lots
        if instrument_id is None or lot.instrument_id == instrument_id
    )
    total_cost_basis = (
        _sum_lot_exact(selected_lots, "cost_basis_base_exact")
        if selected_lots and all(row.measured_base_cost for row in selected_lots)
        else None
    )
    total_unrealized = (
        exact_decimal_subtract(position_market_value, total_cost_basis)
        if position_market_value is not None and total_cost_basis is not None
        else None
    )
    total_allocation = _sum_optional(
        selected_holdings,
        "portfolio_weight",
    )
    return {
        "portfolio_id": publication.metadata.portfolio_id,
        "portfolio_name": str(
            config.get("portfolio_name") or publication.metadata.portfolio_id
        ),
        "base_currency": snapshot.base_currency,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "coverage_note": (
            "Financial values are read from one immutable Portfolio Daily "
            "publication; display metadata is read from its sealed manifest."
        ),
        "quality_warnings": list(snapshot.valuation_reason_codes),
        "publication": publication_metadata_response(publication.metadata).model_dump(
            mode="json"
        ),
        "sealed_display_config": {
            "taxonomy": sealed_taxonomy,
        },
        "summary_cards": [
            {"label": "Instruments", "value": str(len(rows)), "tone": "neutral"},
            {
                "label": "Account Lines",
                "value": str(len(selected_holdings)),
                "tone": "neutral",
            },
            {
                "label": "Measured Lines",
                "value": (
                    f"{sum(1 for row in selected_holdings if row.measured_market_value)} "
                    f"/ {len(selected_holdings)}"
                ),
                "tone": "neutral",
            },
            {
                "label": "Coverage",
                "value": snapshot.nav_coverage_state,
                "tone": "neutral",
            },
        ],
        "rows": rows,
        "totals": {
            "market_value": _decimal_or_none(
                position_market_value,
                field_name="position_market_value",
            ),
            "cash_balance": _decimal_or_none(
                snapshot.settled_cash,
                field_name="settled_cash",
            ),
            "pending_settlement": _decimal_or_none(
                pending_settlement,
                field_name="pending_settlement",
            ),
            "nav": _decimal_or_none(
                snapshot.closing_nav,
                field_name="closing_nav",
            ),
            "day_change_pct": _decimal_or_none(
                snapshot.subperiod_twr_published,
                field_name="subperiod_twr_published",
            ),
            "day_change_value": _decimal_or_none(
                snapshot.economic_pnl,
                field_name="economic_pnl",
            ),
            "cost_basis": _decimal_or_none(
                total_cost_basis,
                field_name="cost_basis_base",
            ),
            "unrealized_pnl_base": _decimal_or_none(
                total_unrealized,
                field_name="unrealized_pnl_ending_base",
            ),
            "unrealized_return": None,
            "allocation": _decimal_or_none(
                total_allocation,
                field_name="portfolio_weight",
            ),
        },
    }


@router.get("/summary", response_model=PortfolioWorkspaceSummaryResponse)
def workspace_summary(
    portfolio_id: str | None = None,
    as_of_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> PortfolioWorkspaceSummaryResponse:
    if portfolio_id is None or not portfolio_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "portfolio_id_required"},
        )
    publication = read_published_latest(
        session,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        tables=("snapshots", "holdings"),
    )
    if len(publication.snapshots) != 1:
        raise calculation_not_ready(
            portfolio_id,
            end_date=as_of_date,
            reason="published_snapshot_missing",
        )
    snapshot = publication.snapshots[0]
    config = _sealed_portfolio_config(
        session,
        manifest_id=publication.metadata.manifest_id,
        portfolio_id=portfolio_id,
    )
    instrument_count = len({row.instrument_id for row in publication.holdings})
    operating_profile = require_portfolio_operating_profile(
        config.get("operating_profile"),
        context="Sealed portfolio config",
    )
    sections = [
        {"label": "Holdings", "href": "/holdings", "status": "published"},
        {"label": "Performance", "href": "/performance", "status": "published"},
        {"label": "Risk", "href": "/risk", "status": "separate-calculation"},
        {"label": "Transactions", "href": "/transactions", "status": "facts"},
        {"label": "Accounts", "href": "/accounts", "status": "facts"},
        {"label": "Taxonomies", "href": "/taxonomies", "status": "facts"},
    ]
    if operating_profile == "standard_taxonomy":
        sections.append(
            {
                "label": "Allocation Lab",
                "href": "/allocation-research",
                "status": "separate-calculation",
            }
        )
    return PortfolioWorkspaceSummaryResponse.model_validate(
        {
            "portfolio_id": portfolio_id,
            "portfolio_name": str(config.get("portfolio_name") or portfolio_id),
            "base_currency": snapshot.base_currency,
            "operating_profile": operating_profile,
            "valuation_timezone": str(config["valuation_timezone"]),
            "as_of_date": snapshot.as_of_date.isoformat(),
            "measured_nav": snapshot.measured_nav,
            "nav": snapshot.closing_nav,
            "economic_pnl": snapshot.economic_pnl,
            "subperiod_twr_method50": snapshot.subperiod_twr_method50,
            "subperiod_twr_published": snapshot.subperiod_twr_published,
            "return_period_start_date": (
                snapshot.return_period_start_date.isoformat()
                if snapshot.return_period_start_date is not None
                else None
            ),
            "return_period_end_date": (
                snapshot.return_period_end_date.isoformat()
                if snapshot.return_period_end_date is not None
                else None
            ),
            "return_period_day_count": snapshot.return_period_day_count,
            "instrument_count": instrument_count,
            "nav_coverage_state": snapshot.nav_coverage_state,
            "nav_reason_codes": list(snapshot.nav_reason_codes),
            "book_pnl_coverage_state": snapshot.book_pnl_coverage_state,
            "book_pnl_reason_codes": list(snapshot.book_pnl_reason_codes),
            "return_coverage_state": snapshot.return_coverage_state,
            "return_reason_codes": list(snapshot.return_reason_codes),
            "valuation_endpoint_status": snapshot.valuation_endpoint_status,
            "valuation_reason_codes": list(snapshot.valuation_reason_codes),
            "default_planning_taxonomy_id": config.get("default_planning_taxonomy_id"),
            "publication": publication_metadata_response(
                publication.metadata
            ).model_dump(mode="json"),
            "toolbar_label": "View: Portfolio Summary",
            "sections": sections,
        }
    )


@router.get("/holdings")
def holdings_workspace(
    portfolio_id: str | None = None,
    as_of_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if portfolio_id is None or not portfolio_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "portfolio_id_required"},
        )
    publication = read_published_latest(
        session,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        tables=("snapshots", "holdings", "lots", "contributions"),
    )
    return _published_holdings_workspace(session, publication)


@router.get("/holdings/instrument")
def instrument_holding_projection(
    portfolio_id: str | None = None,
    instrument_id: str | None = None,
    as_of_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if portfolio_id is None or not portfolio_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "portfolio_id_required"},
        )
    if instrument_id is None or not instrument_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "instrument_id_required"},
        )
    publication = read_published_latest(
        session,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        tables=("snapshots", "holdings", "lots", "contributions"),
    )
    workspace = _published_holdings_workspace(
        session,
        publication,
        instrument_id=instrument_id,
    )
    rows = workspace["rows"]
    row = rows[0] if isinstance(rows, list) and rows else None
    return {
        "portfolio_id": workspace["portfolio_id"],
        "portfolio_name": workspace["portfolio_name"],
        "base_currency": workspace["base_currency"],
        "as_of_date": workspace["as_of_date"],
        "view_label": workspace["view_label"],
        "publication": workspace["publication"],
        "quality_warnings": workspace["quality_warnings"],
        "row": row,
    }


__all__: list[str] = ["router"]
