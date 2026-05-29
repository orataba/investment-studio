from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, cast, delete, func, or_, select
from yungu_instrument_core.db_models import InstrumentMarketData

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
    PortfolioInstrumentUniverseRecordModel,
    PortfolioRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.ledger import build_account_workspace, build_position_lots
from portfolio_app.services.snapshot_selection import default_portfolio_snapshot

EMPTY_STORE: dict[str, list[dict[str, Any]]] = {
    "portfolios": [],
    "accounts": [],
    "transactions": [],
    "taxonomies": [],
    "taxonomy_nodes": [],
    "taxonomy_assignments": [],
    "instrument_universe": [],
    "target_sets": [],
    "target_set_lines": [],
}

UNSET = object()
TARGET_SET_EPSILON = 0.0005
TARGET_MEMBER_NODE = "taxonomy_node"
LEGACY_ASSET_REFERENCE_KEYS = {"asset_id", "asset_name", "asset_type"}
INSTRUMENT_REF_REQUIRED_KEYS = {"instrument_id", "instrument_name", "instrument_type", "currency"}


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time_component(value: object) -> time | None:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            parsed = time.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed.replace(second=0, microsecond=0)
    return None


def _format_trade_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _validate_instrument_ref_contract(
    instrument_ref: dict[str, object],
    *,
    context: str,
    expected_instrument_id: str | None = None,
) -> None:
    legacy_keys = sorted(key for key in LEGACY_ASSET_REFERENCE_KEYS if key in instrument_ref)
    if legacy_keys:
        raise ValueError(f"{context} uses legacy asset reference fields: {', '.join(legacy_keys)}")
    missing_keys = sorted(key for key in INSTRUMENT_REF_REQUIRED_KEYS if not instrument_ref.get(key))
    if missing_keys:
        raise ValueError(f"{context} is missing instrument reference fields: {', '.join(missing_keys)}")
    if expected_instrument_id and str(instrument_ref["instrument_id"]) != expected_instrument_id:
        raise ValueError(f"{context} instrument_ref.instrument_id must match instrument_id")
    identifiers = instrument_ref.get("identifiers")
    if identifiers is not None and not isinstance(identifiers, list):
        raise ValueError(f"{context} instrument_ref.identifiers must be a list")


def resolve_trade_timing(
    *,
    trade_date: date,
    trade_time: str | None = None,
    trade_timezone: str | None = None,
    trade_time_is_estimated: bool | None = None,
) -> dict[str, object]:
    settings = get_settings()
    resolved_timezone = (trade_timezone or settings.default_trade_timezone).strip() or settings.default_trade_timezone
    resolved_time = _parse_time_component(trade_time)
    is_estimated = bool(trade_time_is_estimated) if trade_time_is_estimated is not None else False
    if resolved_time is None:
        resolved_time = _parse_time_component(settings.default_trade_time) or time(hour=12, minute=0)
        is_estimated = True

    trade_at = datetime.combine(
        trade_date,
        resolved_time,
        tzinfo=ZoneInfo(resolved_timezone),
    )
    return {
        "trade_time": _format_trade_time(resolved_time),
        "trade_at": _utc_isoformat(trade_at),
        "trade_timezone": resolved_timezone,
        "trade_time_is_estimated": is_estimated,
    }


def _normalize_store(store: dict[str, object]) -> dict[str, object]:
    normalized = deepcopy(EMPTY_STORE)
    for key in (
        "portfolios",
        "accounts",
        "transactions",
        "taxonomies",
        "taxonomy_nodes",
        "taxonomy_assignments",
        "instrument_universe",
        "target_sets",
        "target_set_lines",
    ):
        value = store.get(key)
        if isinstance(value, list):
            normalized[key] = value
    for portfolio in normalized["portfolios"]:
        if isinstance(portfolio, dict):
            portfolio.setdefault("valuation_timezone", "Asia/Shanghai")
            portfolio.setdefault("valuation_cutoff_policy", "latest_complete_eod")
            portfolio.setdefault("default_planning_taxonomy_id", None)
    for account in normalized["accounts"]:
        if not isinstance(account, dict):
            continue
        if "allowed_asset_types" in account:
            raise ValueError(
                f"Account '{account.get('account_id')}' uses legacy allowed_asset_types; use allowed_instrument_types."
            )
        if "allowed_instrument_types" not in account:
            account["allowed_instrument_types"] = None
    for transaction in normalized["transactions"]:
        if not isinstance(transaction, dict):
            continue
        if "asset_id" in transaction:
            raise ValueError(
                f"Transaction '{transaction.get('transaction_id')}' uses legacy asset_id; use instrument_id."
            )
        instrument_id = str(transaction.get("instrument_id") or "").strip()
        instrument_ref = transaction.get("instrument_ref")
        if isinstance(instrument_ref, dict):
            _validate_instrument_ref_contract(
                instrument_ref,
                context=f"Transaction '{transaction.get('transaction_id')}'",
                expected_instrument_id=instrument_id or None,
            )
        elif instrument_id:
            raise ValueError(f"Transaction '{transaction.get('transaction_id')}' with instrument_id requires instrument_ref.")
        if "counter_amount" not in transaction:
            transaction["counter_amount"] = None
        if "fx_rate" not in transaction:
            transaction["fx_rate"] = None
        trade_date_value = transaction.get("trade_date")
        try:
            resolved_trade_date = (
                trade_date_value if isinstance(trade_date_value, date) else date.fromisoformat(str(trade_date_value))
            )
        except ValueError:
            resolved_trade_date = date.today()
        resolved_timing = resolve_trade_timing(
            trade_date=resolved_trade_date,
            trade_time=str(transaction.get("trade_time") or "").strip() or None,
            trade_timezone=str(transaction.get("trade_timezone") or "").strip() or None,
            trade_time_is_estimated=(
                bool(transaction.get("trade_time_is_estimated"))
                if transaction.get("trade_time_is_estimated") is not None
                else None
            ),
        )
        transaction["trade_time"] = resolved_timing["trade_time"]
        transaction["trade_at"] = resolved_timing["trade_at"]
        transaction["trade_timezone"] = resolved_timing["trade_timezone"]
        transaction["trade_time_is_estimated"] = resolved_timing["trade_time_is_estimated"]
    for universe_record in normalized["instrument_universe"]:
        if not isinstance(universe_record, dict):
            continue
        instrument_id = str(universe_record.get("instrument_id") or "").strip()
        instrument_ref = universe_record.get("instrument_ref")
        if isinstance(instrument_ref, dict):
            _validate_instrument_ref_contract(
                instrument_ref,
                context=f"Instrument universe '{instrument_id}'",
                expected_instrument_id=instrument_id or None,
            )
    return normalized


def reset_store(data: dict[str, object] | None = None) -> None:
    payload = data if data is not None else EMPTY_STORE
    normalized = _normalize_store(deepcopy(payload))
    session_factory = get_session_factory()
    with session_factory() as session:
        _save_store_to_db(session, normalized)
        session.commit()


def _load_store_from_db(session) -> dict[str, object]:
    portfolios = session.scalars(
        select(PortfolioRecordModel).order_by(
            PortfolioRecordModel.sort_order,
            PortfolioRecordModel.portfolio_name,
            PortfolioRecordModel.portfolio_id,
        )
    ).all()
    accounts = session.scalars(
        select(AccountRecordModel).order_by(
            AccountRecordModel.portfolio_id,
            AccountRecordModel.account_id,
        )
    ).all()
    transactions = session.scalars(
        select(TransactionRecordModel).order_by(
            TransactionRecordModel.trade_date,
            TransactionRecordModel.trade_at,
            TransactionRecordModel.created_at,
            TransactionRecordModel.transaction_id,
            TransactionRecordModel.settlement_date,
        )
    ).all()
    taxonomies = session.scalars(
        select(TaxonomyRecordModel).order_by(
            TaxonomyRecordModel.portfolio_id,
            TaxonomyRecordModel.name,
            TaxonomyRecordModel.taxonomy_id,
        )
    ).all()
    taxonomy_nodes = session.scalars(
        select(TaxonomyNodeRecordModel).order_by(
            TaxonomyNodeRecordModel.taxonomy_id,
            TaxonomyNodeRecordModel.sort_order,
            TaxonomyNodeRecordModel.node_name,
            TaxonomyNodeRecordModel.taxonomy_node_id,
        )
    ).all()
    taxonomy_assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel).order_by(
            TaxonomyAssignmentRecordModel.taxonomy_id,
            TaxonomyAssignmentRecordModel.target_scope,
            TaxonomyAssignmentRecordModel.target_entity_id,
            TaxonomyAssignmentRecordModel.assignment_id,
        )
    ).all()
    target_sets = session.scalars(
        select(TargetSetRecordModel).order_by(
            TargetSetRecordModel.taxonomy_id,
            TargetSetRecordModel.comparator_taxonomy_node_id,
            TargetSetRecordModel.target_set_type,
            TargetSetRecordModel.target_set_id,
        )
    ).all()
    instrument_universe_records = session.scalars(
        select(PortfolioInstrumentUniverseRecordModel).order_by(
            PortfolioInstrumentUniverseRecordModel.portfolio_id,
            PortfolioInstrumentUniverseRecordModel.instrument_id,
        )
    ).all()
    target_set_lines = session.scalars(
        select(TargetSetLineRecordModel).order_by(
            TargetSetLineRecordModel.target_set_id,
            TargetSetLineRecordModel.target_member_type,
            TargetSetLineRecordModel.target_member_id,
            TargetSetLineRecordModel.target_line_id,
        )
    ).all()
    return {
        "portfolios": [
            {
                "portfolio_id": item.portfolio_id,
                "portfolio_name": item.portfolio_name,
                "base_currency": item.base_currency,
                "valuation_timezone": item.valuation_timezone,
                "valuation_cutoff_policy": item.valuation_cutoff_policy,
                "as_of_date": item.as_of_date.isoformat() if item.as_of_date is not None else None,
                "nav": item.nav,
                "day_change_value": item.day_change_value,
                "day_change_pct": item.day_change_pct,
                "securities_count": item.securities_count,
                "sort_order": item.sort_order,
                "default_planning_taxonomy_id": item.default_planning_taxonomy_id,
            }
            for item in portfolios
        ],
        "accounts": [
            {
                "account_id": item.account_id,
                "portfolio_id": item.portfolio_id,
                "account_name": item.account_name,
                "account_type": item.account_type,
                "currency": item.currency,
                "institution": item.institution,
                "default_settlement_cash_account_id": item.default_settlement_cash_account_id,
                "cost_basis_method": item.cost_basis_method,
                "allowed_instrument_types": deepcopy(item.allowed_instrument_types_json),
                "opened_at": item.opened_at.isoformat() if item.opened_at is not None else None,
                "closed_at": item.closed_at.isoformat() if item.closed_at is not None else None,
                "status": item.status,
            }
            for item in accounts
        ],
        "transactions": [
            {
                "transaction_id": item.transaction_id,
                "portfolio_id": item.portfolio_id,
                "transaction_type": item.transaction_type,
                "trade_date": item.trade_date.isoformat(),
                "trade_time": item.trade_time,
                "trade_at": item.trade_at,
                "trade_timezone": item.trade_timezone,
                "trade_time_is_estimated": item.trade_time_is_estimated,
                "settlement_date": item.settlement_date.isoformat(),
                "entitlement_date": item.entitlement_date.isoformat()
                if item.entitlement_date is not None
                else None,
                "acquisition_date": item.acquisition_date.isoformat()
                if item.acquisition_date is not None
                else None,
                "account_id": item.account_id,
                "settlement_cash_account_id": item.settlement_cash_account_id,
                "instrument_id": item.instrument_id,
                "instrument_ref": deepcopy(item.instrument_ref_json),
                "quantity": item.quantity,
                "price": item.price,
                "gross_amount": item.gross_amount,
                "counter_amount": item.counter_amount,
                "fx_rate": item.fx_rate,
                "fees": item.fees,
                "taxes": item.taxes,
                "currency": item.currency,
                "transfer_scope": item.transfer_scope,
                "transfer_object_type": item.transfer_object_type,
                "transfer_group_id": item.transfer_group_id,
                "counterparty_account_id": item.counterparty_account_id,
                "note": item.note,
                "created_at": item.created_at,
            }
            for item in transactions
        ],
        "taxonomies": [
            {
                "taxonomy_id": item.taxonomy_id,
                "portfolio_id": item.portfolio_id,
                "name": item.name,
                "taxonomy_type": item.taxonomy_type,
                "purpose": item.purpose,
                "primary_assignment_scope": item.primary_assignment_scope,
                "planning_enabled": item.planning_enabled,
                "budgeting_level": item.budgeting_level,
                "status": item.status,
                "source_template_ref": item.source_template_ref,
            }
            for item in taxonomies
        ],
        "taxonomy_nodes": [
            {
                "taxonomy_node_id": item.taxonomy_node_id,
                "taxonomy_id": item.taxonomy_id,
                "parent_taxonomy_node_id": item.parent_taxonomy_node_id,
                "node_name": item.node_name,
                "node_code": item.node_code,
                "sort_order": item.sort_order,
                "is_terminal": item.is_terminal,
                "default_target_dimension": item.default_target_dimension,
                "status": item.status,
            }
            for item in taxonomy_nodes
        ],
        "taxonomy_assignments": [
            {
                "assignment_id": item.assignment_id,
                "taxonomy_id": item.taxonomy_id,
                "target_scope": item.target_scope,
                "target_entity_id": item.target_entity_id,
                "taxonomy_node_id": item.taxonomy_node_id,
                "status": item.status,
            }
            for item in taxonomy_assignments
        ],
        "instrument_universe": [
            _serialize_portfolio_instrument_universe_row(item)
            for item in instrument_universe_records
        ],
        "target_sets": [
            {
                "target_set_id": item.target_set_id,
                "taxonomy_id": item.taxonomy_id,
                "comparator_taxonomy_node_id": item.comparator_taxonomy_node_id,
                "target_set_type": item.target_set_type,
                "name": item.name,
                "weight_enabled": item.weight_enabled,
                "risk_budget_enabled": item.risk_budget_enabled,
                "status": item.status,
                "notes": item.notes,
            }
            for item in target_sets
        ],
        "target_set_lines": [
            {
                "target_line_id": item.target_line_id,
                "target_set_id": item.target_set_id,
                "target_member_type": item.target_member_type,
                "target_member_id": item.target_member_id,
                "taxonomy_node_id": item.taxonomy_node_id,
                "target_weight": item.target_weight,
                "target_risk_share": item.target_risk_share,
                "notes": item.notes,
            }
            for item in target_set_lines
        ],
    }


