from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, cast, delete, func, or_, select

from app.core.settings import get_settings
from app.db.models import (
    AccountRecordModel,
    PortfolioRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
    TransactionRecordModel,
)
from app.db.session import get_session_factory

EMPTY_STORE: dict[str, list[dict[str, Any]]] = {
    "portfolios": [],
    "accounts": [],
    "transactions": [],
    "taxonomies": [],
    "taxonomy_nodes": [],
    "taxonomy_assignments": [],
    "target_sets": [],
    "target_set_lines": [],
}

UNSET = object()
TARGET_SET_EPSILON = 0.0005
TARGET_MEMBER_NODE = "taxonomy_node"


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
        if isinstance(account, dict) and "allowed_asset_types" not in account:
            account["allowed_asset_types"] = None
    for transaction in normalized["transactions"]:
        if not isinstance(transaction, dict):
            continue
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
            TaxonomyAssignmentRecordModel.effective_from,
            TaxonomyAssignmentRecordModel.assignment_id,
        )
    ).all()
    target_sets = session.scalars(
        select(TargetSetRecordModel).order_by(
            TargetSetRecordModel.taxonomy_id,
            TargetSetRecordModel.comparator_taxonomy_node_id,
            TargetSetRecordModel.target_set_type,
            TargetSetRecordModel.effective_from,
            TargetSetRecordModel.target_set_id,
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
                "allowed_asset_types": deepcopy(item.allowed_asset_types_json),
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
                "account_id": item.account_id,
                "settlement_cash_account_id": item.settlement_cash_account_id,
                "asset_id": item.asset_id,
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
                "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
                "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
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
                "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
                "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
                "status": item.status,
            }
            for item in taxonomy_assignments
        ],
        "target_sets": [
            {
                "target_set_id": item.target_set_id,
                "taxonomy_id": item.taxonomy_id,
                "comparator_taxonomy_node_id": item.comparator_taxonomy_node_id,
                "target_set_type": item.target_set_type,
                "name": item.name,
                "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
                "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
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
                allowed_asset_types_json=(
                    sorted(
                        {
                            str(item).strip()
                            for item in raw_account.get("allowed_asset_types", [])
                            if str(item).strip()
                        }
                    )
                    if isinstance(raw_account.get("allowed_asset_types"), list)
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
        effective_from = raw_taxonomy.get("effective_from")
        effective_to = raw_taxonomy.get("effective_to")
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
                effective_from=date.fromisoformat(str(effective_from)) if effective_from else None,
                effective_to=date.fromisoformat(str(effective_to)) if effective_to else None,
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

    for raw_assignment in list(normalized.get("taxonomy_assignments", [])):
        if not isinstance(raw_assignment, dict):
            continue
        effective_from = raw_assignment.get("effective_from")
        effective_to = raw_assignment.get("effective_to")
        session.add(
            TaxonomyAssignmentRecordModel(
                assignment_id=str(raw_assignment.get("assignment_id") or "").strip(),
                taxonomy_id=str(raw_assignment.get("taxonomy_id") or "").strip(),
                target_scope=str(raw_assignment.get("target_scope") or "instrument").strip() or "instrument",
                target_entity_id=str(raw_assignment.get("target_entity_id") or "").strip(),
                taxonomy_node_id=str(raw_assignment.get("taxonomy_node_id") or "").strip(),
                effective_from=date.fromisoformat(str(effective_from)) if effective_from else None,
                effective_to=date.fromisoformat(str(effective_to)) if effective_to else None,
                status=str(raw_assignment.get("status") or "active").strip() or "active",
            )
        )

    for raw_target_set in list(normalized.get("target_sets", [])):
        if not isinstance(raw_target_set, dict):
            continue
        effective_from = raw_target_set.get("effective_from")
        effective_to = raw_target_set.get("effective_to")
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
                effective_from=date.fromisoformat(str(effective_from)) if effective_from else None,
                effective_to=date.fromisoformat(str(effective_to)) if effective_to else None,
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

    for raw_transaction in list(normalized.get("transactions", [])):
        if not isinstance(raw_transaction, dict):
            continue
        entitlement_date = raw_transaction.get("entitlement_date")
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
                account_id=str(raw_transaction.get("account_id") or "").strip(),
                settlement_cash_account_id=(
                    str(raw_transaction.get("settlement_cash_account_id")).strip()
                    if raw_transaction.get("settlement_cash_account_id")
                    else None
                ),
                asset_id=str(raw_transaction.get("asset_id")).strip() if raw_transaction.get("asset_id") else None,
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
    }


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
        "allowed_asset_types": deepcopy(item.allowed_asset_types_json),
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
        "account_id": item.account_id,
        "settlement_cash_account_id": item.settlement_cash_account_id,
        "asset_id": item.asset_id,
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
        "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
        "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
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
        "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
        "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
        "status": item.status,
    }


def _serialize_target_set_row(item: TargetSetRecordModel) -> dict[str, object]:
    return {
        "target_set_id": item.target_set_id,
        "taxonomy_id": item.taxonomy_id,
        "comparator_taxonomy_node_id": item.comparator_taxonomy_node_id,
        "target_set_type": item.target_set_type,
        "name": item.name,
        "effective_from": item.effective_from.isoformat() if item.effective_from is not None else None,
        "effective_to": item.effective_to.isoformat() if item.effective_to is not None else None,
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


def _periods_overlap(
    start_date: date | None,
    end_date: date | None,
    other_start_date: date | None,
    other_end_date: date | None,
) -> bool:
    resolved_start = start_date or date.min
    resolved_end = end_date or date.max
    resolved_other_start = other_start_date or date.min
    resolved_other_end = other_end_date or date.max
    return resolved_start <= resolved_other_end and resolved_other_start <= resolved_end


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

    direct_assignments = session.execute(
        select(
            TaxonomyAssignmentRecordModel.target_scope,
            TaxonomyAssignmentRecordModel.target_entity_id,
        )
        .where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id == comparator_taxonomy_node_id,
            TaxonomyAssignmentRecordModel.status == "active",
        )
        .distinct()
        .order_by(
            TaxonomyAssignmentRecordModel.target_scope,
            TaxonomyAssignmentRecordModel.target_entity_id,
        )
    ).all()
    if not direct_assignments:
        raise ValueError("Comparator scope must have active child sleeves or directly assigned instruments.")

    return parent_node, [
        {
            "target_member_type": str(item.target_scope),
            "target_member_id": str(item.target_entity_id),
            "taxonomy_node_id": None,
            "label": str(item.target_entity_id),
        }
        for item in direct_assignments
    ]


def _validate_target_set_lines(
    session,
    *,
    taxonomy: TaxonomyRecordModel,
    comparator_taxonomy_node_id: str | None,
    target_set_type: str,
    effective_from: date | None,
    effective_to: date | None,
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
    if effective_from and effective_to and effective_to < effective_from:
        raise ValueError("effective_to must not be earlier than effective_from.")
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
        elif target_risk_share is not None:
            raise ValueError("target_risk_share must be empty when risk_budget is disabled.")

    if seen_member_keys != expected_member_keys:
        raise ValueError("Target set lines must cover every direct member in the selected scope.")

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
            if _periods_overlap(effective_from, effective_to, existing.effective_from, existing.effective_to):
                if target_set_type == "saa":
                    raise ValueError("An active SAA target set already overlaps this scope and effective period.")
                raise ValueError("An active TAA target set already overlaps this scope and effective period.")

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
        return [_serialize_portfolio_row(item) for item in portfolios]


def get_portfolio(portfolio_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(PortfolioRecordModel, portfolio_id)
        if record is None:
            return None
        return _serialize_portfolio_row(record)


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
    effective_from: date | None,
    effective_to: date | None,
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
            effective_from=effective_from,
            effective_to=effective_to,
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
    effective_from: date | None = UNSET,
    effective_to: date | None = UNSET,
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

        resolved_effective_from = record.effective_from if effective_from is UNSET else effective_from
        resolved_effective_to = record.effective_to if effective_to is UNSET else effective_to
        if (
            resolved_effective_from is not None
            and resolved_effective_to is not None
            and resolved_effective_to < resolved_effective_from
        ):
            raise ValueError("effective_to must not be earlier than effective_from.")

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
        if effective_from is not UNSET:
            record.effective_from = resolved_effective_from
        if effective_to is not UNSET:
            record.effective_to = resolved_effective_to
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
                TaxonomyAssignmentRecordModel.effective_from,
                TaxonomyAssignmentRecordModel.assignment_id,
            )
        ).all()
        return [_serialize_taxonomy_assignment_row(item) for item in assignments]


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
                TargetSetRecordModel.effective_from,
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
    effective_from: date | None,
    effective_to: date | None,
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
                TaxonomyAssignmentRecordModel.effective_from == effective_from,
            )
        )
        if existing is not None:
            raise ValueError("An assignment for this entity and effective_from already exists.")

        record = TaxonomyAssignmentRecordModel(
            assignment_id=_next_taxonomy_assignment_id(session),
            taxonomy_id=taxonomy_id,
            target_scope=target_scope,
            target_entity_id=target_entity_id.strip(),
            taxonomy_node_id=taxonomy_node_id,
            effective_from=effective_from,
            effective_to=effective_to,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.commit()
        return _serialize_taxonomy_assignment_row(record)


def update_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    assignment_id: str,
    *,
    taxonomy_node_id: str | None = UNSET,
    effective_from: date | None = UNSET,
    effective_to: date | None = UNSET,
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

        resolved_effective_from = record.effective_from if effective_from is UNSET else effective_from
        resolved_effective_to = record.effective_to if effective_to is UNSET else effective_to
        if (
            resolved_effective_from is not None
            and resolved_effective_to is not None
            and resolved_effective_to < resolved_effective_from
        ):
            raise ValueError("effective_to must not be earlier than effective_from.")

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

        if effective_from is not UNSET:
            record.effective_from = resolved_effective_from
        if effective_to is not UNSET:
            record.effective_to = resolved_effective_to
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        existing = session.scalar(
            select(TaxonomyAssignmentRecordModel).where(
                TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
                TaxonomyAssignmentRecordModel.target_scope == record.target_scope,
                TaxonomyAssignmentRecordModel.target_entity_id == record.target_entity_id,
                TaxonomyAssignmentRecordModel.effective_from == record.effective_from,
                TaxonomyAssignmentRecordModel.assignment_id != assignment_id,
            )
        )
        if existing is not None:
            raise ValueError("An assignment for this entity and effective_from already exists.")

        session.commit()
        return _serialize_taxonomy_assignment_row(record)


def create_target_set(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    target_set_type: str,
    name: str,
    effective_from: date | None,
    effective_to: date | None,
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
            effective_from=effective_from,
            effective_to=effective_to,
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
            effective_from=effective_from,
            effective_to=effective_to,
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
    effective_from: date | None = UNSET,
    effective_to: date | None = UNSET,
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
        resolved_effective_from = record.effective_from if effective_from is UNSET else effective_from
        resolved_effective_to = record.effective_to if effective_to is UNSET else effective_to
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
            effective_from=resolved_effective_from,
            effective_to=resolved_effective_to,
            weight_enabled=resolved_weight_enabled,
            risk_budget_enabled=resolved_risk_budget_enabled,
            status=(record.status if status is UNSET else ((status or "active").strip() or "active")),
            lines=resolved_lines,
            exclude_target_set_id=target_set_id,
        )

        if name is not UNSET and name is not None:
            record.name = name.strip()
        if effective_from is not UNSET:
            record.effective_from = resolved_effective_from
        if effective_to is not UNSET:
            record.effective_to = resolved_effective_to
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
        session.delete(record)
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
        session.delete(record)
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
                    allowed_asset_types_json=deepcopy(account.allowed_asset_types_json),
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
                    effective_from=taxonomy.effective_from,
                    effective_to=taxonomy.effective_to,
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
                    effective_from=assignment.effective_from,
                    effective_to=assignment.effective_to,
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
                    effective_from=target_set.effective_from,
                    effective_to=target_set.effective_to,
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
                    account_id=str(copied_transaction["account_id"]),
                    settlement_cash_account_id=(
                        str(copied_transaction["settlement_cash_account_id"])
                        if copied_transaction.get("settlement_cash_account_id")
                        else None
                    ),
                    asset_id=(
                        str(copied_transaction["asset_id"])
                        if copied_transaction.get("asset_id")
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


def create_account(
    portfolio_id: str,
    *,
    account_name: str,
    account_type: str,
    currency: str,
    institution: str | None,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
    allowed_asset_types: list[str] | None,
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
            allowed_asset_types_json=sorted(set(allowed_asset_types or [])) or None,
            opened_at=opened_at,
            closed_at=closed_at,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.commit()
        return _serialize_account_row(record)


def list_transactions(
    portfolio_id: str,
    *,
    account_id: str | None = None,
    transaction_type: str | None = None,
    asset_id: str | None = None,
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
        if asset_id:
            statement = statement.where(TransactionRecordModel.asset_id == asset_id)
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


def create_transaction(
    portfolio_id: str,
    *,
    transaction_type: str,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    asset_id: str | None,
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
    session_factory = get_session_factory()
    with session_factory() as session:
        resolved_timing = resolve_trade_timing(trade_date=trade_date, trade_time=trade_time)
        created_timestamp = created_at or _current_utc_timestamp()
        record = TransactionRecordModel(
            transaction_id=_next_transaction_id(session),
            portfolio_id=portfolio_id,
            transaction_type=transaction_type,
            trade_date=trade_date,
            trade_time=resolved_timing["trade_time"],
            trade_at=resolved_timing["trade_at"],
            trade_timezone=resolved_timing["trade_timezone"],
            trade_time_is_estimated=bool(resolved_timing["trade_time_is_estimated"]),
            settlement_date=settlement_date,
            entitlement_date=entitlement_date,
            account_id=account_id,
            settlement_cash_account_id=settlement_cash_account_id,
            asset_id=asset_id,
            instrument_ref_json=deepcopy(instrument_ref) if isinstance(instrument_ref, dict) else None,
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            counter_amount=counter_amount,
            fx_rate=fx_rate,
            fees=fees,
            taxes=taxes,
            currency=currency.upper(),
            transfer_scope=transfer_scope,
            transfer_object_type=transfer_object_type,
            transfer_group_id=transfer_group_id,
            counterparty_account_id=counterparty_account_id,
            note=(note or "").strip() or None,
            created_at=created_timestamp,
        )
        session.add(record)
        session.commit()
        return _serialize_transaction_row(record)