def _save_store_to_db(session, data: dict[str, object]) -> None:
    normalized = _normalize_store(data)

    session.execute(delete(PortfolioDailyContributionSliceModel))
    session.execute(delete(PortfolioDailyHoldingSnapshotModel))
    session.execute(delete(PortfolioDailySnapshotModel))
    session.execute(delete(PortfolioCalculationStateModel))
    session.execute(delete(PortfolioInstrumentUniverseRecordModel))
    session.execute(delete(TargetSetLineRecordModel))
    session.execute(delete(TargetSetRecordModel))
    session.execute(delete(TaxonomyAssignmentRecordModel))
    session.execute(delete(TaxonomyNodeRecordModel))
    session.execute(delete(TaxonomyRecordModel))
    session.execute(delete(TransactionRecordModel))
    session.execute(delete(AccountRecordModel))
    session.execute(delete(PortfolioRecordModel))

    for raw_portfolio in list(normalized.get("portfolios", [])):
        if not isinstance(raw_portfolio, dict):
            continue
        as_of_date = raw_portfolio.get("as_of_date")
        session.add(
            PortfolioRecordModel(
                portfolio_id=str(raw_portfolio.get("portfolio_id") or "").strip(),
                portfolio_name=str(raw_portfolio.get("portfolio_name") or "").strip(),
                base_currency=str(raw_portfolio.get("base_currency") or "USD").strip().upper() or "USD",
                valuation_timezone=str(raw_portfolio.get("valuation_timezone") or "Asia/Shanghai").strip()
                or "Asia/Shanghai",
                valuation_cutoff_policy=str(
                    raw_portfolio.get("valuation_cutoff_policy") or "latest_complete_eod"
                ).strip()
                or "latest_complete_eod",
                as_of_date=(
                    date.fromisoformat(str(as_of_date))
                    if as_of_date
                    else None
                ),
                nav=float(raw_portfolio.get("nav") or 0.0),
                day_change_value=float(raw_portfolio.get("day_change_value") or 0.0),
                day_change_pct=float(raw_portfolio.get("day_change_pct") or 0.0),
                securities_count=int(raw_portfolio.get("securities_count") or 0),
                sort_order=int(raw_portfolio.get("sort_order") or 0),
                default_planning_taxonomy_id=(
                    str(raw_portfolio.get("default_planning_taxonomy_id")).strip()
                    if raw_portfolio.get("default_planning_taxonomy_id")
                    else None
                ),
                risk_policy_json=(
                    deepcopy(raw_portfolio.get("risk_policy_json"))
                    if isinstance(raw_portfolio.get("risk_policy_json"), dict)
                    else None
                ),
            )
        )

    for raw_account in list(normalized.get("accounts", [])):
        if not isinstance(raw_account, dict):
            continue
        opened_at = raw_account.get("opened_at")
        closed_at = raw_account.get("closed_at")
        session.add(
            AccountRecordModel(
                account_id=str(raw_account.get("account_id") or "").strip(),
                portfolio_id=str(raw_account.get("portfolio_id") or "").strip(),
                account_name=str(raw_account.get("account_name") or "").strip(),
                account_type=str(raw_account.get("account_type") or "").strip(),
                currency=str(raw_account.get("currency") or "USD").strip().upper() or "USD",
                institution=(str(raw_account.get("institution")).strip() if raw_account.get("institution") else None),
                default_settlement_cash_account_id=(
                    str(raw_account.get("default_settlement_cash_account_id")).strip()
                    if raw_account.get("default_settlement_cash_account_id")
                    else None
                ),
                cost_basis_method=(
                    str(raw_account.get("cost_basis_method")).strip()
                    if raw_account.get("cost_basis_method")
                    else None
                ),
                allowed_instrument_types_json=(
                    sorted(
                        {
                            str(item).strip()
                            for item in raw_account.get("allowed_instrument_types", [])
                            if str(item).strip()
                        }
                    )
                    if isinstance(raw_account.get("allowed_instrument_types"), list)
                    else None
                ),
                opened_at=date.fromisoformat(str(opened_at)) if opened_at else None,
                closed_at=date.fromisoformat(str(closed_at)) if closed_at else None,
                status=str(raw_account.get("status") or "active").strip() or "active",
            )
        )

    for raw_taxonomy in list(normalized.get("taxonomies", [])):
        if not isinstance(raw_taxonomy, dict):
            continue
        session.add(
            TaxonomyRecordModel(
                taxonomy_id=str(raw_taxonomy.get("taxonomy_id") or "").strip(),
                portfolio_id=str(raw_taxonomy.get("portfolio_id") or "").strip(),
                name=str(raw_taxonomy.get("name") or "").strip(),
                taxonomy_type=str(raw_taxonomy.get("taxonomy_type") or "custom").strip() or "custom",
                purpose=(str(raw_taxonomy.get("purpose")).strip() if raw_taxonomy.get("purpose") else None),
                primary_assignment_scope=(
                    str(raw_taxonomy.get("primary_assignment_scope") or "instrument").strip() or "instrument"
                ),
                planning_enabled=bool(raw_taxonomy.get("planning_enabled")),
                budgeting_level=(
                    str(raw_taxonomy.get("budgeting_level")).strip() if raw_taxonomy.get("budgeting_level") else None
                ),
                root_default_target_dimension=(
                    str(raw_taxonomy.get("root_default_target_dimension") or "weight").strip() or "weight"
                ),
                status=str(raw_taxonomy.get("status") or "active").strip() or "active",
                source_template_ref=(
                    str(raw_taxonomy.get("source_template_ref")).strip()
                    if raw_taxonomy.get("source_template_ref")
                    else None
                ),
            )
        )

    for raw_node in list(normalized.get("taxonomy_nodes", [])):
        if not isinstance(raw_node, dict):
            continue
        session.add(
            TaxonomyNodeRecordModel(
                taxonomy_node_id=str(raw_node.get("taxonomy_node_id") or "").strip(),
                taxonomy_id=str(raw_node.get("taxonomy_id") or "").strip(),
                parent_taxonomy_node_id=(
                    str(raw_node.get("parent_taxonomy_node_id")).strip()
                    if raw_node.get("parent_taxonomy_node_id")
                    else None
                ),
                node_name=str(raw_node.get("node_name") or "").strip(),
                node_code=str(raw_node.get("node_code")).strip() if raw_node.get("node_code") else None,
                sort_order=int(raw_node.get("sort_order") or 0),
                is_terminal=bool(raw_node.get("is_terminal", True)),
                default_target_dimension=str(raw_node.get("default_target_dimension") or "weight").strip()
                or "weight",
                status=str(raw_node.get("status") or "active").strip() or "active",
            )
        )

    assignment_rows_by_key: dict[tuple[str, str, str], dict[str, object]] = {}
    for raw_assignment in list(normalized.get("taxonomy_assignments", [])):
        if not isinstance(raw_assignment, dict):
            continue
        assignment_key = (
            str(raw_assignment.get("taxonomy_id") or "").strip(),
            str(raw_assignment.get("target_scope") or "instrument").strip() or "instrument",
            str(raw_assignment.get("target_entity_id") or "").strip(),
        )
        assignment_rows_by_key[assignment_key] = raw_assignment

    for raw_assignment in assignment_rows_by_key.values():
        if not isinstance(raw_assignment, dict):
            continue
        session.add(
            TaxonomyAssignmentRecordModel(
                assignment_id=str(raw_assignment.get("assignment_id") or "").strip(),
                taxonomy_id=str(raw_assignment.get("taxonomy_id") or "").strip(),
                target_scope=str(raw_assignment.get("target_scope") or "instrument").strip() or "instrument",
                target_entity_id=str(raw_assignment.get("target_entity_id") or "").strip(),
                taxonomy_node_id=str(raw_assignment.get("taxonomy_node_id") or "").strip(),
                status=str(raw_assignment.get("status") or "active").strip() or "active",
            )
        )

    for raw_target_set in list(normalized.get("target_sets", [])):
        if not isinstance(raw_target_set, dict):
            continue
        session.add(
            TargetSetRecordModel(
                target_set_id=str(raw_target_set.get("target_set_id") or "").strip(),
                taxonomy_id=str(raw_target_set.get("taxonomy_id") or "").strip(),
                comparator_taxonomy_node_id=(
                    str(raw_target_set.get("comparator_taxonomy_node_id")).strip()
                    if raw_target_set.get("comparator_taxonomy_node_id")
                    else None
                ),
                target_set_type=str(raw_target_set.get("target_set_type") or "saa").strip() or "saa",
                name=str(raw_target_set.get("name") or "").strip(),
                weight_enabled=bool(raw_target_set.get("weight_enabled")),
                risk_budget_enabled=bool(raw_target_set.get("risk_budget_enabled")),
                status=str(raw_target_set.get("status") or "active").strip() or "active",
                notes=str(raw_target_set.get("notes")).strip() if raw_target_set.get("notes") else None,
            )
        )

    for raw_target_line in list(normalized.get("target_set_lines", [])):
        if not isinstance(raw_target_line, dict):
            continue
        session.add(
            TargetSetLineRecordModel(
                target_line_id=str(raw_target_line.get("target_line_id") or "").strip(),
                target_set_id=str(raw_target_line.get("target_set_id") or "").strip(),
                taxonomy_node_id=(
                    str(raw_target_line.get("taxonomy_node_id")).strip()
                    if raw_target_line.get("taxonomy_node_id")
                    else None
                ),
                target_member_type=(
                    str(raw_target_line.get("target_member_type") or TARGET_MEMBER_NODE).strip() or TARGET_MEMBER_NODE
                ),
                target_member_id=(
                    str(raw_target_line.get("target_member_id") or raw_target_line.get("taxonomy_node_id") or "").strip()
                ),
                target_weight=(
                    float(raw_target_line["target_weight"])
                    if raw_target_line.get("target_weight") is not None
                    else None
                ),
                target_risk_share=(
                    float(raw_target_line["target_risk_share"])
                    if raw_target_line.get("target_risk_share") is not None
                    else None
                ),
                notes=str(raw_target_line.get("notes")).strip() if raw_target_line.get("notes") else None,
            )
        )

    for raw_universe_record in list(normalized.get("instrument_universe", [])):
        if not isinstance(raw_universe_record, dict):
            continue
        first_transaction_date = raw_universe_record.get("first_transaction_date")
        last_transaction_date = raw_universe_record.get("last_transaction_date")
        now = _current_utc_timestamp()
        session.add(
            PortfolioInstrumentUniverseRecordModel(
                portfolio_id=str(raw_universe_record.get("portfolio_id") or "").strip(),
                instrument_id=str(raw_universe_record.get("instrument_id") or "").strip(),
                instrument_ref_json=(
                    deepcopy(raw_universe_record.get("instrument_ref"))
                    if isinstance(raw_universe_record.get("instrument_ref"), dict)
                    else None
                ),
                source=str(raw_universe_record.get("source") or "manual").strip() or "manual",
                holding_state=str(raw_universe_record.get("holding_state") or "not_held").strip() or "not_held",
                first_transaction_date=(
                    date.fromisoformat(str(first_transaction_date))
                    if first_transaction_date
                    else None
                ),
                last_transaction_date=(
                    date.fromisoformat(str(last_transaction_date))
                    if last_transaction_date
                    else None
                ),
                transaction_count=int(raw_universe_record.get("transaction_count") or 0),
                status=str(raw_universe_record.get("status") or "active").strip() or "active",
                created_at=str(raw_universe_record.get("created_at") or now).strip(),
                updated_at=str(raw_universe_record.get("updated_at") or now).strip(),
            )
        )

    for raw_transaction in list(normalized.get("transactions", [])):
        if not isinstance(raw_transaction, dict):
            continue
        entitlement_date = raw_transaction.get("entitlement_date")
        acquisition_date = raw_transaction.get("acquisition_date")
        session.add(
            TransactionRecordModel(
                transaction_id=str(raw_transaction.get("transaction_id") or "").strip(),
                portfolio_id=str(raw_transaction.get("portfolio_id") or "").strip(),
                transaction_type=str(raw_transaction.get("transaction_type") or "").strip(),
                trade_date=date.fromisoformat(str(raw_transaction.get("trade_date") or date.today().isoformat())),
                trade_time=str(raw_transaction.get("trade_time") or "12:00").strip() or "12:00",
                trade_at=str(raw_transaction.get("trade_at") or "").strip(),
                trade_timezone=str(raw_transaction.get("trade_timezone") or "").strip(),
                trade_time_is_estimated=bool(raw_transaction.get("trade_time_is_estimated")),
                settlement_date=date.fromisoformat(
                    str(raw_transaction.get("settlement_date") or date.today().isoformat())
                ),
                entitlement_date=date.fromisoformat(str(entitlement_date)) if entitlement_date else None,
                acquisition_date=date.fromisoformat(str(acquisition_date)) if acquisition_date else None,
                account_id=str(raw_transaction.get("account_id") or "").strip(),
                settlement_cash_account_id=(
                    str(raw_transaction.get("settlement_cash_account_id")).strip()
                    if raw_transaction.get("settlement_cash_account_id")
                    else None
                ),
                instrument_id=str(raw_transaction.get("instrument_id")).strip() if raw_transaction.get("instrument_id") else None,
                instrument_ref_json=(
                    deepcopy(raw_transaction.get("instrument_ref"))
                    if isinstance(raw_transaction.get("instrument_ref"), dict)
                    else None
                ),
                quantity=(
                    float(raw_transaction["quantity"])
                    if raw_transaction.get("quantity") is not None
                    else None
                ),
                price=float(raw_transaction["price"]) if raw_transaction.get("price") is not None else None,
                gross_amount=float(raw_transaction.get("gross_amount") or 0.0),
                counter_amount=(
                    float(raw_transaction["counter_amount"])
                    if raw_transaction.get("counter_amount") is not None
                    else None
                ),
                fx_rate=float(raw_transaction["fx_rate"]) if raw_transaction.get("fx_rate") is not None else None,
                fees=float(raw_transaction.get("fees") or 0.0),
                taxes=float(raw_transaction.get("taxes") or 0.0),
                currency=str(raw_transaction.get("currency") or "USD").strip().upper() or "USD",
                transfer_scope=(
                    str(raw_transaction.get("transfer_scope")).strip()
                    if raw_transaction.get("transfer_scope")
                    else None
                ),
                transfer_object_type=(
                    str(raw_transaction.get("transfer_object_type")).strip()
                    if raw_transaction.get("transfer_object_type")
                    else None
                ),
                transfer_group_id=(
                    str(raw_transaction.get("transfer_group_id")).strip()
                    if raw_transaction.get("transfer_group_id")
                    else None
                ),
                counterparty_account_id=(
                    str(raw_transaction.get("counterparty_account_id")).strip()
                    if raw_transaction.get("counterparty_account_id")
                    else None
                ),
                note=str(raw_transaction.get("note")).strip() if raw_transaction.get("note") else None,
                created_at=(
                    str(raw_transaction.get("created_at")).strip()
                    if raw_transaction.get("created_at")
                    else None
                ),
            )
        )

    session.flush()
    _refresh_portfolio_instrument_universe_records(session, None)


def _serialize_portfolio_row(item: PortfolioRecordModel) -> dict[str, object]:
    return {
        "portfolio_id": item.portfolio_id,
        "portfolio_name": item.portfolio_name,
        "base_currency": item.base_currency,
        "valuation_timezone": item.valuation_timezone,
        "valuation_cutoff_policy": item.valuation_cutoff_policy,
        "as_of_date": item.as_of_date.isoformat() if item.as_of_date is not None else None,
        "nav": item.nav,
        "day_change_value": item.day_change_value,
        "day_change_pct": item.day_change_pct,
        "securities_count": item.securities_count,
        "sort_order": item.sort_order,
        "default_planning_taxonomy_id": item.default_planning_taxonomy_id,
        "risk_policy_json": deepcopy(item.risk_policy_json) if isinstance(item.risk_policy_json, dict) else None,
    }


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _max_transaction_trade_date(transactions: list[TransactionRecordModel]) -> date | None:
    return max((transaction.trade_date for transaction in transactions if transaction.trade_date is not None), default=None)


def _max_transaction_activity_date(transactions: list[TransactionRecordModel]) -> date | None:
    activity_dates = [
        candidate
        for transaction in transactions
        for candidate in (
            transaction.trade_date,
            transaction.settlement_date,
            transaction.entitlement_date,
        )
        if candidate is not None
    ]
    return max(activity_dates, default=None)


def _latest_market_data_date_for_instruments(session, instrument_ids: set[str]) -> date | None:
    normalized_instrument_ids = {instrument_id for instrument_id in instrument_ids if instrument_id}
    if not normalized_instrument_ids:
        return None

    return session.scalar(
        select(InstrumentMarketData.as_of_date)
        .where(
            InstrumentMarketData.instrument_id.in_(normalized_instrument_ids),
            InstrumentMarketData.metric_family.in_(("price", "nav")),
            InstrumentMarketData.status != "error",
        )
        .group_by(InstrumentMarketData.as_of_date)
        .having(func.count(func.distinct(InstrumentMarketData.instrument_id)) == len(normalized_instrument_ids))
        .order_by(InstrumentMarketData.as_of_date.desc())
        .limit(1)
    )


def _resolve_live_portfolio_as_of_date(
    session,
    item: PortfolioRecordModel,
    *,
    accounts: list[AccountRecordModel],
    transactions: list[TransactionRecordModel],
) -> date:
    transaction_rows = [_serialize_transaction_row(transaction) for transaction in transactions]
    portfolio_as_of_date = item.as_of_date
    latest_trade_date = _max_transaction_trade_date(transactions)
    latest_activity_date = _max_transaction_activity_date(transactions)
    transacted_instrument_ids = {
        str(transaction.instrument_id or "")
        for transaction in transactions
        if str(transaction.instrument_id or "")
    }

    latest_transacted_market_date = _latest_market_data_date_for_instruments(session, transacted_instrument_ids)
    source_candidate_dates = [
        candidate
        for candidate in (
            latest_activity_date,
            latest_transacted_market_date,
        )
        if candidate is not None
    ]
    candidate_as_of_date = max(source_candidate_dates, default=portfolio_as_of_date or date.today())

    boundary_transactions = [
        transaction
        for transaction in transaction_rows
        if (_safe_date(transaction.get("trade_date")) or date.min) <= candidate_as_of_date
    ]
    open_instrument_ids = {
        str(position_lot.get("instrument_id") or "")
        for position_lot in build_position_lots(
            item.portfolio_id,
            [_serialize_account_row(account) for account in accounts],
            boundary_transactions,
            status="open",
            as_of_date=candidate_as_of_date,
        )
        if str(position_lot.get("instrument_id") or "")
    }
    latest_open_market_date = _latest_market_data_date_for_instruments(session, open_instrument_ids)
    if latest_open_market_date is not None:
        resolved_candidate_dates = [
            candidate
            for candidate in (
                latest_activity_date,
                latest_open_market_date,
            )
            if candidate is not None
        ]
        return max(resolved_candidate_dates, default=candidate_as_of_date)

    fallback_candidate_dates = [
        candidate
        for candidate in (
            latest_activity_date,
            latest_transacted_market_date,
            portfolio_as_of_date,
        )
        if candidate is not None
    ]
    return max(fallback_candidate_dates, default=candidate_as_of_date)


def _build_live_portfolio_rollup(
    session,
    item: PortfolioRecordModel,
    *,
    accounts: list[AccountRecordModel],
    transactions: list[TransactionRecordModel],
) -> dict[str, object]:
    base_payload = _serialize_portfolio_row(item)
    as_of_date = _resolve_live_portfolio_as_of_date(
        session,
        item,
        accounts=accounts,
        transactions=transactions,
    )
    base_payload["as_of_date"] = as_of_date.isoformat()
    if not accounts and not transactions:
        return base_payload

    workspace = build_account_workspace(
        item.portfolio_id,
        [_serialize_account_row(account) for account in accounts],
        [_serialize_transaction_row(transaction) for transaction in transactions],
        selected_account_id=None,
        base_currency=item.base_currency,
        as_of_date=as_of_date,
    )
    account_rollups = workspace.get("accounts", [])
    nav = sum(
        _safe_float(account.get("account_value_base")) or 0.0
        for account in account_rollups
        if isinstance(account, dict)
    )
    summary = workspace.get("summary", {})
    return {
        **base_payload,
        "nav": nav,
        "securities_count": int(summary.get("position_line_count") or 0),
    }


def _serialize_portfolio_row_with_live_summary(
    session,
    item: PortfolioRecordModel,
) -> dict[str, object]:
    accounts = session.scalars(
        select(AccountRecordModel)
        .where(AccountRecordModel.portfolio_id == item.portfolio_id)
        .order_by(AccountRecordModel.account_id)
    ).all()
    transactions = session.scalars(
        select(TransactionRecordModel)
        .where(TransactionRecordModel.portfolio_id == item.portfolio_id)
        .order_by(
            TransactionRecordModel.trade_date,
            TransactionRecordModel.trade_at,
            TransactionRecordModel.created_at,
            TransactionRecordModel.transaction_id,
            TransactionRecordModel.settlement_date,
        )
    ).all()
    return _build_live_portfolio_rollup(session, item, accounts=accounts, transactions=transactions)


def _serialize_portfolio_row_with_materialized_summary(
    session,
    item: PortfolioRecordModel,
) -> dict[str, object]:
    payload = _serialize_portfolio_row(item)
    latest_snapshot = default_portfolio_snapshot(session, item.portfolio_id)
    if latest_snapshot is None:
        return payload

    snapshot = latest_snapshot.snapshot_json if isinstance(latest_snapshot.snapshot_json, dict) else {}
    payload["as_of_date"] = latest_snapshot.as_of_date.isoformat()
    payload["nav"] = _safe_float(snapshot.get("nav")) or 0.0
    payload["day_change_value"] = _safe_float(snapshot.get("absolute_change")) or 0.0
    payload["day_change_pct"] = _safe_float(snapshot.get("daily_twr")) or 0.0
    payload["securities_count"] = int(snapshot.get("total_position_count") or 0)
    return payload


def _serialize_account_row(item: AccountRecordModel) -> dict[str, object]:
    return {
        "account_id": item.account_id,
        "portfolio_id": item.portfolio_id,
        "account_name": item.account_name,
        "account_type": item.account_type,
        "currency": item.currency,
        "institution": item.institution,
        "default_settlement_cash_account_id": item.default_settlement_cash_account_id,
        "cost_basis_method": item.cost_basis_method,
        "allowed_instrument_types": deepcopy(item.allowed_instrument_types_json),
        "opened_at": item.opened_at.isoformat() if item.opened_at is not None else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at is not None else None,
        "status": item.status,
    }


def _serialize_transaction_row(item: TransactionRecordModel) -> dict[str, object]:
    return {
        "transaction_id": item.transaction_id,
        "portfolio_id": item.portfolio_id,
        "transaction_type": item.transaction_type,
        "trade_date": item.trade_date.isoformat(),
        "trade_time": item.trade_time,
        "trade_at": item.trade_at,
        "trade_timezone": item.trade_timezone,
        "trade_time_is_estimated": item.trade_time_is_estimated,
        "settlement_date": item.settlement_date.isoformat(),
        "entitlement_date": item.entitlement_date.isoformat() if item.entitlement_date is not None else None,
        "acquisition_date": item.acquisition_date.isoformat() if item.acquisition_date is not None else None,
        "account_id": item.account_id,
        "settlement_cash_account_id": item.settlement_cash_account_id,
        "instrument_id": item.instrument_id,
        "instrument_ref": deepcopy(item.instrument_ref_json),
        "quantity": item.quantity,
        "price": item.price,
        "gross_amount": item.gross_amount,
        "counter_amount": item.counter_amount,
        "fx_rate": item.fx_rate,
        "fees": item.fees,
        "taxes": item.taxes,
        "currency": item.currency,
        "transfer_scope": item.transfer_scope,
        "transfer_object_type": item.transfer_object_type,
        "transfer_group_id": item.transfer_group_id,
        "counterparty_account_id": item.counterparty_account_id,
        "note": item.note,
        "created_at": item.created_at,
    }


def _serialize_portfolio_instrument_universe_row(
    item: PortfolioInstrumentUniverseRecordModel,
) -> dict[str, object]:
    return {
        "portfolio_id": item.portfolio_id,
        "instrument_id": item.instrument_id,
        "instrument_ref": deepcopy(item.instrument_ref_json),
        "source": item.source,
        "holding_state": item.holding_state,
        "first_transaction_date": (
            item.first_transaction_date.isoformat()
            if item.first_transaction_date is not None
            else None
        ),
        "last_transaction_date": (
            item.last_transaction_date.isoformat()
            if item.last_transaction_date is not None
            else None
        ),
        "transaction_count": item.transaction_count,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _transaction_position_quantity_delta(record: TransactionRecordModel) -> float:
    quantity = _safe_float(record.quantity) or 0.0
    if quantity <= 0:
        return 0.0
    transaction_type = str(record.transaction_type or "")
    if transaction_type in {"opening_balance", "buy", "dividend_reinvestment"}:
        return quantity
    if transaction_type in {"sell", "maturity_redemption"}:
        return -quantity
    if transaction_type == "transfer_in" and record.transfer_object_type == "position":
        return quantity
    if transaction_type == "transfer_out" and record.transfer_object_type == "position":
        return -quantity
    return 0.0


def _transaction_record_order_key(record: TransactionRecordModel) -> tuple[str, str, str, str, str]:
    return (
        record.trade_date.isoformat() if record.trade_date is not None else "",
        record.trade_at or "",
        record.created_at or "",
        record.transaction_id or "",
        record.settlement_date.isoformat() if record.settlement_date is not None else "",
    )


def _refresh_portfolio_instrument_universe_records(
    session,
    portfolio_id: str | None,
    instrument_ids: set[str] | None = None,
) -> None:
    normalized_portfolio_id = str(portfolio_id or "").strip()
    normalized_instrument_ids = {
        str(instrument_id or "").strip()
        for instrument_id in (instrument_ids or set())
        if str(instrument_id or "").strip()
    }
    if instrument_ids is not None and not normalized_instrument_ids:
        return

    existing_statement = select(PortfolioInstrumentUniverseRecordModel)
    if normalized_portfolio_id:
        existing_statement = existing_statement.where(
            PortfolioInstrumentUniverseRecordModel.portfolio_id == normalized_portfolio_id
        )
    if normalized_instrument_ids:
        existing_statement = existing_statement.where(
            PortfolioInstrumentUniverseRecordModel.instrument_id.in_(normalized_instrument_ids)
        )
    existing_records = session.scalars(existing_statement).all()
    existing_by_key = {
        (record.portfolio_id, record.instrument_id): record
        for record in existing_records
    }

    transaction_statement = select(TransactionRecordModel).where(TransactionRecordModel.instrument_id.is_not(None))
    if normalized_portfolio_id:
        transaction_statement = transaction_statement.where(TransactionRecordModel.portfolio_id == normalized_portfolio_id)
    if normalized_instrument_ids:
        transaction_statement = transaction_statement.where(TransactionRecordModel.instrument_id.in_(normalized_instrument_ids))
    transaction_rows = session.scalars(
        transaction_statement.order_by(
            TransactionRecordModel.portfolio_id,
            TransactionRecordModel.instrument_id,
            TransactionRecordModel.trade_date,
            TransactionRecordModel.trade_at,
            TransactionRecordModel.created_at,
            TransactionRecordModel.transaction_id,
        )
    ).all()
    transactions_by_key: dict[tuple[str, str], list[TransactionRecordModel]] = {}
    for transaction in transaction_rows:
        instrument_id = str(transaction.instrument_id or "").strip()
        if not instrument_id:
            continue
        transactions_by_key.setdefault((transaction.portfolio_id, instrument_id), []).append(transaction)

    assignment_statement = (
        select(TaxonomyRecordModel.portfolio_id, TaxonomyAssignmentRecordModel.target_entity_id)
        .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TaxonomyAssignmentRecordModel.taxonomy_id)
        .where(
            TaxonomyAssignmentRecordModel.target_scope == "instrument",
            TaxonomyAssignmentRecordModel.status == "active",
        )
    )
    if normalized_portfolio_id:
        assignment_statement = assignment_statement.where(TaxonomyRecordModel.portfolio_id == normalized_portfolio_id)
    if normalized_instrument_ids:
        assignment_statement = assignment_statement.where(
            TaxonomyAssignmentRecordModel.target_entity_id.in_(normalized_instrument_ids)
        )
    assignment_keys = {
        (str(row[0] or "").strip(), str(row[1] or "").strip())
        for row in session.execute(assignment_statement)
        if str(row[0] or "").strip() and str(row[1] or "").strip()
    }

    target_keys = set(existing_by_key) | set(transactions_by_key) | assignment_keys
    if normalized_portfolio_id:
        target_keys = {key for key in target_keys if key[0] == normalized_portfolio_id}
    if normalized_instrument_ids:
        target_keys = {key for key in target_keys if key[1] in normalized_instrument_ids}

    now = _current_utc_timestamp()
    for target_portfolio_id, target_instrument_id in sorted(target_keys):
        rows = transactions_by_key.get((target_portfolio_id, target_instrument_id), [])
        existing = existing_by_key.get((target_portfolio_id, target_instrument_id))
        has_assignment = (target_portfolio_id, target_instrument_id) in assignment_keys

        if rows:
            quantity = sum(_transaction_position_quantity_delta(row) for row in rows)
            latest = max(rows, key=_transaction_record_order_key)
            first_transaction_date = min((row.trade_date for row in rows if row.trade_date is not None), default=None)
            last_transaction_date = max((row.trade_date for row in rows if row.trade_date is not None), default=None)
            if existing is None:
                existing = PortfolioInstrumentUniverseRecordModel(
                    portfolio_id=target_portfolio_id,
                    instrument_id=target_instrument_id,
                    created_at=now,
                )
                session.add(existing)
            existing.instrument_ref_json = deepcopy(latest.instrument_ref_json) if isinstance(latest.instrument_ref_json, dict) else None
            existing.source = "transaction"
            existing.holding_state = "held" if quantity > 1e-9 else "not_held"
            existing.first_transaction_date = first_transaction_date
            existing.last_transaction_date = last_transaction_date
            existing.transaction_count = len(rows)
            existing.status = "active"
            existing.updated_at = now
            continue

        if existing is None:
            if not has_assignment:
                continue
            session.add(
                PortfolioInstrumentUniverseRecordModel(
                    portfolio_id=target_portfolio_id,
                    instrument_id=target_instrument_id,
                    instrument_ref_json=None,
                    source="taxonomy",
                    holding_state="not_held",
                    first_transaction_date=None,
                    last_transaction_date=None,
                    transaction_count=0,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            continue

        if not has_assignment:
            existing.holding_state = "not_held"
            existing.first_transaction_date = None
            existing.last_transaction_date = None
            existing.transaction_count = 0
            existing.status = "active" if existing.source == "manual" else "archived"
            existing.updated_at = now
            continue

        existing.source = "manual" if existing.source == "manual" else "taxonomy"
        existing.holding_state = "not_held"
        existing.first_transaction_date = None
        existing.last_transaction_date = None
        existing.transaction_count = 0
        existing.status = "active"
        existing.updated_at = now


def _serialize_taxonomy_row(item: TaxonomyRecordModel) -> dict[str, object]:
    return {
        "taxonomy_id": item.taxonomy_id,
        "portfolio_id": item.portfolio_id,
        "name": item.name,
        "taxonomy_type": item.taxonomy_type,
        "purpose": item.purpose,
        "primary_assignment_scope": item.primary_assignment_scope,
        "planning_enabled": item.planning_enabled,
        "budgeting_level": item.budgeting_level,
        "root_default_target_dimension": item.root_default_target_dimension,
        "status": item.status,
        "source_template_ref": item.source_template_ref,
    }


def _serialize_taxonomy_node_row(item: TaxonomyNodeRecordModel) -> dict[str, object]:
    return {
        "taxonomy_node_id": item.taxonomy_node_id,
        "taxonomy_id": item.taxonomy_id,
        "parent_taxonomy_node_id": item.parent_taxonomy_node_id,
        "node_name": item.node_name,
        "node_code": item.node_code,
        "sort_order": item.sort_order,
        "is_terminal": item.is_terminal,
        "default_target_dimension": item.default_target_dimension,
        "status": item.status,
    }


def _serialize_taxonomy_assignment_row(item: TaxonomyAssignmentRecordModel) -> dict[str, object]:
    return {
        "assignment_id": item.assignment_id,
        "taxonomy_id": item.taxonomy_id,
        "target_scope": item.target_scope,
        "target_entity_id": item.target_entity_id,
        "taxonomy_node_id": item.taxonomy_node_id,
        "status": item.status,
    }


def _serialize_target_set_row(item: TargetSetRecordModel) -> dict[str, object]:
    return {
        "target_set_id": item.target_set_id,
        "taxonomy_id": item.taxonomy_id,
        "comparator_taxonomy_node_id": item.comparator_taxonomy_node_id,
        "target_set_type": item.target_set_type,
        "name": item.name,
        "weight_enabled": item.weight_enabled,
        "risk_budget_enabled": item.risk_budget_enabled,
        "status": item.status,
        "notes": item.notes,
    }


def _serialize_target_set_line_row(item: TargetSetLineRecordModel) -> dict[str, object]:
    return {
        "target_line_id": item.target_line_id,
        "target_set_id": item.target_set_id,
        "target_member_type": item.target_member_type,
        "target_member_id": item.target_member_id,
        "taxonomy_node_id": item.taxonomy_node_id,
        "target_weight": item.target_weight,
        "target_risk_share": item.target_risk_share,
        "notes": item.notes,
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "portfolio"


def _next_transaction_id_from_values(transaction_ids: list[str]) -> str:
    next_number = 1
    for transaction_id in transaction_ids:
        if not transaction_id.startswith("txn-"):
            continue
        try:
            next_number = max(next_number, int(transaction_id.split("-", 1)[1]) + 1)
        except ValueError:
            continue
    return f"txn-{next_number:04d}"


def _next_transaction_id(session) -> str:
    max_suffix = session.scalar(
        select(func.max(cast(func.substr(TransactionRecordModel.transaction_id, 5), Integer))).where(
            TransactionRecordModel.transaction_id.like("txn-%")
        )
    )
    next_number = int(max_suffix or 0) + 1
    return f"txn-{next_number:04d}"


def _next_account_id(existing_ids: list[str], account_name: str, account_type: str) -> str:
    base = _slugify(account_name)
    prefix = "cash" if account_type == "deposit_account" else "broker"
    candidate = f"{prefix}-{base}"
    existing_id_set = set(existing_ids)
    suffix = 2
    while candidate in existing_id_set:
        candidate = f"{prefix}-{base}-{suffix}"
        suffix += 1
    return candidate


def _next_taxonomy_id(session, name: str) -> str:
    base = f"tax-{_slugify(name)}"
    candidate = base
    suffix = 2
    existing_ids = set(
        session.scalars(
            select(TaxonomyRecordModel.taxonomy_id).where(TaxonomyRecordModel.taxonomy_id.like(f"{base}%"))
        ).all()
    )
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _next_taxonomy_node_id(session, taxonomy_id: str, node_name: str) -> str:
    base = f"{taxonomy_id}-{_slugify(node_name)}"
    candidate = base
    suffix = 2
    existing_ids = set(
        session.scalars(
            select(TaxonomyNodeRecordModel.taxonomy_node_id).where(
                TaxonomyNodeRecordModel.taxonomy_node_id.like(f"{base}%")
            )
        ).all()
    )
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _next_taxonomy_assignment_id(session) -> str:
    max_suffix = session.scalar(
        select(func.max(cast(func.substr(TaxonomyAssignmentRecordModel.assignment_id, 8), Integer))).where(
            TaxonomyAssignmentRecordModel.assignment_id.like("assign-%")
        )
    )
    next_number = int(max_suffix or 0) + 1
    return f"assign-{next_number:04d}"


def _next_target_set_id(session, taxonomy_id: str, target_set_type: str, name: str) -> str:
    base = f"{taxonomy_id}-{target_set_type}-{_slugify(name)}"
    candidate = base
    suffix = 2
    existing_ids = set(
        session.scalars(
            select(TargetSetRecordModel.target_set_id).where(TargetSetRecordModel.target_set_id.like(f"{base}%"))
        ).all()
    )
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _next_target_line_id(session) -> str:
    max_suffix = session.scalar(
        select(func.max(cast(func.substr(TargetSetLineRecordModel.target_line_id, 7), Integer))).where(
            TargetSetLineRecordModel.target_line_id.like("tline-%")
        )
    )
    next_number = int(max_suffix or 0) + 1
    return f"tline-{next_number:04d}"


def _taxonomy_node_assignment_count(session, taxonomy_node_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TaxonomyAssignmentRecordModel)
            .where(TaxonomyAssignmentRecordModel.taxonomy_node_id == taxonomy_node_id)
        )
        or 0
    )


def _taxonomy_node_child_count(session, taxonomy_node_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TaxonomyNodeRecordModel)
            .where(TaxonomyNodeRecordModel.parent_taxonomy_node_id == taxonomy_node_id)
        )
        or 0
    )


def _ensure_node_can_accept_children(session, taxonomy_id: str, taxonomy_node_id: str) -> TaxonomyNodeRecordModel:
    node = session.scalar(
        select(TaxonomyNodeRecordModel).where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
        )
    )
    if node is None:
        raise ValueError("Parent taxonomy node not found.")
    if _taxonomy_node_assignment_count(session, taxonomy_node_id) > 0:
        raise ValueError("Cannot add child nodes under a taxonomy node that already has assignments.")
    return node


def _refresh_parent_terminal_state(session, taxonomy_node_id: str | None) -> None:
    if not taxonomy_node_id:
        return
    parent_record = session.get(TaxonomyNodeRecordModel, taxonomy_node_id)
    if parent_record is None:
        return
    parent_record.is_terminal = _taxonomy_node_child_count(session, taxonomy_node_id) == 0


def _budgeting_dimensions(budgeting_level: str | None) -> set[str]:
    if budgeting_level == "weight":
        return {"weight"}
    if budgeting_level == "risk_budget":
        return {"risk_budget"}
    if budgeting_level == "weight_and_risk_budget":
        return {"weight", "risk_budget"}
    return set()


def _taxonomy_allows_assignment_scope(taxonomy: TaxonomyRecordModel, target_scope: str) -> bool:
    if taxonomy.primary_assignment_scope == target_scope:
        return True
    return bool(
        taxonomy.planning_enabled
        and taxonomy.primary_assignment_scope == "instrument"
        and target_scope == "cash_bucket"
    )


def _active_taxonomy_nodes_by_parent(
    session,
    *,
    taxonomy_id: str,
) -> dict[str | None, list[TaxonomyNodeRecordModel]]:
    nodes = session.scalars(
        select(TaxonomyNodeRecordModel).where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyNodeRecordModel.status == "active",
        )
    ).all()
    grouped: dict[str | None, list[TaxonomyNodeRecordModel]] = {}
    for node in nodes:
        grouped.setdefault(node.parent_taxonomy_node_id, []).append(node)
    return grouped


def _taxonomy_node_subtree_ids(
    nodes_by_parent: dict[str | None, list[TaxonomyNodeRecordModel]],
    *,
    root_node_id: str,
) -> set[str]:
    pending = [root_node_id]
    seen: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for child in nodes_by_parent.get(node_id, []):
            pending.append(child.taxonomy_node_id)
    return seen


def _scope_member_is_cash(
    session,
    *,
    taxonomy_id: str,
    target_member_type: str,
    target_member_id: str,
    nodes_by_parent: dict[str | None, list[TaxonomyNodeRecordModel]] | None = None,
) -> bool:
    if target_member_type == "cash_bucket":
        return True
    if target_member_type != TARGET_MEMBER_NODE:
        return False
    resolved_nodes_by_parent = nodes_by_parent or _active_taxonomy_nodes_by_parent(session, taxonomy_id=taxonomy_id)
    subtree_node_ids = _taxonomy_node_subtree_ids(
        resolved_nodes_by_parent,
        root_node_id=target_member_id,
    )
    assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id.in_(list(subtree_node_ids)),
            TaxonomyAssignmentRecordModel.status == "active",
        )
    ).all()
    return bool(assignments) and all(
        str(assignment.target_scope) == "cash_bucket" for assignment in assignments
    )


def _normalize_target_line_member(raw_line: dict[str, object]) -> tuple[str, str, str | None]:
    target_member_type = str(raw_line.get("target_member_type") or TARGET_MEMBER_NODE).strip() or TARGET_MEMBER_NODE
    target_member_id = str(raw_line.get("target_member_id") or raw_line.get("taxonomy_node_id") or "").strip()
    taxonomy_node_id = (
        str(raw_line.get("taxonomy_node_id")).strip()
        if raw_line.get("taxonomy_node_id")
        else (target_member_id if target_member_type == TARGET_MEMBER_NODE else None)
    )
    if not target_member_id:
        raise ValueError("Target set lines require a target member reference.")
    if target_member_type == TARGET_MEMBER_NODE and not taxonomy_node_id:
        raise ValueError("taxonomy_node target lines require taxonomy_node_id.")
    return target_member_type, target_member_id, taxonomy_node_id


def _scope_child_nodes(
    session,
    *,
    taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
) -> tuple[TaxonomyNodeRecordModel | None, list[dict[str, object]]]:
    parent_node = None
    if comparator_taxonomy_node_id:
        parent_node = session.scalar(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.taxonomy_node_id == comparator_taxonomy_node_id,
            )
        )
        if parent_node is None:
            raise ValueError("Comparator scope node not found.")
        children = session.scalars(
            select(TaxonomyNodeRecordModel)
            .where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.parent_taxonomy_node_id == comparator_taxonomy_node_id,
                TaxonomyNodeRecordModel.status == "active",
            )
            .order_by(
                TaxonomyNodeRecordModel.sort_order,
                TaxonomyNodeRecordModel.node_name,
                TaxonomyNodeRecordModel.taxonomy_node_id,
            )
        ).all()
    else:
        children = session.scalars(
            select(TaxonomyNodeRecordModel)
            .where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.parent_taxonomy_node_id.is_(None),
                TaxonomyNodeRecordModel.status == "active",
            )
            .order_by(
                TaxonomyNodeRecordModel.sort_order,
                TaxonomyNodeRecordModel.node_name,
                TaxonomyNodeRecordModel.taxonomy_node_id,
            )
        ).all()
    if not children:
        return parent_node, []
    return parent_node, children


def _scope_target_members(
    session,
    *,
    taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
) -> tuple[TaxonomyNodeRecordModel | None, list[dict[str, object]]]:
    parent_node, child_nodes = _scope_child_nodes(
        session,
        taxonomy_id=taxonomy_id,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
    )
    if child_nodes:
        return parent_node, [
            {
                "target_member_type": TARGET_MEMBER_NODE,
                "target_member_id": node.taxonomy_node_id,
                "taxonomy_node_id": node.taxonomy_node_id,
                "label": node.node_name,
            }
            for node in child_nodes
        ]

    if comparator_taxonomy_node_id is None:
        raise ValueError("Comparator scope must have active child sleeves.")

    direct_assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id == comparator_taxonomy_node_id,
            TaxonomyAssignmentRecordModel.status == "active",
        )
    ).all()
    visible_direct_assignments: dict[tuple[str, str], TaxonomyAssignmentRecordModel] = {}
    for assignment in direct_assignments:
        member_key = (str(assignment.target_scope), str(assignment.target_entity_id))
        visible_direct_assignments.setdefault(member_key, assignment)
    if not direct_assignments:
        raise ValueError("Comparator scope must have active child sleeves or directly assigned instruments.")

    return parent_node, [
        {
            "target_member_type": member_key[0],
            "target_member_id": member_key[1],
            "taxonomy_node_id": None,
            "label": member_key[1],
        }
        for member_key in sorted(visible_direct_assignments)
    ]


def _validate_target_set_lines(
    session,
    *,
    taxonomy: TaxonomyRecordModel,
    comparator_taxonomy_node_id: str | None,
    target_set_type: str,
    weight_enabled: bool,
    risk_budget_enabled: bool,
    status: str,
    lines: list[dict[str, object]],
    exclude_target_set_id: str | None = None,
) -> tuple[TaxonomyNodeRecordModel | None, list[TaxonomyNodeRecordModel]]:
    if not taxonomy.planning_enabled:
        raise ValueError("Target sets require a planning-enabled taxonomy.")
    if taxonomy.primary_assignment_scope != "instrument":
        raise ValueError("Target sets require an instrument-scoped taxonomy.")
    if not weight_enabled and not risk_budget_enabled:
        raise ValueError("At least one target dimension must be enabled.")

    allowed_dimensions = _budgeting_dimensions(taxonomy.budgeting_level)
    if not allowed_dimensions:
        raise ValueError("Taxonomy budgeting_level must be configured before adding target sets.")
    if weight_enabled and "weight" not in allowed_dimensions:
        raise ValueError("Taxonomy budgeting_level does not permit weight targets.")
    if risk_budget_enabled and "risk_budget" not in allowed_dimensions:
        raise ValueError("Taxonomy budgeting_level does not permit risk-budget targets.")

    parent_node, scope_members = _scope_target_members(
        session,
        taxonomy_id=taxonomy.taxonomy_id,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
    )
    if not lines:
        raise ValueError("Target set lines are required.")

    expected_member_keys = {
        (str(item["target_member_type"]), str(item["target_member_id"]))
        for item in scope_members
    }
    seen_member_keys: set[tuple[str, str]] = set()
    nodes_by_parent = _active_taxonomy_nodes_by_parent(session, taxonomy_id=taxonomy.taxonomy_id)
    non_cash_risk_share_total = 0.0

    for raw_line in lines:
        member_type, member_id, _ = _normalize_target_line_member(raw_line)
        member_key = (member_type, member_id)
        if member_key not in expected_member_keys:
            raise ValueError("Target set lines must match the direct members of the selected scope.")
        if member_key in seen_member_keys:
            raise ValueError("Duplicate scope member in target set lines.")
        seen_member_keys.add(member_key)

        target_weight = raw_line.get("target_weight")
        target_risk_share = raw_line.get("target_risk_share")
        if weight_enabled:
            if target_weight is None:
                raise ValueError("Every scope member needs a target_weight when weight is enabled.")
            resolved_weight = float(target_weight)
            if resolved_weight < 0:
                raise ValueError("target_weight must be zero or greater.")
        elif target_weight is not None:
            raise ValueError("target_weight must be empty when weight is disabled.")

        if risk_budget_enabled:
            if target_risk_share is None:
                raise ValueError("Every scope member needs a target_risk_share when risk_budget is enabled.")
            resolved_risk_share = float(target_risk_share)
            if resolved_risk_share < 0:
                raise ValueError("target_risk_share must be zero or greater.")
            if _scope_member_is_cash(
                session,
                taxonomy_id=taxonomy.taxonomy_id,
                target_member_type=member_type,
                target_member_id=member_id,
                nodes_by_parent=nodes_by_parent,
            ):
                if abs(resolved_risk_share) > TARGET_SET_EPSILON:
                    raise ValueError("Cash scope members must use target_risk_share = 0.")
            else:
                non_cash_risk_share_total += resolved_risk_share
        elif target_risk_share is not None:
            raise ValueError("target_risk_share must be empty when risk_budget is disabled.")

    if seen_member_keys != expected_member_keys:
        raise ValueError("Target set lines must cover every direct member in the selected scope.")
    if risk_budget_enabled and abs(non_cash_risk_share_total - 1.0) > TARGET_SET_EPSILON:
        raise ValueError("Non-cash target_risk_share values must sum to 100%.")

    if status == "active":
        overlapping_target_sets = session.scalars(
            select(TargetSetRecordModel).where(
                TargetSetRecordModel.taxonomy_id == taxonomy.taxonomy_id,
                TargetSetRecordModel.target_set_type == target_set_type,
                TargetSetRecordModel.status == "active",
                TargetSetRecordModel.comparator_taxonomy_node_id == comparator_taxonomy_node_id,
            )
        ).all()
        for existing in overlapping_target_sets:
            if exclude_target_set_id and existing.target_set_id == exclude_target_set_id:
                continue
            if target_set_type == "saa":
                raise ValueError("An active SAA target set already exists for this scope.")
            raise ValueError("An active TAA target set already exists for this scope.")

    return parent_node, scope_members


def _sort_transactions(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        records,
        key=lambda item: (
            str(item.get("trade_date") or ""),
            str(item.get("trade_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("transaction_id") or ""),
            str(item.get("settlement_date") or ""),
        ),
        reverse=True,
    )


def list_portfolios() -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(
            select(PortfolioRecordModel).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_name,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        return [_serialize_portfolio_row_with_materialized_summary(session, item) for item in portfolios]


def get_portfolio(portfolio_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(PortfolioRecordModel, portfolio_id)
        if record is None:
            return None
        return _serialize_portfolio_row_with_materialized_summary(session, record)


def get_portfolio_live_summary(portfolio_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(PortfolioRecordModel, portfolio_id)
        if record is None:
            return None
        return _serialize_portfolio_row_with_live_summary(session, record)


def list_taxonomies(portfolio_id: str) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomies = session.scalars(
            select(TaxonomyRecordModel)
            .where(TaxonomyRecordModel.portfolio_id == portfolio_id)
            .order_by(
                TaxonomyRecordModel.name,
                TaxonomyRecordModel.taxonomy_id,
            )
        ).all()
        return [_serialize_taxonomy_row(item) for item in taxonomies]


def get_taxonomy(portfolio_id: str, taxonomy_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if record is None:
            return None
        return _serialize_taxonomy_row(record)


def create_taxonomy(
    portfolio_id: str,
    *,
    name: str,
    taxonomy_type: str,
    purpose: str | None,
    primary_assignment_scope: str,
    planning_enabled: bool,
    budgeting_level: str | None,
    root_default_target_dimension: str,
    status: str,
    source_template_ref: str | None,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = TaxonomyRecordModel(
            taxonomy_id=_next_taxonomy_id(session, name),
            portfolio_id=portfolio_id,
            name=name.strip(),
            taxonomy_type=(taxonomy_type or "custom").strip() or "custom",
            purpose=(purpose or "").strip() or None,
            primary_assignment_scope=primary_assignment_scope,
            planning_enabled=planning_enabled,
            budgeting_level=(budgeting_level or "").strip() or None,
            root_default_target_dimension=(root_default_target_dimension or "weight").strip() or "weight",
            status=(status or "active").strip() or "active",
            source_template_ref=(source_template_ref or "").strip() or None,
        )
        session.add(record)
        session.commit()
        return _serialize_taxonomy_row(record)


def update_taxonomy(
    portfolio_id: str,
    taxonomy_id: str,
    *,
    name: str | None = UNSET,
    taxonomy_type: str | None = UNSET,
    purpose: str | None = UNSET,
    planning_enabled: bool | None = UNSET,
    budgeting_level: str | None = UNSET,
    root_default_target_dimension: str | None = UNSET,
    status: str | None = UNSET,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            raise ValueError("Portfolio not found.")
        record = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if record is None:
            raise ValueError("Taxonomy not found.")

        resolved_planning_enabled = record.planning_enabled if planning_enabled is UNSET else bool(planning_enabled)
        resolved_budgeting_level = record.budgeting_level if budgeting_level is UNSET else budgeting_level
        if resolved_budgeting_level and not resolved_planning_enabled:
            raise ValueError("budgeting_level requires planning_enabled.")
        if resolved_planning_enabled and record.primary_assignment_scope != "instrument":
            raise ValueError("planning_enabled taxonomies must use instrument assignment scope.")

        if name is not UNSET and name is not None:
            record.name = name.strip()
        if taxonomy_type is not UNSET and taxonomy_type is not None:
            record.taxonomy_type = taxonomy_type.strip() or "custom"
        if purpose is not UNSET:
            record.purpose = (purpose or "").strip() or None
        if planning_enabled is not UNSET:
            record.planning_enabled = resolved_planning_enabled
        if budgeting_level is not UNSET:
            record.budgeting_level = (resolved_budgeting_level or "").strip() or None
        if root_default_target_dimension is not UNSET and root_default_target_dimension is not None:
            record.root_default_target_dimension = root_default_target_dimension.strip() or "weight"
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        if not record.planning_enabled:
            if portfolio.default_planning_taxonomy_id == taxonomy_id:
                portfolio.default_planning_taxonomy_id = None
            research_settings = session.get(ResearchSettingsRecordModel, portfolio_id)
            if research_settings is not None and research_settings.planning_taxonomy_id == taxonomy_id:
                research_settings.planning_taxonomy_id = None
                research_settings.comparator_taxonomy_node_id = None

        session.commit()
        return _serialize_taxonomy_row(record)


def list_taxonomy_nodes(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = (
            select(TaxonomyNodeRecordModel)
            .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TaxonomyNodeRecordModel.taxonomy_id)
            .where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        )
        if taxonomy_id:
            statement = statement.where(TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)
        nodes = session.scalars(
            statement.order_by(
                TaxonomyNodeRecordModel.taxonomy_id,
                TaxonomyNodeRecordModel.sort_order,
                TaxonomyNodeRecordModel.node_name,
                TaxonomyNodeRecordModel.taxonomy_node_id,
            )
        ).all()
        return [_serialize_taxonomy_node_row(item) for item in nodes]


def create_taxonomy_node(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    parent_taxonomy_node_id: str | None,
    node_name: str,
    node_code: str | None,
    sort_order: int | None,
    is_terminal: bool,
    default_target_dimension: str,
    status: str,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")

        parent_node = None
        if parent_taxonomy_node_id:
            parent_node = _ensure_node_can_accept_children(session, taxonomy_id, parent_taxonomy_node_id)
            parent_node.is_terminal = False

        max_sort_order = session.scalar(
            select(func.max(TaxonomyNodeRecordModel.sort_order)).where(TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)
        )
        resolved_sort_order = int(sort_order) if sort_order is not None else int(max_sort_order or -1) + 1
        record = TaxonomyNodeRecordModel(
            taxonomy_node_id=_next_taxonomy_node_id(session, taxonomy_id, node_name),
            taxonomy_id=taxonomy_id,
            parent_taxonomy_node_id=parent_taxonomy_node_id,
            node_name=node_name.strip(),
            node_code=(node_code or "").strip() or None,
            sort_order=resolved_sort_order,
            is_terminal=is_terminal,
            default_target_dimension=(default_target_dimension or "weight").strip() or "weight",
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.commit()
        return _serialize_taxonomy_node_row(record)


def update_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    *,
    node_name: str | None = UNSET,
    node_code: str | None = UNSET,
    parent_taxonomy_node_id: str | None = UNSET,
    sort_order: int | None = UNSET,
    default_target_dimension: str | None = UNSET,
    status: str | None = UNSET,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")

        record = session.scalar(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
            )
        )
        if record is None:
            raise ValueError("Taxonomy node not found.")

        if node_name is not UNSET and node_name is not None:
            record.node_name = node_name.strip()
        if node_code is not UNSET:
            record.node_code = (node_code or "").strip() or None
        if sort_order is not UNSET and sort_order is not None:
            record.sort_order = int(sort_order)
        if default_target_dimension is not UNSET and default_target_dimension is not None:
            record.default_target_dimension = default_target_dimension.strip() or "weight"
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        if parent_taxonomy_node_id is not UNSET:
            resolved_parent_id = (parent_taxonomy_node_id or "").strip() or None
            old_parent_id = record.parent_taxonomy_node_id
            if resolved_parent_id == taxonomy_node_id:
                raise ValueError("A taxonomy node cannot become its own parent.")
            if resolved_parent_id != old_parent_id:
                referenced_target_line_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(TargetSetLineRecordModel)
                        .where(TargetSetLineRecordModel.taxonomy_node_id == taxonomy_node_id)
                    )
                    or 0
                )
                referenced_scope_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(TargetSetRecordModel)
                        .where(TargetSetRecordModel.comparator_taxonomy_node_id == taxonomy_node_id)
                    )
                    or 0
                )
                if referenced_target_line_count or referenced_scope_count:
                    raise ValueError("Cannot move a taxonomy node while it is referenced by target-set configuration.")

            if resolved_parent_id is not None:
                ancestor_id = resolved_parent_id
                while ancestor_id is not None:
                    if ancestor_id == taxonomy_node_id:
                        raise ValueError("Cannot move a taxonomy node under one of its descendants.")
                    ancestor_record = session.get(TaxonomyNodeRecordModel, ancestor_id)
                    ancestor_id = ancestor_record.parent_taxonomy_node_id if ancestor_record is not None else None

                if resolved_parent_id != old_parent_id:
                    new_parent = _ensure_node_can_accept_children(session, taxonomy_id, resolved_parent_id)
                    new_parent.is_terminal = False

            record.parent_taxonomy_node_id = resolved_parent_id
            session.flush()
            _refresh_parent_terminal_state(session, old_parent_id)
            _refresh_parent_terminal_state(session, resolved_parent_id)

        session.commit()
        return _serialize_taxonomy_node_row(record)


def list_taxonomy_assignments(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = (
            select(TaxonomyAssignmentRecordModel)
            .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TaxonomyAssignmentRecordModel.taxonomy_id)
            .where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        )
        if taxonomy_id:
            statement = statement.where(TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id)
        assignments = session.scalars(
            statement.order_by(
                TaxonomyAssignmentRecordModel.taxonomy_id,
                TaxonomyAssignmentRecordModel.target_scope,
                TaxonomyAssignmentRecordModel.target_entity_id,
                TaxonomyAssignmentRecordModel.assignment_id,
            )
        ).all()
        current_assignments_by_entity: dict[tuple[str, str, str], TaxonomyAssignmentRecordModel] = {}
        for item in assignments:
            assignment_key = (item.taxonomy_id, item.target_scope, item.target_entity_id)
            current = current_assignments_by_entity.get(assignment_key)
            if current is None:
                current_assignments_by_entity[assignment_key] = item
                continue
            if item.assignment_id >= current.assignment_id:
                current_assignments_by_entity[assignment_key] = item
        return [_serialize_taxonomy_assignment_row(item) for item in current_assignments_by_entity.values()]


def list_portfolio_instrument_universe(portfolio_id: str) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(PortfolioInstrumentUniverseRecordModel)
            .where(
                PortfolioInstrumentUniverseRecordModel.portfolio_id == portfolio_id,
                PortfolioInstrumentUniverseRecordModel.status == "active",
            )
            .order_by(
                PortfolioInstrumentUniverseRecordModel.holding_state,
                PortfolioInstrumentUniverseRecordModel.instrument_id,
            )
        ).all()
        return [_serialize_portfolio_instrument_universe_row(item) for item in records]


def upsert_portfolio_instrument_universe_record(
    portfolio_id: str,
    instrument_id: str,
    *,
    instrument_ref: dict[str, object] | None = None,
) -> dict[str, object] | None:
    normalized_portfolio_id = str(portfolio_id or "").strip()
    normalized_instrument_id = str(instrument_id or "").strip()
    if not normalized_portfolio_id or not normalized_instrument_id:
        raise ValueError("portfolio_id and instrument_id are required.")
    if isinstance(instrument_ref, dict):
        _validate_instrument_ref_contract(
            instrument_ref,
            context=f"Instrument universe '{normalized_instrument_id}'",
            expected_instrument_id=normalized_instrument_id,
        )

    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get(PortfolioRecordModel, normalized_portfolio_id) is None:
            return None

        now = _current_utc_timestamp()
        record = session.get(
            PortfolioInstrumentUniverseRecordModel,
            (normalized_portfolio_id, normalized_instrument_id),
        )
        if record is None:
            record = PortfolioInstrumentUniverseRecordModel(
                portfolio_id=normalized_portfolio_id,
                instrument_id=normalized_instrument_id,
                instrument_ref_json=deepcopy(instrument_ref) if isinstance(instrument_ref, dict) else None,
                source="manual",
                holding_state="not_held",
                first_transaction_date=None,
                last_transaction_date=None,
                transaction_count=0,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(record)
        else:
            if isinstance(instrument_ref, dict):
                record.instrument_ref_json = deepcopy(instrument_ref)
            if record.source != "transaction":
                record.source = "manual"
            if record.transaction_count == 0:
                record.holding_state = "not_held"
                record.first_transaction_date = None
                record.last_transaction_date = None
            record.status = "active"
            record.updated_at = now

        session.flush()
        serialized = _serialize_portfolio_instrument_universe_row(record)
        session.commit()
        return serialized


def delete_portfolio_instrument_universe_record(
    portfolio_id: str,
    instrument_id: str,
) -> bool:
    normalized_portfolio_id = str(portfolio_id or "").strip()
    normalized_instrument_id = str(instrument_id or "").strip()
    if not normalized_portfolio_id or not normalized_instrument_id:
        raise ValueError("portfolio_id and instrument_id are required.")

    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(
            PortfolioInstrumentUniverseRecordModel,
            (normalized_portfolio_id, normalized_instrument_id),
        )
        if record is None:
            return False
        if record.source != "manual" or record.holding_state == "held" or record.transaction_count > 0:
            raise ValueError("Only manually watched instruments without holdings can be deleted.")

        active_assignment_count = session.scalar(
            select(func.count())
            .select_from(TaxonomyAssignmentRecordModel)
            .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TaxonomyAssignmentRecordModel.taxonomy_id)
            .where(
                TaxonomyRecordModel.portfolio_id == normalized_portfolio_id,
                TaxonomyAssignmentRecordModel.target_scope == "instrument",
                TaxonomyAssignmentRecordModel.target_entity_id == normalized_instrument_id,
                TaxonomyAssignmentRecordModel.status == "active",
            )
        )
        if active_assignment_count:
            raise ValueError("Remove taxonomy assignments before deleting this watched instrument.")

        session.delete(record)
        session.commit()
        return True


def list_target_sets(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = (
            select(TargetSetRecordModel)
            .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TargetSetRecordModel.taxonomy_id)
            .where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        )
        if taxonomy_id:
            statement = statement.where(TargetSetRecordModel.taxonomy_id == taxonomy_id)
        records = session.scalars(
            statement.order_by(
                TargetSetRecordModel.taxonomy_id,
                TargetSetRecordModel.comparator_taxonomy_node_id,
                TargetSetRecordModel.target_set_type,
                TargetSetRecordModel.target_set_id,
            )
        ).all()
        return [_serialize_target_set_row(item) for item in records]


def list_target_set_lines(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
    target_set_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = (
            select(TargetSetLineRecordModel)
            .join(TargetSetRecordModel, TargetSetRecordModel.target_set_id == TargetSetLineRecordModel.target_set_id)
            .join(TaxonomyRecordModel, TaxonomyRecordModel.taxonomy_id == TargetSetRecordModel.taxonomy_id)
            .where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        )
        if taxonomy_id:
            statement = statement.where(TargetSetRecordModel.taxonomy_id == taxonomy_id)
        if target_set_id:
            statement = statement.where(TargetSetLineRecordModel.target_set_id == target_set_id)
        records = session.scalars(
            statement.order_by(
                TargetSetLineRecordModel.target_set_id,
                TargetSetLineRecordModel.target_member_type,
                TargetSetLineRecordModel.target_member_id,
                TargetSetLineRecordModel.target_line_id,
            )
        ).all()
        return [_serialize_target_set_line_row(item) for item in records]


def create_taxonomy_assignment(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    target_scope: str,
    target_entity_id: str,
    taxonomy_node_id: str,
    status: str,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")
        if not _taxonomy_allows_assignment_scope(taxonomy, target_scope):
            raise ValueError("Assignment target_scope is not allowed for this taxonomy.")

        node = session.scalar(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
            )
        )
        if node is None:
            raise ValueError("Taxonomy node not found.")
        if not node.is_terminal:
            raise ValueError("Assignments must reference a terminal taxonomy node.")

        existing = session.scalar(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyAssignmentRecordModel.target_scope == target_scope,
                TaxonomyAssignmentRecordModel.target_entity_id == target_entity_id.strip(),
            )
        )
        if existing is not None:
            raise ValueError("An assignment for this entity already exists.")

        record = TaxonomyAssignmentRecordModel(
            assignment_id=_next_taxonomy_assignment_id(session),
            taxonomy_id=taxonomy_id,
            target_scope=target_scope,
            target_entity_id=target_entity_id.strip(),
            taxonomy_node_id=taxonomy_node_id,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.flush()
        if target_scope == "instrument":
            _refresh_portfolio_instrument_universe_records(
                session,
                portfolio_id,
                {target_entity_id.strip()},
            )
        session.commit()
        return _serialize_taxonomy_assignment_row(record)


def update_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    assignment_id: str,
    *,
    taxonomy_node_id: str | None = UNSET,
    status: str | None = UNSET,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")

        record = session.scalar(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyAssignmentRecordModel.assignment_id == assignment_id,
            )
        )
        if record is None:
            raise ValueError("Taxonomy assignment not found.")

        if taxonomy_node_id is not UNSET and taxonomy_node_id is not None:
            node = session.scalar(
                select(TaxonomyNodeRecordModel).where(
                    TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                    TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
                )
            )
            if node is None:
                raise ValueError("Taxonomy node not found.")
            if not node.is_terminal:
                raise ValueError("Assignments must reference a terminal taxonomy node.")
            record.taxonomy_node_id = taxonomy_node_id

        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        existing = session.scalar(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyAssignmentRecordModel.target_scope == record.target_scope,
                TaxonomyAssignmentRecordModel.target_entity_id == record.target_entity_id,
                TaxonomyAssignmentRecordModel.assignment_id != assignment_id,
            )
        )
        if existing is not None:
            raise ValueError("An assignment for this entity already exists.")

        session.flush()
        if record.target_scope == "instrument":
            _refresh_portfolio_instrument_universe_records(
                session,
                portfolio_id,
                {record.target_entity_id},
            )
        session.commit()
        return _serialize_taxonomy_assignment_row(record)


def create_target_set(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    target_set_type: str,
    name: str,
    weight_enabled: bool,
    risk_budget_enabled: bool,
    status: str,
    notes: str | None,
    lines: list[dict[str, object]],
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")

        _validate_target_set_lines(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=comparator_taxonomy_node_id,
            target_set_type=target_set_type,
            weight_enabled=weight_enabled,
            risk_budget_enabled=risk_budget_enabled,
            status=(status or "active").strip() or "active",
            lines=lines,
        )

        record = TargetSetRecordModel(
            target_set_id=_next_target_set_id(session, taxonomy_id, target_set_type, name),
            taxonomy_id=taxonomy_id,
            comparator_taxonomy_node_id=comparator_taxonomy_node_id,
            target_set_type=target_set_type,
            name=name.strip(),
            weight_enabled=weight_enabled,
            risk_budget_enabled=risk_budget_enabled,
            status=(status or "active").strip() or "active",
            notes=(notes or "").strip() or None,
        )
        session.add(record)
        session.flush()

        for raw_line in lines:
            target_member_type, target_member_id, taxonomy_node_id = _normalize_target_line_member(raw_line)
            session.add(
                TargetSetLineRecordModel(
                    target_line_id=f"{record.target_set_id}::{target_member_type}::{target_member_id}",
                    target_set_id=record.target_set_id,
                    taxonomy_node_id=taxonomy_node_id,
                    target_member_type=target_member_type,
                    target_member_id=target_member_id,
                    target_weight=(
                        float(raw_line["target_weight"])
                        if raw_line.get("target_weight") is not None
                        else None
                    ),
                    target_risk_share=(
                        float(raw_line["target_risk_share"])
                        if raw_line.get("target_risk_share") is not None
                        else None
                    ),
                    notes=(str(raw_line.get("notes")).strip() if raw_line.get("notes") else None),
                )
            )

        session.commit()
        return _serialize_target_set_row(record)


def update_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    target_set_id: str,
    *,
    name: str | None = UNSET,
    weight_enabled: bool | None = UNSET,
    risk_budget_enabled: bool | None = UNSET,
    status: str | None = UNSET,
    notes: str | None = UNSET,
    lines: list[dict[str, object]] | None = UNSET,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")

        record = session.scalar(
            select(TargetSetRecordModel).where(
                TargetSetRecordModel.taxonomy_id == taxonomy_id,
                TargetSetRecordModel.target_set_id == target_set_id,
            )
        )
        if record is None:
            raise ValueError("Target set not found.")

        existing_lines = session.scalars(
            select(TargetSetLineRecordModel).where(TargetSetLineRecordModel.target_set_id == target_set_id)
        ).all()
        resolved_weight_enabled = record.weight_enabled if weight_enabled is UNSET else bool(weight_enabled)
        resolved_risk_budget_enabled = (
            record.risk_budget_enabled if risk_budget_enabled is UNSET else bool(risk_budget_enabled)
        )
        resolved_lines = (
            [
                {
                    "target_member_type": item.target_member_type,
                    "target_member_id": item.target_member_id,
                    "taxonomy_node_id": item.taxonomy_node_id,
                    "target_weight": item.target_weight,
                    "target_risk_share": item.target_risk_share,
                    "notes": item.notes,
                }
                for item in existing_lines
            ]
            if lines is UNSET
            else list(lines or [])
        )

        _validate_target_set_lines(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=record.comparator_taxonomy_node_id,
            target_set_type=record.target_set_type,
            weight_enabled=resolved_weight_enabled,
            risk_budget_enabled=resolved_risk_budget_enabled,
            status=(record.status if status is UNSET else ((status or "active").strip() or "active")),
            lines=resolved_lines,
            exclude_target_set_id=target_set_id,
        )

        if name is not UNSET and name is not None:
            record.name = name.strip()
        if weight_enabled is not UNSET:
            record.weight_enabled = resolved_weight_enabled
        if risk_budget_enabled is not UNSET:
            record.risk_budget_enabled = resolved_risk_budget_enabled
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"
        if notes is not UNSET:
            record.notes = (notes or "").strip() or None

        if lines is not UNSET:
            session.execute(delete(TargetSetLineRecordModel).where(TargetSetLineRecordModel.target_set_id == target_set_id))
            session.flush()
            for raw_line in resolved_lines:
                target_member_type, target_member_id, taxonomy_node_id = _normalize_target_line_member(raw_line)
                session.add(
                    TargetSetLineRecordModel(
                        target_line_id=f"{target_set_id}::{target_member_type}::{target_member_id}",
                        target_set_id=target_set_id,
                        taxonomy_node_id=taxonomy_node_id,
                        target_member_type=target_member_type,
                        target_member_id=target_member_id,
                        target_weight=(
                            float(raw_line["target_weight"])
                            if raw_line.get("target_weight") is not None
                            else None
                        ),
                        target_risk_share=(
                            float(raw_line["target_risk_share"])
                            if raw_line.get("target_risk_share") is not None
                            else None
                        ),
                        notes=(str(raw_line.get("notes")).strip() if raw_line.get("notes") else None),
                    )
                )

        session.commit()
        return _serialize_target_set_row(record)


def delete_target_set(portfolio_id: str, taxonomy_id: str, target_set_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            return False

        record = session.scalar(
            select(TargetSetRecordModel).where(
                TargetSetRecordModel.taxonomy_id == taxonomy_id,
                TargetSetRecordModel.target_set_id == target_set_id,
            )
        )
        if record is None:
            return False
        session.delete(record)
        session.commit()
        return True


def delete_taxonomy(portfolio_id: str, taxonomy_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return False
        record = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if record is None:
            return False
        if portfolio.default_planning_taxonomy_id == taxonomy_id:
            portfolio.default_planning_taxonomy_id = None
        research_settings = session.get(ResearchSettingsRecordModel, portfolio_id)
        if research_settings is not None and research_settings.planning_taxonomy_id == taxonomy_id:
            research_settings.planning_taxonomy_id = None
            research_settings.comparator_taxonomy_node_id = None
        affected_instrument_ids = {
            str(item.target_entity_id or "").strip()
            for item in session.scalars(
                select(TaxonomyAssignmentRecordModel).where(
                    TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                    TaxonomyAssignmentRecordModel.target_scope == "instrument",
                )
            ).all()
            if str(item.target_entity_id or "").strip()
        }
        session.delete(record)
        session.flush()
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        session.commit()
        return True


def delete_taxonomy_node(portfolio_id: str, taxonomy_id: str, taxonomy_node_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            return False

        record = session.scalar(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
            )
        )
        if record is None:
            return False

        child_count = int(
            session.scalar(
                select(func.count())
                .select_from(TaxonomyNodeRecordModel)
                .where(TaxonomyNodeRecordModel.parent_taxonomy_node_id == taxonomy_node_id)
            )
            or 0
        )
        if child_count:
            raise ValueError("Cannot delete taxonomy node with child nodes.")

        assignment_count = int(
            session.scalar(
                select(func.count())
                .select_from(TaxonomyAssignmentRecordModel)
                .where(TaxonomyAssignmentRecordModel.taxonomy_node_id == taxonomy_node_id)
            )
            or 0
        )
        if assignment_count:
            raise ValueError("Cannot delete taxonomy node with assignments.")

        target_line_count = int(
            session.scalar(
                select(func.count())
                .select_from(TargetSetLineRecordModel)
                .where(TargetSetLineRecordModel.taxonomy_node_id == taxonomy_node_id)
            )
            or 0
        )
        if target_line_count:
            raise ValueError("Cannot delete taxonomy node with target-set lines.")

        target_scope_count = int(
            session.scalar(
                select(func.count())
                .select_from(TargetSetRecordModel)
                .where(TargetSetRecordModel.comparator_taxonomy_node_id == taxonomy_node_id)
            )
            or 0
        )
        if target_scope_count:
            raise ValueError("Cannot delete taxonomy node while it owns child-scope target sets.")

        parent_taxonomy_node_id = record.parent_taxonomy_node_id
        session.delete(record)
        session.flush()
        if parent_taxonomy_node_id:
            remaining_child_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(TaxonomyNodeRecordModel)
                    .where(TaxonomyNodeRecordModel.parent_taxonomy_node_id == parent_taxonomy_node_id)
                )
                or 0
            )
            if remaining_child_count == 0:
                parent_record = session.get(TaxonomyNodeRecordModel, parent_taxonomy_node_id)
                if parent_record is not None:
                    parent_record.is_terminal = True
        session.commit()
        return True


def delete_taxonomy_assignment(portfolio_id: str, taxonomy_id: str, assignment_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            return False

        record = session.scalar(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyAssignmentRecordModel.assignment_id == assignment_id,
            )
        )
        if record is None:
            return False
        target_scope = record.target_scope
        target_entity_id = record.target_entity_id
        session.delete(record)
        session.flush()
        if target_scope == "instrument":
            _refresh_portfolio_instrument_universe_records(
                session,
                portfolio_id,
                {target_entity_id},
            )
        session.commit()
        return True


def set_default_planning_taxonomy(
    portfolio_id: str,
    taxonomy_id: str | None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio is None:
            return None

        resolved_taxonomy_id = str(taxonomy_id or "").strip() or None
        if resolved_taxonomy_id is None:
            portfolio.default_planning_taxonomy_id = None
            session.commit()
            return _serialize_portfolio_row(portfolio)

        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == resolved_taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Planning taxonomy not found.")
        if not taxonomy.planning_enabled:
            raise ValueError("Default planning taxonomy must be planning-enabled.")
        if taxonomy.primary_assignment_scope != "instrument":
            raise ValueError("Default planning taxonomy must use instrument assignment scope.")

        portfolio.default_planning_taxonomy_id = resolved_taxonomy_id
        session.commit()
        return _serialize_portfolio_row(portfolio)


def create_portfolio(name: str | None = None) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(select(PortfolioRecordModel)).all()
        resolved_name = (name or "").strip() or f"Portfolio {len(portfolios) + 1}"
        base_id = _slugify(resolved_name)
        candidate = base_id
        suffix = 2
        existing_ids = {item.portfolio_id for item in portfolios}
        while candidate in existing_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1

        record = PortfolioRecordModel(
            portfolio_id=candidate,
            portfolio_name=resolved_name,
            base_currency="USD",
            valuation_timezone="Asia/Shanghai",
            valuation_cutoff_policy="latest_complete_eod",
            as_of_date=date.today(),
            nav=0.0,
            day_change_value=0.0,
            day_change_pct=0.0,
            securities_count=0,
            sort_order=len(portfolios),
            default_planning_taxonomy_id=None,
            risk_policy_json=None,
        )
        session.add(record)
        session.commit()
        return _serialize_portfolio_row(record)


def copy_portfolio(portfolio_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(
            select(PortfolioRecordModel).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        source = next((item for item in portfolios if item.portfolio_id == portfolio_id), None)
        if source is None:
            return None

        copied_name = f"{source.portfolio_name} Copy"
        base_id = _slugify(copied_name)
        candidate = base_id
        suffix = 2
        existing_ids = {item.portfolio_id for item in portfolios}
        while candidate in existing_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1

        copied = PortfolioRecordModel(
            portfolio_id=candidate,
            portfolio_name=copied_name,
            base_currency=source.base_currency,
            valuation_timezone=source.valuation_timezone,
            valuation_cutoff_policy=source.valuation_cutoff_policy,
            as_of_date=source.as_of_date,
            nav=source.nav,
            day_change_value=source.day_change_value,
            day_change_pct=source.day_change_pct,
            securities_count=source.securities_count,
            sort_order=len(portfolios),
            default_planning_taxonomy_id=None,
            risk_policy_json=deepcopy(source.risk_policy_json) if isinstance(source.risk_policy_json, dict) else None,
        )
        session.add(copied)

        source_accounts = session.scalars(
            select(AccountRecordModel).where(AccountRecordModel.portfolio_id == portfolio_id)
        ).all()
        account_id_map: dict[str, str] = {}
        for account in source_accounts:
            copied_account_id = f"{account.account_id}-{candidate}"
            account_id_map[account.account_id] = copied_account_id

        for account in source_accounts:
            session.add(
                AccountRecordModel(
                    account_id=account_id_map[account.account_id],
                    portfolio_id=candidate,
                    account_name=account.account_name,
                    account_type=account.account_type,
                    currency=account.currency,
                    institution=account.institution,
                    default_settlement_cash_account_id=(
                        account_id_map.get(account.default_settlement_cash_account_id)
                        if account.default_settlement_cash_account_id
                        else None
                    ),
                    cost_basis_method=account.cost_basis_method,
                    allowed_instrument_types_json=deepcopy(account.allowed_instrument_types_json),
                    opened_at=account.opened_at,
                    closed_at=account.closed_at,
                    status=account.status,
                )
            )

        source_taxonomies = session.scalars(
            select(TaxonomyRecordModel).where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        ).all()
        taxonomy_id_map: dict[str, str] = {}
        for taxonomy in source_taxonomies:
            taxonomy_id_map[taxonomy.taxonomy_id] = f"{taxonomy.taxonomy_id}-{candidate}"

        for taxonomy in source_taxonomies:
            session.add(
                TaxonomyRecordModel(
                    taxonomy_id=taxonomy_id_map[taxonomy.taxonomy_id],
                    portfolio_id=candidate,
                    name=taxonomy.name,
                    taxonomy_type=taxonomy.taxonomy_type,
                    purpose=taxonomy.purpose,
                    primary_assignment_scope=taxonomy.primary_assignment_scope,
                    planning_enabled=taxonomy.planning_enabled,
                    budgeting_level=taxonomy.budgeting_level,
                    root_default_target_dimension=taxonomy.root_default_target_dimension,
                    status=taxonomy.status,
                    source_template_ref=taxonomy.source_template_ref,
                )
            )

        if source.default_planning_taxonomy_id:
            copied.default_planning_taxonomy_id = taxonomy_id_map.get(source.default_planning_taxonomy_id)

        source_taxonomy_nodes = session.scalars(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id.in_(list(taxonomy_id_map.keys()))
            )
        ).all()
        taxonomy_node_id_map: dict[str, str] = {}
        for node in source_taxonomy_nodes:
            taxonomy_node_id_map[node.taxonomy_node_id] = f"{node.taxonomy_node_id}-{candidate}"

        for node in source_taxonomy_nodes:
            session.add(
                TaxonomyNodeRecordModel(
                    taxonomy_node_id=taxonomy_node_id_map[node.taxonomy_node_id],
                    taxonomy_id=taxonomy_id_map.get(node.taxonomy_id, node.taxonomy_id),
                    parent_taxonomy_node_id=(
                        taxonomy_node_id_map.get(node.parent_taxonomy_node_id)
                        if node.parent_taxonomy_node_id
                        else None
                    ),
                    node_name=node.node_name,
                    node_code=node.node_code,
                    sort_order=node.sort_order,
                    is_terminal=node.is_terminal,
                    default_target_dimension=node.default_target_dimension,
                    status=node.status,
                )
            )

        source_taxonomy_assignments = session.scalars(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id.in_(list(taxonomy_id_map.keys()))
            )
        ).all()
        for assignment in source_taxonomy_assignments:
            session.add(
                TaxonomyAssignmentRecordModel(
                    assignment_id=f"{assignment.assignment_id}-{candidate}",
                    taxonomy_id=taxonomy_id_map.get(assignment.taxonomy_id, assignment.taxonomy_id),
                    target_scope=assignment.target_scope,
                    target_entity_id=assignment.target_entity_id,
                    taxonomy_node_id=taxonomy_node_id_map.get(assignment.taxonomy_node_id, assignment.taxonomy_node_id),
                    status=assignment.status,
                )
            )

        source_target_sets = session.scalars(
            select(TargetSetRecordModel).where(TargetSetRecordModel.taxonomy_id.in_(list(taxonomy_id_map.keys())))
        ).all()
        target_set_id_map: dict[str, str] = {}
        for target_set in source_target_sets:
            target_set_id_map[target_set.target_set_id] = f"{target_set.target_set_id}-{candidate}"

        for target_set in source_target_sets:
            session.add(
                TargetSetRecordModel(
                    target_set_id=target_set_id_map[target_set.target_set_id],
                    taxonomy_id=taxonomy_id_map.get(target_set.taxonomy_id, target_set.taxonomy_id),
                    comparator_taxonomy_node_id=(
                        taxonomy_node_id_map.get(target_set.comparator_taxonomy_node_id)
                        if target_set.comparator_taxonomy_node_id
                        else None
                    ),
                    target_set_type=target_set.target_set_type,
                    name=target_set.name,
                    weight_enabled=target_set.weight_enabled,
                    risk_budget_enabled=target_set.risk_budget_enabled,
                    status=target_set.status,
                    notes=target_set.notes,
                )
            )

        source_target_set_lines = session.scalars(
            select(TargetSetLineRecordModel).where(TargetSetLineRecordModel.target_set_id.in_(list(target_set_id_map.keys())))
        ).all()
        for line in source_target_set_lines:
            target_member_id = line.target_member_id
            taxonomy_node_id = line.taxonomy_node_id
            if line.target_member_type == TARGET_MEMBER_NODE:
                target_member_id = taxonomy_node_id_map.get(line.target_member_id, line.target_member_id)
                taxonomy_node_id = taxonomy_node_id_map.get(line.taxonomy_node_id, line.taxonomy_node_id)
            session.add(
                TargetSetLineRecordModel(
                    target_line_id=f"{line.target_line_id}-{candidate}",
                    target_set_id=target_set_id_map.get(line.target_set_id, line.target_set_id),
                    taxonomy_node_id=taxonomy_node_id,
                    target_member_type=line.target_member_type,
                    target_member_id=target_member_id,
                    target_weight=line.target_weight,
                    target_risk_share=line.target_risk_share,
                    notes=line.notes,
                )
            )

        all_transactions = [
            _serialize_transaction_row(item)
            for item in session.scalars(select(TransactionRecordModel)).all()
        ]
        source_transactions = [
            item for item in all_transactions if item.get("portfolio_id") == portfolio_id
        ]
        existing_transactions = list(all_transactions)
        for transaction in source_transactions:
            copied_transaction = deepcopy(transaction)
            copied_transaction["transaction_id"] = _next_transaction_id_from_values(
                [str(item.get("transaction_id") or "") for item in existing_transactions]
            )
            copied_transaction["portfolio_id"] = candidate
            if isinstance(copied_transaction.get("account_id"), str):
                copied_transaction["account_id"] = account_id_map.get(
                    str(copied_transaction["account_id"]),
                    copied_transaction["account_id"],
                )
            if isinstance(copied_transaction.get("settlement_cash_account_id"), str):
                copied_transaction["settlement_cash_account_id"] = account_id_map.get(
                    str(copied_transaction["settlement_cash_account_id"]),
                    copied_transaction["settlement_cash_account_id"],
                )
            if isinstance(copied_transaction.get("counterparty_account_id"), str):
                copied_transaction["counterparty_account_id"] = account_id_map.get(
                    str(copied_transaction["counterparty_account_id"]),
                    copied_transaction["counterparty_account_id"],
                )
            session.add(
                TransactionRecordModel(
                    transaction_id=str(copied_transaction["transaction_id"]),
                    portfolio_id=str(copied_transaction["portfolio_id"]),
                    transaction_type=str(copied_transaction["transaction_type"]),
                    trade_date=date.fromisoformat(str(copied_transaction["trade_date"])),
                    trade_time=str(copied_transaction["trade_time"]),
                    trade_at=str(copied_transaction["trade_at"]),
                    trade_timezone=str(copied_transaction["trade_timezone"]),
                    trade_time_is_estimated=bool(copied_transaction["trade_time_is_estimated"]),
                    settlement_date=date.fromisoformat(str(copied_transaction["settlement_date"])),
                    entitlement_date=(
                        date.fromisoformat(str(copied_transaction["entitlement_date"]))
                        if copied_transaction.get("entitlement_date")
                        else None
                    ),
                    acquisition_date=(
                        date.fromisoformat(str(copied_transaction["acquisition_date"]))
                        if copied_transaction.get("acquisition_date")
                        else None
                    ),
                    account_id=str(copied_transaction["account_id"]),
                    settlement_cash_account_id=(
                        str(copied_transaction["settlement_cash_account_id"])
                        if copied_transaction.get("settlement_cash_account_id")
                        else None
                    ),
                    instrument_id=(
                        str(copied_transaction["instrument_id"])
                        if copied_transaction.get("instrument_id")
                        else None
                    ),
                    instrument_ref_json=deepcopy(copied_transaction.get("instrument_ref")),
                    quantity=(
                        float(copied_transaction["quantity"])
                        if copied_transaction.get("quantity") is not None
                        else None
                    ),
                    price=(
                        float(copied_transaction["price"])
                        if copied_transaction.get("price") is not None
                        else None
                    ),
                    gross_amount=float(copied_transaction.get("gross_amount") or 0.0),
                    counter_amount=(
                        float(copied_transaction["counter_amount"])
                        if copied_transaction.get("counter_amount") is not None
                        else None
                    ),
                    fx_rate=(
                        float(copied_transaction["fx_rate"])
                        if copied_transaction.get("fx_rate") is not None
                        else None
                    ),
                    fees=float(copied_transaction.get("fees") or 0.0),
                    taxes=float(copied_transaction.get("taxes") or 0.0),
                    currency=str(copied_transaction.get("currency") or "USD"),
                    transfer_scope=(
                        str(copied_transaction["transfer_scope"])
                        if copied_transaction.get("transfer_scope")
                        else None
                    ),
                    transfer_object_type=(
                        str(copied_transaction["transfer_object_type"])
                        if copied_transaction.get("transfer_object_type")
                        else None
                    ),
                    transfer_group_id=(
                        str(copied_transaction["transfer_group_id"])
                        if copied_transaction.get("transfer_group_id")
                        else None
                    ),
                    counterparty_account_id=(
                        str(copied_transaction["counterparty_account_id"])
                        if copied_transaction.get("counterparty_account_id")
                        else None
                    ),
                    note=str(copied_transaction["note"]) if copied_transaction.get("note") else None,
                    created_at=(
                        str(copied_transaction["created_at"])
                        if copied_transaction.get("created_at")
                        else None
                    ),
                )
            )
            existing_transactions.append(copied_transaction)

        session.flush()
        _refresh_portfolio_instrument_universe_records(session, candidate)
        session.commit()
        return _serialize_portfolio_row(copied)


def delete_portfolio(portfolio_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(
            select(PortfolioRecordModel).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        if len(portfolios) <= 1:
            raise ValueError("At least one portfolio must remain.")

        target = next((item for item in portfolios if item.portfolio_id == portfolio_id), None)
        if target is None:
            return False

        session.execute(
            delete(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id.in_(
                    select(TaxonomyRecordModel.taxonomy_id).where(TaxonomyRecordModel.portfolio_id == portfolio_id)
                )
            )
        )
        session.execute(
            delete(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id.in_(
                    select(TaxonomyRecordModel.taxonomy_id).where(TaxonomyRecordModel.portfolio_id == portfolio_id)
                )
            )
        )
        session.execute(delete(PortfolioDailyContributionSliceModel).where(PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioDailyHoldingSnapshotModel).where(PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioDailySnapshotModel).where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioCalculationStateModel).where(PortfolioCalculationStateModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioInstrumentUniverseRecordModel).where(PortfolioInstrumentUniverseRecordModel.portfolio_id == portfolio_id))
        session.execute(delete(TaxonomyRecordModel).where(TaxonomyRecordModel.portfolio_id == portfolio_id))
        session.execute(delete(TransactionRecordModel).where(TransactionRecordModel.portfolio_id == portfolio_id))
        session.execute(delete(AccountRecordModel).where(AccountRecordModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_id == portfolio_id))

        remaining = [
            item
            for item in session.scalars(
                select(PortfolioRecordModel).order_by(
                    PortfolioRecordModel.sort_order,
                    PortfolioRecordModel.portfolio_id,
                )
            ).all()
        ]
        for index, item in enumerate(remaining):
            item.sort_order = index
        session.commit()
        return True


def reorder_portfolios(portfolio_ids: list[str]) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(
            select(PortfolioRecordModel).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        by_id = {item.portfolio_id: item for item in portfolios}
        ordered_ids = [portfolio_id for portfolio_id in portfolio_ids if portfolio_id in by_id]
        remaining_ids = [item.portfolio_id for item in portfolios if item.portfolio_id not in ordered_ids]
        final_ids = ordered_ids + remaining_ids
        for index, portfolio_id in enumerate(final_ids):
            by_id[portfolio_id].sort_order = index
        session.commit()
        reordered = session.scalars(
            select(PortfolioRecordModel).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_name,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        return [_serialize_portfolio_row(item) for item in reordered]


def list_accounts(portfolio_id: str) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        accounts = session.scalars(
            select(AccountRecordModel)
            .where(AccountRecordModel.portfolio_id == portfolio_id)
            .order_by(
                AccountRecordModel.account_type,
                AccountRecordModel.account_name,
                AccountRecordModel.account_id,
            )
        ).all()
        return [_serialize_account_row(item) for item in accounts]


def get_account(portfolio_id: str, account_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(AccountRecordModel).where(
                AccountRecordModel.portfolio_id == portfolio_id,
                AccountRecordModel.account_id == account_id,
            )
        )
        if record is None:
            return None
        return _serialize_account_row(record)


def get_transaction(portfolio_id: str, transaction_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.transaction_id == transaction_id,
            )
        )
        if record is None:
            return None
        return _serialize_transaction_row(record)


def create_account(
    portfolio_id: str,
    *,
    account_name: str,
    account_type: str,
    currency: str,
    institution: str | None,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
    allowed_instrument_types: list[str] | None,
    opened_at: date | None,
    closed_at: date | None,
    status: str,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        base = _slugify(account_name)
        prefix = "cash" if account_type == "deposit_account" else "broker"
        existing_account_ids = session.scalars(
            select(AccountRecordModel.account_id).where(
                AccountRecordModel.account_id.like(f"{prefix}-{base}%")
            )
        ).all()
        record = AccountRecordModel(
            account_id=_next_account_id(list(existing_account_ids), account_name, account_type),
            portfolio_id=portfolio_id,
            account_name=account_name.strip(),
            account_type=account_type,
            currency=currency.upper(),
            institution=(institution or "").strip() or None,
            default_settlement_cash_account_id=default_settlement_cash_account_id,
            cost_basis_method=cost_basis_method,
            allowed_instrument_types_json=sorted(set(allowed_instrument_types or [])) or None,
            opened_at=opened_at,
            closed_at=closed_at,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.commit()
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=opened_at)
        return _serialize_account_row(record)


def update_account(
    portfolio_id: str,
    account_id: str,
    *,
    account_name: str,
    institution: str | None,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
    allowed_instrument_types: list[str] | None,
    opened_at: date | None,
    closed_at: date | None,
    status: str,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(AccountRecordModel).where(
                AccountRecordModel.portfolio_id == portfolio_id,
                AccountRecordModel.account_id == account_id,
            )
        )
        if record is None:
            return None

        previous_opened_at = record.opened_at
        previous_cost_basis_method = record.cost_basis_method
        first_instrument_transaction_date = None
        if cost_basis_method != previous_cost_basis_method:
            first_instrument_transaction_date = session.scalar(
                select(func.min(TransactionRecordModel.trade_date)).where(
                    TransactionRecordModel.portfolio_id == portfolio_id,
                    or_(
                        TransactionRecordModel.account_id == account_id,
                        TransactionRecordModel.counterparty_account_id == account_id,
                    ),
                    or_(
                        TransactionRecordModel.instrument_id.is_not(None),
                        TransactionRecordModel.transfer_object_type == "position",
                    ),
                )
            )

        record.account_name = account_name.strip()
        record.institution = (institution or "").strip() or None
        record.default_settlement_cash_account_id = default_settlement_cash_account_id
        record.cost_basis_method = cost_basis_method
        record.allowed_instrument_types_json = sorted(set(allowed_instrument_types or [])) or None
        record.opened_at = opened_at
        record.closed_at = closed_at
        record.status = (status or "active").strip() or "active"
        session.commit()
        dirty_from = min(
            (
                candidate
                for candidate in (first_instrument_transaction_date, previous_opened_at, opened_at)
                if candidate is not None
            ),
            default=None,
        )
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)
        return _serialize_account_row(record)


def list_transactions(
    portfolio_id: str,
    *,
    account_id: str | None = None,
    transaction_type: str | None = None,
    instrument_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(TransactionRecordModel).where(TransactionRecordModel.portfolio_id == portfolio_id)
        if account_id:
            statement = statement.where(
                or_(
                    TransactionRecordModel.account_id == account_id,
                    and_(
                        TransactionRecordModel.transaction_type == "fx_conversion",
                        TransactionRecordModel.counterparty_account_id == account_id,
                    ),
                )
            )
        if transaction_type:
            statement = statement.where(TransactionRecordModel.transaction_type == transaction_type)
        if instrument_id:
            statement = statement.where(TransactionRecordModel.instrument_id == instrument_id)
        if start_date is not None:
            statement = statement.where(TransactionRecordModel.trade_date >= start_date)
        if end_date is not None:
            statement = statement.where(TransactionRecordModel.trade_date <= end_date)

        records = session.scalars(
            statement.order_by(
                TransactionRecordModel.trade_date.desc(),
                TransactionRecordModel.trade_at.desc(),
                TransactionRecordModel.created_at.desc(),
                TransactionRecordModel.transaction_id.desc(),
                TransactionRecordModel.settlement_date.desc(),
            )
        ).all()
        return [_serialize_transaction_row(item) for item in records]


def _mark_daily_snapshots_stale(portfolio_id: str, *, dirty_from: date | None = None) -> None:
    from portfolio_app.services.daily_snapshots import mark_portfolio_daily_snapshots_stale

    mark_portfolio_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)


def create_transaction(
    portfolio_id: str,
    *,
    transaction_type: str,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    quantity: float | None,
    price: float | None,
    gross_amount: float,
    counter_amount: float | None,
    fx_rate: float | None,
    fees: float,
    taxes: float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    note: str | None,
    created_at: str | None = None,
) -> dict[str, object]:
    records = create_transactions(
        portfolio_id=portfolio_id,
        records=[
            {
                "transaction_type": transaction_type,
                "trade_date": trade_date,
                "trade_time": trade_time,
                "settlement_date": settlement_date,
                "entitlement_date": entitlement_date,
                "acquisition_date": acquisition_date,
                "account_id": account_id,
                "settlement_cash_account_id": settlement_cash_account_id,
                "instrument_id": instrument_id,
                "instrument_ref": instrument_ref,
                "quantity": quantity,
                "price": price,
                "gross_amount": gross_amount,
                "counter_amount": counter_amount,
                "fx_rate": fx_rate,
                "fees": fees,
                "taxes": taxes,
                "currency": currency,
                "transfer_scope": transfer_scope,
                "transfer_object_type": transfer_object_type,
                "transfer_group_id": transfer_group_id,
                "counterparty_account_id": counterparty_account_id,
                "note": note,
                "created_at": created_at,
            }
        ],
    )
    return records[0]


def create_transactions(
    portfolio_id: str,
    *,
    records: list[dict[str, Any]],
) -> list[dict[str, object]]:
    if not records:
        return []

    session_factory = get_session_factory()
    with session_factory() as session:
        created: list[TransactionRecordModel] = []
        for values in records:
            record = TransactionRecordModel(
                transaction_id=_next_transaction_id(session),
                portfolio_id=portfolio_id,
            )
            _apply_transaction_record(
                record,
                transaction_type=str(values["transaction_type"]),
                trade_date=values["trade_date"],
                trade_time=values.get("trade_time"),
                settlement_date=values["settlement_date"],
                entitlement_date=values.get("entitlement_date"),
                acquisition_date=values.get("acquisition_date"),
                account_id=str(values["account_id"]),
                settlement_cash_account_id=(
                    str(values["settlement_cash_account_id"])
                    if values.get("settlement_cash_account_id")
                    else None
                ),
                instrument_id=str(values["instrument_id"]) if values.get("instrument_id") else None,
                instrument_ref=(
                    values["instrument_ref"]
                    if isinstance(values.get("instrument_ref"), dict)
                    else None
                ),
                quantity=values.get("quantity"),
                price=values.get("price"),
                gross_amount=float(values["gross_amount"]),
                counter_amount=values.get("counter_amount"),
                fx_rate=values.get("fx_rate"),
                fees=float(values["fees"]),
                taxes=float(values["taxes"]),
                currency=str(values["currency"]),
                transfer_scope=str(values["transfer_scope"]) if values.get("transfer_scope") else None,
                transfer_object_type=(
                    str(values["transfer_object_type"]) if values.get("transfer_object_type") else None
                ),
                transfer_group_id=str(values["transfer_group_id"]) if values.get("transfer_group_id") else None,
                counterparty_account_id=(
                    str(values["counterparty_account_id"])
                    if values.get("counterparty_account_id")
                    else None
                ),
                note=str(values["note"]) if values.get("note") is not None else None,
                created_at=str(values.get("created_at") or _current_utc_timestamp()),
            )
            session.add(record)
            session.flush()
            created.append(record)
        dirty_from = min((record.trade_date for record in created), default=None)
        affected_instrument_ids = {
            str(record.instrument_id or "").strip()
            for record in created
            if str(record.instrument_id or "").strip()
        }
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        session.commit()
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)
        return [_serialize_transaction_row(record) for record in created]


def update_transaction(
    portfolio_id: str,
    transaction_id: str,
    *,
    transaction_type: str,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    quantity: float | None,
    price: float | None,
    gross_amount: float,
    counter_amount: float | None,
    fx_rate: float | None,
    fees: float,
    taxes: float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    note: str | None,
    created_at: str | None = None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.transaction_id == transaction_id,
            )
        )
        if record is None:
            return None
        previous_trade_date = record.trade_date
        previous_instrument_id = str(record.instrument_id or "").strip()
        _apply_transaction_record(
            record,
            transaction_type=transaction_type,
            trade_date=trade_date,
            trade_time=trade_time,
            settlement_date=settlement_date,
            entitlement_date=entitlement_date,
            acquisition_date=acquisition_date,
            account_id=account_id,
            settlement_cash_account_id=settlement_cash_account_id,
            instrument_id=instrument_id,
            instrument_ref=instrument_ref,
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            counter_amount=counter_amount,
            fx_rate=fx_rate,
            fees=fees,
            taxes=taxes,
            currency=currency,
            transfer_scope=transfer_scope,
            transfer_object_type=transfer_object_type,
            transfer_group_id=transfer_group_id,
            counterparty_account_id=counterparty_account_id,
            note=note,
            created_at=created_at or record.created_at or _current_utc_timestamp(),
        )
        dirty_from = min(
            (candidate for candidate in (previous_trade_date, record.trade_date) if candidate is not None),
            default=None,
        )
        affected_instrument_ids = {
            instrument_id
            for instrument_id in {previous_instrument_id, str(record.instrument_id or "").strip()}
            if instrument_id
        }
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        session.commit()
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)
        return _serialize_transaction_row(record)


def delete_transactions(
    portfolio_id: str,
    *,
    transaction_ids: list[str],
) -> list[dict[str, object]]:
    normalized_transaction_ids = [transaction_id.strip() for transaction_id in transaction_ids if transaction_id.strip()]
    if not normalized_transaction_ids:
        return []

    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.transaction_id.in_(normalized_transaction_ids),
            )
        ).all()
        serialized = [_serialize_transaction_row(record) for record in records]
        affected_instrument_ids = {
            str(record.instrument_id or "").strip()
            for record in records
            if str(record.instrument_id or "").strip()
        }
        for record in records:
            session.delete(record)
        session.flush()
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        deleted_dates = [
            parsed_date
            for parsed_date in (_safe_date(record.get("trade_date")) for record in serialized)
            if parsed_date is not None
        ]
        dirty_from = min(deleted_dates, default=None)
        session.commit()
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)
        return serialized


def _apply_transaction_record(
    record: TransactionRecordModel,
    *,
    transaction_type: str,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    quantity: float | None,
    price: float | None,
    gross_amount: float,
    counter_amount: float | None,
    fx_rate: float | None,
    fees: float,
    taxes: float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    note: str | None,
    created_at: str,
) -> None:
    if isinstance(instrument_ref, dict):
        _validate_instrument_ref_contract(
            instrument_ref,
            context=f"Transaction '{record.transaction_id}'",
            expected_instrument_id=instrument_id,
        )
    elif instrument_id:
        raise ValueError(f"Transaction '{record.transaction_id}' with instrument_id requires instrument_ref.")

    resolved_timing = resolve_trade_timing(trade_date=trade_date, trade_time=trade_time)
    record.transaction_type = transaction_type
    record.trade_date = trade_date
    record.trade_time = str(resolved_timing["trade_time"])
    record.trade_at = str(resolved_timing["trade_at"])
    record.trade_timezone = str(resolved_timing["trade_timezone"])
    record.trade_time_is_estimated = bool(resolved_timing["trade_time_is_estimated"])
    record.settlement_date = settlement_date
    record.entitlement_date = entitlement_date
    record.acquisition_date = acquisition_date
    record.account_id = account_id
    record.settlement_cash_account_id = settlement_cash_account_id
    record.instrument_id = instrument_id
    record.instrument_ref_json = deepcopy(instrument_ref) if isinstance(instrument_ref, dict) else None
    record.quantity = quantity
    record.price = price
    record.gross_amount = gross_amount
    record.counter_amount = counter_amount
    record.fx_rate = fx_rate
    record.fees = fees
    record.taxes = taxes
    record.currency = currency.upper()
    record.transfer_scope = transfer_scope
    record.transfer_object_type = transfer_object_type
    record.transfer_group_id = transfer_group_id
    record.counterparty_account_id = counterparty_account_id
    record.note = (note or "").strip() or None
    record.created_at = created_at
