from __future__ import annotations
from portfolio_app.services.lot_selection import resolve_import_lot_references
from studio_identity import current_principal, IdentityError

import hashlib
import json
import re
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, String, and_, cast, delete, func, inspect, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload
from investment_studio_instrument_core import SUPPORTED_FX_CURRENCIES
from investment_studio_instrument_core.db_models import InstrumentMarketData

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    AccountRecordModel,
    ConcentrationPolicyRevisionModel,
    DerivativeContractRecordModel,
    OptionDeliveryLinkModel,
    PortfolioCalculationStateModel,
    PortfolioTaxonomyStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
    PortfolioInstrumentUniverseRecordModel,
    PortfolioRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyConfigurationRevisionModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
    TransactionChangeLogModel,
    TransactionIdAllocatorModel,
    TransactionIdempotencyRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.account_categories import validate_account_category
from portfolio_app.services.ledger import (
    build_account_workspace,
    build_position_lots,
    validate_transaction_position_history,
)
from portfolio_app.services.taxonomy_configuration import (
    _record_taxonomy_configuration_revision_in_session,
)
from portfolio_app.services.snapshot_selection import default_portfolio_snapshot
from portfolio_app.services.transaction_dates import (
    transaction_affected_dates,
    transaction_performance_effective_date,
)
from portfolio_app.services.valuation_clock import portfolio_valuation_today
from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.research_eligibility import (
    contract_only_instrument_ids,
    derive_research_lifecycle,
    enrich_instrument_research_state,
)

EMPTY_STORE: dict[str, list[dict[str, Any]]] = {
    "portfolios": [],
    "accounts": [],
    "derivative_contracts": [],
    "option_delivery_links": [],
    "transactions": [],
    "taxonomies": [],
    "taxonomy_nodes": [],
    "taxonomy_assignments": [],
    "instrument_universe": [],
    "target_sets": [],
    "target_set_lines": [],
}

UNSET = object()
TARGET_SET_EPSILON = 1e-6
TARGET_MEMBER_NODE = "taxonomy_node"
TARGET_MEMBER_CASH = "cash_bucket"
TARGET_MEMBER_DERIVATIVE = "derivative_bucket"
SYSTEM_CASH_TARGET_MEMBER_ID = "__cash__"
SYSTEM_DERIVATIVE_TARGET_MEMBER_ID = "__derivatives__"
SYSTEM_CASH_TARGET_LABEL = "Cash"
LEGACY_ASSET_REFERENCE_KEYS = {"asset_id", "asset_name", "asset_type"}
INSTRUMENT_REF_REQUIRED_KEYS = {"instrument_id", "instrument_name", "instrument_type", "currency"}
QUANTITY_SOURCE_QUANTUM = Decimal("0.000000000001")
PRICE_SOURCE_QUANTUM = Decimal("0.000000000001")
AMOUNT_SOURCE_QUANTUM = Decimal("0.00000001")
SOURCE_DECIMAL_LIMITS = {
    QUANTITY_SOURCE_QUANTUM: Decimal("1e16"),
    PRICE_SOURCE_QUANTUM: Decimal("1e16"),
    AMOUNT_SOURCE_QUANTUM: Decimal("1e20"),
}


class TransactionIdempotencyKeyError(ValueError):
    pass


class TransactionIdempotencyConflictError(ValueError):
    pass


class TransactionRowVersionConflictError(ValueError):
    pass


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _transaction_source_decimal(
    value: object,
    *,
    quantum: Decimal,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"Transaction {field_name} must be a finite decimal value.") from error
    if not resolved.is_finite():
        raise ValueError(f"Transaction {field_name} must be a finite decimal value.")
    if abs(resolved) >= SOURCE_DECIMAL_LIMITS[quantum]:
        raise ValueError(
            f"Transaction {field_name} exceeds the practical source precision contract."
        )
    try:
        return resolved.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError(
            f"Transaction {field_name} exceeds the practical source precision contract."
        ) from error


def _transaction_source_from_mapping(
    values: dict[str, object],
    *,
    source_key: str,
    projection_key: str,
    quantum: Decimal,
) -> Decimal | None:
    source_value = values.get(source_key)
    if source_value is None:
        source_value = values.get(projection_key)
    return _transaction_source_decimal(
        source_value,
        quantum=quantum,
        field_name=projection_key,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _normalize_transaction_idempotency_key(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise TransactionIdempotencyKeyError(
            "Transaction idempotency key must not exceed 128 characters."
        )
    if any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise TransactionIdempotencyKeyError(
            "Transaction idempotency key must contain printable ASCII without spaces."
        )
    return normalized


def _transaction_request_hash(records: list[dict[str, Any]]) -> str:
    semantic_records = [
        {
            key: value
            for key, value in record.items()
            if key != "created_at"
        }
        for record in records
    ]
    canonical = json.dumps(
        semantic_records,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _transaction_change_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        "derivative_contracts",
        "option_delivery_links",
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
    for account in normalized["accounts"]:
        if not isinstance(account, dict):
            continue
        if "allowed_asset_types" in account or "allowed_instrument_types" in account:
            raise ValueError(
                f"Account '{account.get('account_id')}' uses a removed instrument-scope field; use account_category."
            )
        try:
            account["account_category"] = validate_account_category(
                account_type=account.get("account_type"),
                account_category=account.get("account_category"),
            )
        except ValueError as error:
            raise ValueError(
                f"Account '{account.get('account_id')}' is invalid: {error}"
            ) from error
    account_by_key = {
        (str(account.get("portfolio_id") or ""), str(account.get("account_id") or "")): account
        for account in normalized["accounts"]
        if isinstance(account, dict)
    }
    for account in normalized["accounts"]:
        if not isinstance(account, dict) or account.get("account_type") != "securities_account":
            continue
        settlement_account_id = str(
            account.get("default_settlement_cash_account_id") or ""
        )
        settlement_account = account_by_key.get(
            (str(account.get("portfolio_id") or ""), settlement_account_id)
        )
        if not settlement_account_id or settlement_account is None:
            raise ValueError(
                f"Account '{account.get('account_id')}' requires a default settlement cash account."
            )
        if settlement_account.get("account_type") != "deposit_account":
            raise ValueError(
                f"Account '{account.get('account_id')}' settlement mapping must target a cash account."
            )
        if str(settlement_account.get("currency") or "").upper() != str(
            account.get("currency") or ""
        ).upper():
            raise ValueError(
                f"Account '{account.get('account_id')}' settlement mapping must use the same currency."
            )
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
        if "position_effective_date" not in transaction:
            transaction["position_effective_date"] = None
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
        try:
            row_version = int(transaction.get("row_version") or 1)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Transaction '{transaction.get('transaction_id')}' row_version must be a positive integer."
            ) from error
        if row_version < 1:
            raise ValueError(
                f"Transaction '{transaction.get('transaction_id')}' row_version must be a positive integer."
            )
        transaction["row_version"] = row_version
    transaction_by_id = {
        str(transaction.get("transaction_id") or "").strip(): transaction
        for transaction in normalized["transactions"]
        if isinstance(transaction, dict)
    }
    seen_option_transaction_ids: set[str] = set()
    seen_stock_transaction_ids: set[str] = set()
    for link in normalized["option_delivery_links"]:
        if not isinstance(link, dict):
            continue
        option_transaction_id = str(
            link.get("option_transaction_id") or ""
        ).strip()
        stock_transaction_id = str(
            link.get("stock_transaction_id") or ""
        ).strip()
        option_transaction = transaction_by_id.get(option_transaction_id)
        stock_transaction = transaction_by_id.get(stock_transaction_id)
        if option_transaction is None or stock_transaction is None:
            raise ValueError("Option delivery links require both transaction facts.")
        if option_transaction_id in seen_option_transaction_ids:
            raise ValueError("An option outcome may have only one stock delivery link.")
        if stock_transaction_id in seen_stock_transaction_ids:
            raise ValueError("A stock delivery transaction may belong to only one option outcome.")
        if (
            str(option_transaction.get("portfolio_id") or "")
            != str(stock_transaction.get("portfolio_id") or "")
            or str(link.get("portfolio_id") or "")
            != str(option_transaction.get("portfolio_id") or "")
        ):
            raise ValueError("Option delivery transactions must belong to the same portfolio.")
        seen_option_transaction_ids.add(option_transaction_id)
        seen_stock_transaction_ids.add(stock_transaction_id)
    transaction_sequences: set[int] = set()
    for transaction in normalized["transactions"]:
        if not isinstance(transaction, dict):
            continue
        transaction_id = str(transaction.get("transaction_id") or "").strip()
        raw_sequence = transaction.get("transaction_sequence")
        if isinstance(raw_sequence, bool) or raw_sequence is None:
            raise ValueError(
                f"Transaction '{transaction_id}' requires a positive transaction_sequence."
            )
        try:
            transaction_sequence = int(raw_sequence)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Transaction '{transaction_id}' requires a positive transaction_sequence."
            ) from error
        if transaction_sequence < 1:
            raise ValueError(
                f"Transaction '{transaction_id}' requires a positive transaction_sequence."
            )
        if transaction_sequence in transaction_sequences:
            raise ValueError(
                f"Transaction sequence '{transaction_sequence}' is duplicated."
            )
        transaction["transaction_sequence"] = transaction_sequence
        transaction_sequences.add(transaction_sequence)
    for universe_record in normalized["instrument_universe"]:
        if not isinstance(universe_record, dict):
            continue
        universe_record.setdefault("research_pm_approved", False)
        universe_record.setdefault("research_pm_approved_at", None)
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
        _ensure_transaction_id_allocator(
            session,
            minimum_next_value=_next_transaction_number_from_history(session),
        )
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
            TransactionRecordModel.transaction_sequence,
            TransactionRecordModel.settlement_date,
        )
    ).all()
    derivative_contracts = session.scalars(
        select(DerivativeContractRecordModel).order_by(
            DerivativeContractRecordModel.portfolio_id,
            DerivativeContractRecordModel.contract_type,
            DerivativeContractRecordModel.derivative_contract_id,
        )
    ).all()
    option_delivery_links = session.scalars(
        select(OptionDeliveryLinkModel).order_by(
            OptionDeliveryLinkModel.portfolio_id,
            OptionDeliveryLinkModel.created_at,
            OptionDeliveryLinkModel.option_transaction_id,
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
                "inception_date": item.inception_date.isoformat(),
                "as_of_date": item.as_of_date.isoformat() if item.as_of_date is not None else None,
                "nav": item.nav,
                "day_change_value": item.day_change_value,
                "day_change_pct": item.day_change_pct,
                "securities_count": item.securities_count,
                "sort_order": item.sort_order,
            }
            for item in portfolios
        ],
        "accounts": [
            {
                "account_id": item.account_id,
                "portfolio_id": item.portfolio_id,
                "account_name": item.account_name,
                "account_type": item.account_type,
                "account_category": item.account_category,
                "currency": item.currency,
                "institution": item.institution,
                "default_settlement_cash_account_id": item.default_settlement_cash_account_id,
                "cost_basis_method": item.cost_basis_method,
                "cash_purpose": item.cash_purpose,
                "collateral_reference": item.collateral_reference,
                "opened_at": item.opened_at.isoformat() if item.opened_at is not None else None,
                "closed_at": item.closed_at.isoformat() if item.closed_at is not None else None,
                "status": item.status,
            }
            for item in accounts
        ],
        "derivative_contracts": [
            _serialize_derivative_contract_row(item)
            for item in derivative_contracts
        ],
        "option_delivery_links": [
            _serialize_option_delivery_link(item)
            for item in option_delivery_links
        ],
        "transactions": [
            {
                "transaction_id": item.transaction_id,
                "transaction_sequence": item.transaction_sequence,
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
                "asset_deliveries": deepcopy(item.asset_deliveries_json or []),
                "settlement_cashflows": deepcopy(item.settlement_cashflows_json or []),
                "lot_selections": deepcopy(item.lot_selections_json or []),
                "derivative_contract_id": item.derivative_contract_id,
                "derivative_contract": (
                    _serialize_derivative_contract_row(item.derivative_contract)
                    if item.derivative_contract is not None
                    else None
                ),
                "quantity": item.quantity,
                "source_quantity": _decimal_text(item.source_quantity),
                "price": item.price,
                "source_price": _decimal_text(item.source_price),
                "gross_amount": item.gross_amount,
                "source_gross_amount": _decimal_text(item.source_gross_amount),
                "counter_amount": item.counter_amount,
                "source_counter_amount": _decimal_text(item.source_counter_amount),
                "fx_rate": item.fx_rate,
                "source_fx_rate": _decimal_text(item.source_fx_rate),
                "fees": item.fees,
                "source_fees": _decimal_text(item.source_fees),
                "fee_category": item.fee_category,
                "taxes": item.taxes,
                "source_taxes": _decimal_text(item.source_taxes),
                "currency": item.currency,
                "transfer_scope": item.transfer_scope,
                "transfer_object_type": item.transfer_object_type,
                "transfer_group_id": item.transfer_group_id,
                "counterparty_account_id": item.counterparty_account_id,
                "note": item.note,
                "created_at": item.created_at,
                "row_version": item.row_version,
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
                "allocation_basis": item.allocation_basis,
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
                "target_value": item.target_value,
                "notes": item.notes,
            }
            for item in target_set_lines
        ],
    }


def _save_store_to_db(session, data: dict[str, object]) -> None:
    normalized = _normalize_store(data)
    inception_dates: dict[str, date] = {}
    for raw_portfolio in normalized.get("portfolios", []):
        if not isinstance(raw_portfolio, dict):
            continue
        portfolio_id = str(raw_portfolio.get("portfolio_id") or "").strip()
        inception_date = _safe_date(raw_portfolio.get("inception_date"))
        if inception_date is None:
            raise ValueError(
                f"Portfolio '{portfolio_id}' requires a valid inception_date."
            )
        inception_dates[portfolio_id] = inception_date
    for transaction in normalized.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        portfolio_id = str(transaction.get("portfolio_id") or "").strip()
        inception_date = inception_dates.get(portfolio_id)
        trade_date = _safe_date(transaction.get("trade_date"))
        settlement_date = _safe_date(transaction.get("settlement_date")) or trade_date
        if inception_date is not None and (
            trade_date is None or trade_date < inception_date
        ):
            raise ValueError(
                f"Transaction '{transaction.get('transaction_id')}' must not predate "
                f"portfolio inception_date {inception_date}."
            )
        if transaction.get("transaction_type") not in {"opening_balance", "option_opening_balance", "short_opening_balance"}:
            continue
        if trade_date != inception_date or settlement_date != inception_date:
            raise ValueError(
                f"Opening balance '{transaction.get('transaction_id')}' must use "
                f"portfolio inception_date {inception_date}."
            )
    existing_tables = set(inspect(session.get_bind()).get_table_names())
    if ConcentrationPolicyRevisionModel.__tablename__ in existing_tables:
        session.execute(delete(ConcentrationPolicyRevisionModel))
    session.execute(delete(PortfolioDailyContributionSliceModel))
    session.execute(delete(PortfolioDailyHoldingSnapshotModel))
    session.execute(delete(PortfolioDailySnapshotModel))
    session.execute(delete(PortfolioCalculationStateModel))
    session.execute(delete(PortfolioInstrumentUniverseRecordModel))
    if TaxonomyConfigurationRevisionModel.__tablename__ in existing_tables:
        session.execute(delete(TaxonomyConfigurationRevisionModel))
    if PortfolioTaxonomyStateModel.__tablename__ in existing_tables:
        session.execute(delete(PortfolioTaxonomyStateModel))
    session.execute(delete(TargetSetLineRecordModel))
    session.execute(delete(TargetSetRecordModel))
    session.execute(delete(TaxonomyAssignmentRecordModel))
    session.execute(delete(TaxonomyNodeRecordModel))
    session.execute(delete(TaxonomyRecordModel))
    session.execute(delete(TransactionChangeLogModel))
    session.execute(delete(TransactionIdempotencyRecordModel))
    session.execute(delete(OptionDeliveryLinkModel))
    session.execute(delete(TransactionRecordModel))
    session.execute(delete(DerivativeContractRecordModel))
    session.execute(delete(AccountRecordModel))
    session.execute(delete(PortfolioRecordModel))

    for raw_portfolio in list(normalized.get("portfolios", [])):
        if not isinstance(raw_portfolio, dict):
            continue
        as_of_date = raw_portfolio.get("as_of_date")
        portfolio_id = str(raw_portfolio.get("portfolio_id") or "").strip()
        session.add(
            PortfolioRecordModel(
                portfolio_id=portfolio_id,
                portfolio_name=str(raw_portfolio.get("portfolio_name") or "").strip(),
                base_currency=str(raw_portfolio.get("base_currency") or "USD").strip().upper() or "USD",
                valuation_timezone=str(raw_portfolio.get("valuation_timezone") or "Asia/Shanghai").strip()
                or "Asia/Shanghai",
                valuation_cutoff_policy=str(
                    raw_portfolio.get("valuation_cutoff_policy") or "latest_complete_eod"
                ).strip()
                or "latest_complete_eod",
                inception_date=inception_dates[portfolio_id],
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
                cash_purpose=raw_account.get("cash_purpose"),
                collateral_reference=raw_account.get("collateral_reference"),
                account_type=str(raw_account.get("account_type") or "").strip(),
                account_category=str(raw_account.get("account_category") or "").strip(),
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
                opened_at=date.fromisoformat(str(opened_at)) if opened_at else None,
                closed_at=date.fromisoformat(str(closed_at)) if closed_at else None,
                status=str(raw_account.get("status") or "active").strip() or "active",
            )
        )

    for raw_contract in list(normalized.get("derivative_contracts", [])):
        if not isinstance(raw_contract, dict):
            continue
        terms = raw_contract.get("terms")
        if not isinstance(terms, dict):
            raise ValueError("Derivative contract terms must be an object.")
        session.add(
            DerivativeContractRecordModel(
                derivative_contract_id=str(
                    raw_contract.get("derivative_contract_id") or ""
                ).strip(),
                portfolio_id=str(raw_contract.get("portfolio_id") or "").strip(),
                account_id=str(raw_contract.get("account_id") or "").strip(),
                contract_name=str(raw_contract.get("contract_name") or "").strip(),
                contract_type=str(raw_contract.get("contract_type") or "").strip(),
                currency=str(raw_contract.get("currency") or "").strip().upper(),
                external_reference=(
                    str(raw_contract.get("external_reference") or "").strip() or None
                ),
                terms_json=deepcopy(terms),
                row_version=int(raw_contract.get("row_version") or 1),
                amendments_json=deepcopy(raw_contract.get("amendments") or []),
                created_at=str(
                    raw_contract.get("created_at") or _current_utc_timestamp()
                ).strip(),
            )
        )

    for raw_taxonomy in list(normalized.get("taxonomies", [])):
        if not isinstance(raw_taxonomy, dict):
            continue
        primary_assignment_scope = str(
            raw_taxonomy.get("primary_assignment_scope") or "instrument"
        ).strip() or "instrument"
        if primary_assignment_scope != "instrument":
            raise ValueError("Portfolio taxonomies support Registry instruments only.")
        session.add(
            TaxonomyRecordModel(
                taxonomy_id=str(raw_taxonomy.get("taxonomy_id") or "").strip(),
                portfolio_id=str(raw_taxonomy.get("portfolio_id") or "").strip(),
                name=str(raw_taxonomy.get("name") or "").strip(),
                taxonomy_type=str(raw_taxonomy.get("taxonomy_type") or "custom").strip() or "custom",
                purpose=(str(raw_taxonomy.get("purpose")).strip() if raw_taxonomy.get("purpose") else None),
                primary_assignment_scope="instrument",
                root_allocation_basis=(
                    str(raw_taxonomy.get("root_allocation_basis") or "weight").strip() or "weight"
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
                allocation_basis=str(raw_node.get("allocation_basis") or "weight").strip()
                or "weight",
                status=str(raw_node.get("status") or "active").strip() or "active",
            )
        )

    assignment_rows_by_key: dict[tuple[str, str, str], dict[str, object]] = {}
    for raw_assignment in list(normalized.get("taxonomy_assignments", [])):
        if not isinstance(raw_assignment, dict):
            continue
        target_scope = str(
            raw_assignment.get("target_scope") or "instrument"
        ).strip() or "instrument"
        if target_scope != "instrument":
            raise ValueError("Portfolio taxonomy assignments support Registry instruments only.")
        assignment_key = (
            str(raw_assignment.get("taxonomy_id") or "").strip(),
            "instrument",
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
                target_scope="instrument",
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
                target_value=(
                    float(raw_target_line["target_value"])
                    if raw_target_line.get("target_value") is not None
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
                research_pm_approved=bool(raw_universe_record.get("research_pm_approved")),
                research_pm_approved_at=(
                    str(raw_universe_record.get("research_pm_approved_at") or "").strip()
                    or None
                ),
                status=str(raw_universe_record.get("status") or "active").strip() or "active",
                created_at=str(raw_universe_record.get("created_at") or now).strip(),
                updated_at=str(raw_universe_record.get("updated_at") or now).strip(),
            )
        )

    for raw_transaction in list(normalized.get("transactions", [])):
        if not isinstance(raw_transaction, dict):
            continue
        transaction_sequence = int(raw_transaction["transaction_sequence"])
        entitlement_date = raw_transaction.get("entitlement_date")
        acquisition_date = raw_transaction.get("acquisition_date")
        position_effective_date = raw_transaction.get("position_effective_date")
        source_quantity = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_quantity",
            projection_key="quantity",
            quantum=QUANTITY_SOURCE_QUANTUM,
        )
        source_price = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_price",
            projection_key="price",
            quantum=PRICE_SOURCE_QUANTUM,
        )
        source_gross_amount = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_gross_amount",
            projection_key="gross_amount",
            quantum=AMOUNT_SOURCE_QUANTUM,
        ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
        source_counter_amount = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_counter_amount",
            projection_key="counter_amount",
            quantum=AMOUNT_SOURCE_QUANTUM,
        )
        source_fx_rate = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_fx_rate",
            projection_key="fx_rate",
            quantum=PRICE_SOURCE_QUANTUM,
        )
        source_fees = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_fees",
            projection_key="fees",
            quantum=AMOUNT_SOURCE_QUANTUM,
        ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
        source_taxes = _transaction_source_from_mapping(
            raw_transaction,
            source_key="source_taxes",
            projection_key="taxes",
            quantum=AMOUNT_SOURCE_QUANTUM,
        ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
        session.add(
            TransactionRecordModel(
                transaction_id=str(raw_transaction.get("transaction_id") or "").strip(),
                transaction_sequence=transaction_sequence,
                portfolio_id=str(raw_transaction.get("portfolio_id") or "").strip(),
                transaction_type=str(raw_transaction.get("transaction_type") or "").strip(),
                lifecycle_event_type=(
                    str(raw_transaction.get("lifecycle_event_type")).strip()
                    if raw_transaction.get("lifecycle_event_type")
                    else None
                ),
                trade_date=date.fromisoformat(str(raw_transaction.get("trade_date") or date.today().isoformat())),
                trade_time=str(raw_transaction.get("trade_time") or "12:00").strip() or "12:00",
                trade_at=str(raw_transaction.get("trade_at") or "").strip(),
                trade_timezone=str(raw_transaction.get("trade_timezone") or "").strip(),
                trade_time_is_estimated=bool(raw_transaction.get("trade_time_is_estimated")),
                settlement_date=date.fromisoformat(
                    str(raw_transaction.get("settlement_date") or date.today().isoformat())
                ),
                position_effective_date=(
                    date.fromisoformat(str(position_effective_date))
                    if position_effective_date
                    else None
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
                derivative_contract_id=(
                    str(raw_transaction.get("derivative_contract_id") or "").strip()
                    or None
                ),
                quantity=float(source_quantity) if source_quantity is not None else None,
                source_quantity=source_quantity,
                price=float(source_price) if source_price is not None else None,
                source_price=source_price,
                gross_amount=float(source_gross_amount),
                source_gross_amount=source_gross_amount,
                counter_amount=(float(source_counter_amount) if source_counter_amount is not None else None),
                source_counter_amount=source_counter_amount,
                fx_rate=float(source_fx_rate) if source_fx_rate is not None else None,
                source_fx_rate=source_fx_rate,
                fees=float(source_fees),
                source_fees=source_fees,
                fee_category=str(raw_transaction.get("fee_category") or "unknown"),
                asset_deliveries_json=deepcopy(raw_transaction.get("asset_deliveries") or []),
                settlement_cashflows_json=deepcopy(raw_transaction.get("settlement_cashflows") or []),
                lot_selections_json=deepcopy(raw_transaction.get("lot_selections") or []),
                taxes=float(source_taxes),
                source_taxes=source_taxes,
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
                source_system=(
                    str(raw_transaction.get("source_system")).strip()
                    if raw_transaction.get("source_system")
                    else None
                ),
                external_reference=(
                    str(raw_transaction.get("external_reference")).strip()
                    if raw_transaction.get("external_reference")
                    else None
                ),
                note=str(raw_transaction.get("note")).strip() if raw_transaction.get("note") else None,
                created_at=(
                    str(raw_transaction.get("created_at")).strip()
                    if raw_transaction.get("created_at")
                    else None
                ),
                row_version=int(raw_transaction.get("row_version") or 1),
            )
        )

    for raw_link in list(normalized.get("option_delivery_links", [])):
        if not isinstance(raw_link, dict):
            continue
        session.add(
            OptionDeliveryLinkModel(
                portfolio_id=str(raw_link.get("portfolio_id") or "").strip(),
                option_transaction_id=str(
                    raw_link.get("option_transaction_id") or ""
                ).strip(),
                stock_transaction_id=str(
                    raw_link.get("stock_transaction_id") or ""
                ).strip(),
                underlying_instrument_id=str(
                    raw_link.get("underlying_instrument_id") or ""
                ).strip(),
                created_at=str(
                    raw_link.get("created_at") or _current_utc_timestamp()
                ).strip(),
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
        "inception_date": item.inception_date.isoformat(),
        "as_of_date": item.as_of_date.isoformat() if item.as_of_date is not None else None,
        "nav": item.nav,
        "day_change_value": item.day_change_value,
        "day_change_pct": item.day_change_pct,
        "securities_count": item.securities_count,
        "sort_order": item.sort_order,
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


def _max_transaction_activity_date(transactions: list[TransactionRecordModel]) -> date | None:
    activity_dates = [
        candidate
        for transaction in transactions
        for candidate in (
            transaction.trade_date,
            transaction.settlement_date,
            transaction.position_effective_date,
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
    valuation_today = portfolio_valuation_today(item.valuation_timezone)
    transaction_rows = [_serialize_transaction_row(transaction) for transaction in transactions]
    portfolio_as_of_date = item.as_of_date
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
    candidate_as_of_date = min(
        max(source_candidate_dates, default=portfolio_as_of_date or valuation_today),
        valuation_today,
    )

    boundary_transactions = [
        transaction
        for transaction in transaction_rows
        if (
            transaction_performance_effective_date(transaction)
            or date.min
        )
        <= candidate_as_of_date
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
        return min(
            max(resolved_candidate_dates, default=candidate_as_of_date),
            valuation_today,
        )

    fallback_candidate_dates = [
        candidate
        for candidate in (
            latest_activity_date,
            latest_transacted_market_date,
            portfolio_as_of_date,
        )
        if candidate is not None
    ]
    return min(
        max(fallback_candidate_dates, default=candidate_as_of_date),
        valuation_today,
    )


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
        return {
            **base_payload,
            "nav": 0.0,
            "coverage_state": "complete",
            "valuation_coverage": {
                "account_count": 0,
                "valued_account_count": 0,
                "unvalued_account_count": 0,
                "missing_account_ids": [],
            },
        }

    workspace = build_account_workspace(
        item.portfolio_id,
        [_serialize_account_row(account) for account in accounts],
        [_serialize_transaction_row(transaction) for transaction in transactions],
        selected_account_id=None,
        base_currency=item.base_currency,
        as_of_date=as_of_date,
    )
    account_rollups = workspace.get("accounts", [])
    account_rollups = [account for account in account_rollups if isinstance(account, dict)]
    valued_accounts = [
        account for account in account_rollups if _safe_float(account.get("account_value_base")) is not None
    ]
    missing_account_ids = [
        str((account.get("account") or {}).get("account_id") or "")
        for account in account_rollups
        if _safe_float(account.get("account_value_base")) is None
    ]
    nav = (
        sum(float(account["account_value_base"]) for account in valued_accounts)
        if len(valued_accounts) == len(account_rollups)
        else None
    )
    summary = workspace.get("summary", {})
    return {
        **base_payload,
        "nav": nav,
        "coverage_state": str(summary.get("valuation_coverage_state") or "unavailable"),
        "valuation_coverage": {
            "account_count": len(account_rollups),
            "valued_account_count": len(valued_accounts),
            "unvalued_account_count": len(account_rollups) - len(valued_accounts),
            "missing_account_ids": [account_id for account_id in missing_account_ids if account_id],
        },
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
            TransactionRecordModel.transaction_sequence,
            TransactionRecordModel.settlement_date,
        )
    ).all()
    return _build_live_portfolio_rollup(session, item, accounts=accounts, transactions=transactions)


def _serialize_portfolio_row_with_materialized_summary(
    session,
    item: PortfolioRecordModel,
) -> dict[str, object]:
    payload = _serialize_portfolio_row(item)
    state = session.get(PortfolioCalculationStateModel, item.portfolio_id)
    if state is not None:
        from portfolio_app.services.daily_snapshots import _state_requires_refresh

        if _state_requires_refresh(session, item.portfolio_id):
            # Portfolio navigation stays available while recalculating, but a
            # previous source generation must not masquerade as current NAV.
            payload.update(nav=None, day_change_value=None, day_change_pct=None,
                           day_return_capital=None, coverage_state="unavailable")
            return payload
    if state is not None and state.daily_snapshot_status == "failed":
        payload["valuation_blocked_from"] = state.dirty_from.isoformat() if state.dirty_from else None
        payload["valuation_blocked_reason"] = state.error_message
    latest_snapshot = default_portfolio_snapshot(session, item.portfolio_id)
    if latest_snapshot is None:
        payload["nav"] = None
        payload["day_change_value"] = None
        payload["day_change_pct"] = None
        payload["coverage_state"] = "unavailable"
        return payload

    snapshot = latest_snapshot.snapshot_json if isinstance(latest_snapshot.snapshot_json, dict) else {}
    payload["as_of_date"] = latest_snapshot.as_of_date.isoformat()
    payload["nav"] = _safe_float(snapshot.get("nav"))
    # Day change is cash-flow-neutral investment P&L.  Raw NAV movement would
    # misclassify subscriptions, withdrawals, and inception funding as return.
    payload["day_change_value"] = _safe_float(snapshot.get("delta"))
    payload["day_change_pct"] = _safe_float(snapshot.get("daily_twr"))
    beginning_nav = _safe_float(snapshot.get("beginning_nav"))
    external_cash_in = _safe_float(snapshot.get("external_cash_in"))
    payload["day_return_capital"] = (
        beginning_nav + external_cash_in
        if beginning_nav is not None and external_cash_in is not None
        and payload["day_change_pct"] is not None
        else None
    )
    payload["coverage_state"] = str(
        snapshot.get("valuation_coverage_state") or "unavailable"
    )
    payload["securities_count"] = int(snapshot.get("total_position_count") or 0)
    return payload


def _serialize_account_row(item: AccountRecordModel) -> dict[str, object]:
    return {
        "account_id": item.account_id,
        "portfolio_id": item.portfolio_id,
        "account_name": item.account_name,
        "account_type": item.account_type,
        "account_category": item.account_category,
        "currency": item.currency,
        "institution": item.institution,
        "default_settlement_cash_account_id": item.default_settlement_cash_account_id,
        "cost_basis_method": item.cost_basis_method,
        "cash_purpose": item.cash_purpose,
        "collateral_reference": item.collateral_reference,
        "opened_at": item.opened_at.isoformat() if item.opened_at is not None else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at is not None else None,
        "status": item.status,
    }


def _serialize_derivative_contract_row(
    item: DerivativeContractRecordModel,
) -> dict[str, object]:
    return {
        "row_version": item.row_version,
        "amendments": deepcopy(item.amendments_json or []),
        "derivative_contract_id": item.derivative_contract_id,
        "portfolio_id": item.portfolio_id,
        "account_id": item.account_id,
        "contract_name": item.contract_name,
        "contract_type": item.contract_type,
        "currency": item.currency,
        "external_reference": item.external_reference,
        "terms": deepcopy(item.terms_json),
        "created_at": item.created_at,
    }


def _serialize_option_delivery_link(
    item: OptionDeliveryLinkModel,
) -> dict[str, object]:
    return {
        "portfolio_id": item.portfolio_id,
        "option_transaction_id": item.option_transaction_id,
        "stock_transaction_id": item.stock_transaction_id,
        "underlying_instrument_id": item.underlying_instrument_id,
        "created_at": item.created_at,
    }


def _serialize_transaction_row(item: TransactionRecordModel) -> dict[str, object]:
    contract = item.derivative_contract if item.derivative_contract_id is not None else None
    contract_payload = _serialize_derivative_contract_row(contract) if contract is not None else None
    return {
        "asset_deliveries": deepcopy(item.asset_deliveries_json or []),
        "settlement_cashflows": deepcopy(item.settlement_cashflows_json or []),
        "lot_selections": deepcopy(item.lot_selections_json or []),
        "transaction_id": item.transaction_id,
        "transaction_sequence": item.transaction_sequence,
        "portfolio_id": item.portfolio_id,
        "transaction_type": item.transaction_type,
        "option_action": resolve_option_action(
            item.transaction_type,
            derivative_contract=contract_payload,
        ),
        "lifecycle_event_type": item.lifecycle_event_type,
        "trade_date": item.trade_date.isoformat(),
        "trade_time": item.trade_time,
        "trade_at": item.trade_at,
        "trade_timezone": item.trade_timezone,
        "trade_time_is_estimated": item.trade_time_is_estimated,
        "settlement_date": item.settlement_date.isoformat(),
        "position_effective_date": (
            item.position_effective_date.isoformat()
            if item.position_effective_date is not None
            else None
        ),
        "entitlement_date": item.entitlement_date.isoformat() if item.entitlement_date is not None else None,
        "acquisition_date": item.acquisition_date.isoformat() if item.acquisition_date is not None else None,
        "account_id": item.account_id,
        "settlement_cash_account_id": item.settlement_cash_account_id,
        "instrument_id": item.instrument_id,
        "instrument_ref": deepcopy(item.instrument_ref_json),
        "derivative_contract_id": item.derivative_contract_id,
        "derivative_contract": contract_payload,
        "quantity": item.quantity,
        "source_quantity": _decimal_text(item.source_quantity),
        "price": item.price,
        "source_price": _decimal_text(item.source_price),
        "gross_amount": item.gross_amount,
        "source_gross_amount": _decimal_text(item.source_gross_amount),
        "counter_amount": item.counter_amount,
        "source_counter_amount": _decimal_text(item.source_counter_amount),
        "fx_rate": item.fx_rate,
        "source_fx_rate": _decimal_text(item.source_fx_rate),
        "fees": item.fees,
        "source_fees": _decimal_text(item.source_fees),
        "fee_category": item.fee_category,
        "taxes": item.taxes,
        "source_taxes": _decimal_text(item.source_taxes),
        "currency": item.currency,
        "transfer_scope": item.transfer_scope,
        "transfer_object_type": item.transfer_object_type,
        "transfer_group_id": item.transfer_group_id,
        "counterparty_account_id": item.counterparty_account_id,
        "source_system": item.source_system,
        "external_reference": item.external_reference,
        "note": item.note,
        "created_at": item.created_at,
        "row_version": item.row_version,
    }


def _serialize_transaction_change_log_row(
    item: TransactionChangeLogModel,
) -> dict[str, object]:
    return {
        "change_id": item.change_id,
        "portfolio_id": item.portfolio_id,
        "transaction_id": item.transaction_id,
        "change_type": item.change_type,
        "row_version": item.row_version,
        "before": deepcopy(item.before_json) if isinstance(item.before_json, dict) else None,
        "after": deepcopy(item.after_json) if isinstance(item.after_json, dict) else None,
        "request_idempotency_key": item.request_idempotency_key,
        "changed_at": item.changed_at,
        "actor_user_id": item.actor_user_id,
        "actor_name": item.actor_name,
    }


def _append_transaction_change_log(
    session,
    *,
    portfolio_id: str,
    transaction_id: str,
    change_type: str,
    row_version: int,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    request_idempotency_key: str | None = None,
) -> None:
    try:
        principal = current_principal()
    except IdentityError:
        principal = None  # Historical imports retain unknown attribution.
    session.add(
        TransactionChangeLogModel(
            change_id=f"txchg-{uuid4().hex}",
            portfolio_id=portfolio_id,
            transaction_id=transaction_id,
            change_type=change_type,
            row_version=row_version,
            before_json=deepcopy(before) if before is not None else None,
            after_json=deepcopy(after) if after is not None else None,
            request_idempotency_key=request_idempotency_key,
            changed_at=_transaction_change_timestamp(),
            actor_user_id=principal.user_id if principal else None,
            actor_name=principal.display_name if principal else None,
        )
    )


def _idempotency_result_in_session(
    session,
    *,
    portfolio_id: str,
    idempotency_key: str,
    operation: str,
    request_hash: str,
) -> list[dict[str, object]] | None:
    idempotency_record = session.get(
        TransactionIdempotencyRecordModel,
        {
            "portfolio_id": portfolio_id,
            "idempotency_key": idempotency_key,
        },
    )
    if idempotency_record is None:
        return None
    if (
        idempotency_record.operation != operation
        or idempotency_record.request_hash != request_hash
    ):
        raise TransactionIdempotencyConflictError(
            "Idempotency key was already used with a different payload or operation."
        )

    transaction_ids = [
        str(transaction_id or "").strip()
        for transaction_id in list(idempotency_record.transaction_ids_json or [])
        if str(transaction_id or "").strip()
    ]
    if not transaction_ids:
        raise TransactionIdempotencyConflictError(
            "The original idempotent transaction result is no longer available."
        )
    records = session.scalars(
        select(TransactionRecordModel).where(
            TransactionRecordModel.portfolio_id == portfolio_id,
            TransactionRecordModel.transaction_id.in_(transaction_ids),
        )
    ).all()
    records_by_id = {record.transaction_id: record for record in records}
    if len(records_by_id) != len(transaction_ids):
        raise TransactionIdempotencyConflictError(
            "The original idempotent transaction result is no longer available."
        )
    return [
        _serialize_transaction_row(records_by_id[transaction_id])
        for transaction_id in transaction_ids
    ]


def _serialize_portfolio_instrument_universe_row(
    item: PortfolioInstrumentUniverseRecordModel,
) -> dict[str, object]:
    payload: dict[str, object] = {
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
        "research_pm_approved": bool(item.research_pm_approved),
        "research_pm_approved_at": item.research_pm_approved_at,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    payload.update(enrich_instrument_research_state(payload))
    return payload


def _transaction_position_quantity_delta(record: TransactionRecordModel) -> float:
    quantity = _safe_float(record.quantity) or 0.0
    if quantity <= 0:
        return 0.0
    transaction_type = str(record.transaction_type or "")
    if transaction_type in {"opening_balance", "buy", "dividend_reinvestment", "buy_to_cover"}:
        return quantity
    if transaction_type in {"sell", "maturity_redemption", "short_sell", "short_opening_balance"}:
        return -quantity
    if transaction_type == "transfer_in" and record.transfer_object_type == "position":
        return quantity
    if transaction_type == "transfer_out" and record.transfer_object_type == "position":
        return -quantity
    return 0.0


def _transaction_record_order_key(
    record: TransactionRecordModel,
) -> tuple[str, str, str, str, str]:
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

    transaction_statement = select(TransactionRecordModel).where(
        TransactionRecordModel.instrument_id.is_not(None)
    )
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
            TransactionRecordModel.transaction_sequence,
        )
    ).all()
    transactions_by_key: dict[tuple[str, str], list[TransactionRecordModel]] = {}
    for transaction in transaction_rows:
        instrument_id = str(transaction.instrument_id or "").strip()
        if not instrument_id:
            continue
        transactions_by_key.setdefault((transaction.portfolio_id, instrument_id), []).append(transaction)

    delivery_statement = select(TransactionRecordModel).where(TransactionRecordModel.derivative_contract_id.is_not(None))
    if normalized_portfolio_id:
        delivery_statement = delivery_statement.where(TransactionRecordModel.portfolio_id == normalized_portfolio_id)
    for transaction in session.scalars(delivery_statement):
        for leg in transaction.asset_deliveries_json or []:
            instrument_id = str(leg["instrument_id"])
            if normalized_instrument_ids and instrument_id not in normalized_instrument_ids:
                continue
            delivered = SimpleNamespace(
                portfolio_id=transaction.portfolio_id, instrument_id=instrument_id,
                instrument_ref_json=leg["instrument_ref"], transaction_type="buy",
                quantity=float(leg["quantity"]), transfer_object_type=None,
                trade_date=transaction.trade_date, trade_at=transaction.trade_at,
                created_at=transaction.created_at, transaction_sequence=transaction.transaction_sequence,
                transaction_id=transaction.transaction_id,
                settlement_date=transaction.settlement_date,
            )
            transactions_by_key.setdefault((transaction.portfolio_id, instrument_id), []).append(delivered)

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
            existing.holding_state = "held" if abs(quantity) > 1e-9 else "not_held"
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
                    research_pm_approved=False,
                    research_pm_approved_at=None,
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
        "root_allocation_basis": item.root_allocation_basis,
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
        "allocation_basis": item.allocation_basis,
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
        "target_value": item.target_value,
        "notes": item.notes,
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "portfolio"


TRANSACTION_ID_ALLOCATOR_KEY = "transaction"


def _next_transaction_number_from_history(session) -> int:
    maximum_sequence = session.scalar(
        select(func.max(TransactionRecordModel.transaction_sequence))
    )
    return int(maximum_sequence or 0) + 1


def _ensure_transaction_id_allocator(session, *, minimum_next_value: int = 1) -> None:
    values = {
        "allocator_key": TRANSACTION_ID_ALLOCATOR_KEY,
        "next_value": max(int(minimum_next_value), 1),
    }
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = postgresql_insert(TransactionIdAllocatorModel).values(**values)
        session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[TransactionIdAllocatorModel.allocator_key],
            )
        )
    elif dialect_name == "sqlite":
        statement = sqlite_insert(TransactionIdAllocatorModel).values(**values)
        session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[TransactionIdAllocatorModel.allocator_key],
            )
        )
    elif session.get(TransactionIdAllocatorModel, TRANSACTION_ID_ALLOCATOR_KEY) is None:
        session.add(TransactionIdAllocatorModel(**values))
        session.flush()

    session.execute(
        update(TransactionIdAllocatorModel)
        .where(TransactionIdAllocatorModel.allocator_key == TRANSACTION_ID_ALLOCATOR_KEY)
        .where(TransactionIdAllocatorModel.next_value < values["next_value"])
        .values(next_value=values["next_value"])
    )


def _allocate_transaction_identities(session, count: int) -> list[tuple[str, int]]:
    if count <= 0:
        return []
    _ensure_transaction_id_allocator(
        session,
        minimum_next_value=_next_transaction_number_from_history(session),
    )
    next_value = session.scalar(
        update(TransactionIdAllocatorModel)
        .where(TransactionIdAllocatorModel.allocator_key == TRANSACTION_ID_ALLOCATOR_KEY)
        .values(next_value=TransactionIdAllocatorModel.next_value + count)
        .returning(TransactionIdAllocatorModel.next_value)
    )
    if next_value is None:  # pragma: no cover - allocator corruption guard
        raise RuntimeError("Transaction id allocator is unavailable.")
    first_value = int(next_value) - count
    return [
        (f"txn-{number:04d}", number)
        for number in range(first_value, int(next_value))
    ]


def _lock_portfolio_for_transaction_mutation(session, portfolio_id: str) -> bool:
    """Serialize transaction mutations per portfolio on PostgreSQL and SQLite."""

    if session.get_bind().dialect.name == "sqlite":
        result = session.execute(
            update(PortfolioRecordModel)
            .where(PortfolioRecordModel.portfolio_id == portfolio_id)
            .values(portfolio_id=PortfolioRecordModel.portfolio_id)
        )
        return int(result.rowcount or 0) > 0

    portfolio_key = session.scalar(
        select(PortfolioRecordModel.portfolio_id)
        .where(PortfolioRecordModel.portfolio_id == portfolio_id)
        .with_for_update()
    )
    return portfolio_key is not None


def load_locked_portfolio_ledger_state(
    session,
    portfolio_id: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]] | None:
    """Lock one portfolio and return a coherent account/transaction snapshot.

    Event-task projection and transaction mutations share this lock, so an
    entitlement calculation cannot race a ledger write for the same portfolio.
    """

    if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
        return None
    accounts = list(
        session.scalars(
            select(AccountRecordModel)
            .where(AccountRecordModel.portfolio_id == portfolio_id)
            .order_by(AccountRecordModel.account_id)
        ).all()
    )
    transactions = list(
        session.scalars(
            select(TransactionRecordModel)
            .where(TransactionRecordModel.portfolio_id == portfolio_id)
            .order_by(
                TransactionRecordModel.trade_date,
                TransactionRecordModel.trade_at,
                TransactionRecordModel.created_at,
                TransactionRecordModel.transaction_sequence,
                TransactionRecordModel.settlement_date,
            )
        ).all()
    )
    return (
        [_serialize_account_row(item) for item in accounts],
        [_serialize_transaction_row(item) for item in transactions],
    )


def _validate_portfolio_transaction_history(session, portfolio_id: str) -> None:
    accounts = list(
        session.scalars(
            select(AccountRecordModel).where(AccountRecordModel.portfolio_id == portfolio_id)
        ).all()
    )
    transactions = session.scalars(
        select(TransactionRecordModel).where(
            TransactionRecordModel.portfolio_id == portfolio_id
        )
    ).all()
    validate_transaction_position_history(
        portfolio_id,
        [_serialize_transaction_row(record) for record in transactions],
        account_cost_methods={
            account.account_id: str(account.cost_basis_method or "fifo")
            for account in accounts
            if account.account_type == "securities_account"
        },
    )


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




def _is_reserved_cash_label(node_name: str | None, node_code: str | None) -> bool:
    normalized_name = (node_name or "").strip().lower()
    normalized_code = (node_code or "").strip().lower()
    return normalized_code == "cash" or normalized_name in {"cash", "现金"}


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
    taxonomy: TaxonomyRecordModel,
    comparator_taxonomy_node_id: str | None,
) -> tuple[TaxonomyNodeRecordModel | None, list[dict[str, object]]]:
    parent_node, child_nodes = _scope_child_nodes(
        session,
        taxonomy_id=taxonomy.taxonomy_id,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
    )
    members: list[dict[str, object]] = []
    if child_nodes:
        members.extend(
            {
                "target_member_type": TARGET_MEMBER_NODE,
                "target_member_id": node.taxonomy_node_id,
                "taxonomy_node_id": node.taxonomy_node_id,
                "label": node.node_name,
            }
            for node in child_nodes
        )

    if members:
        return parent_node, members

    if comparator_taxonomy_node_id is None:
        raise ValueError("Comparator scope must have active child sleeves.")

    direct_assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy.taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id == comparator_taxonomy_node_id,
            TaxonomyAssignmentRecordModel.status == "active",
        )
    ).all()
    explicit_members = session.scalars(select(TargetSetLineRecordModel.target_member_id)
        .join(TargetSetRecordModel, TargetSetRecordModel.target_set_id == TargetSetLineRecordModel.target_set_id)
        .where(TargetSetRecordModel.taxonomy_id == taxonomy.taxonomy_id,
               TargetSetRecordModel.status == "active", TargetSetLineRecordModel.target_member_type == "instrument")).all()
    from portfolio_app.services.valuation_clock import portfolio_valuation_today
    portfolio = session.get(PortfolioRecordModel, taxonomy.portfolio_id)
    contract_only = contract_only_instrument_ids(taxonomy.portfolio_id,
        as_of_date=portfolio_valuation_today(portfolio.valuation_timezone), explicitly_selected=explicit_members, session=session)
    visible_direct_assignments: dict[tuple[str, str], TaxonomyAssignmentRecordModel] = {}
    for assignment in direct_assignments:
        member_key = (str(assignment.target_scope), str(assignment.target_entity_id))
        if member_key[0] == "instrument" and member_key[1] in contract_only:
            continue
        visible_direct_assignments.setdefault(member_key, assignment)
    if not visible_direct_assignments:
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
    session, *, taxonomy: TaxonomyRecordModel,
    comparator_taxonomy_node_id: str | None, target_set_type: str,
    status: str, lines: list[dict[str, object]],
    exclude_target_set_id: str | None = None,
) -> tuple[TaxonomyNodeRecordModel | None, list[dict[str, object]]]:
    parent, members = _scope_target_members(session, taxonomy=taxonomy,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id)
    basis = parent.allocation_basis if parent else taxonomy.root_allocation_basis
    if basis not in {"weight", "risk_budget"}:
        raise ValueError("Unsupported allocation basis.")
    expected = {(str(m["target_member_type"]), str(m["target_member_id"])) for m in members}
    cash_key = (TARGET_MEMBER_CASH, SYSTEM_CASH_TARGET_MEMBER_ID)
    allowed = expected | ({cash_key} if parent is None else set())
    seen: set[tuple[str, str]] = set()
    total = 0.0
    cash_reserve = 0.0
    for line in lines:
        member_type, member_id, _ = _normalize_target_line_member(line)
        key = (member_type, member_id)
        if key not in allowed:
            raise ValueError("Target set lines must match the direct members of the selected scope; derivative capital is not a target.")
        if key in seen:
            raise ValueError("Duplicate scope member in target set lines.")
        seen.add(key)
        value = line.get("target_value")
        if value is None or not isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError("Every scope member needs a finite target_value between 0% and 100%.")
        if key == cash_key:
            cash_reserve = float(value)
            if cash_reserve > 1:
                raise ValueError("Cash reserve must be between 0% and 100% of portfolio NAV.")
        else:
            total += float(value)
    if not expected.issubset(seen):
        raise ValueError("Target set lines must cover every direct member in the selected scope.")
    all_cash = parent is None and basis == "weight" and cash_reserve == 1 and abs(total) <= 1e-12
    if abs(total - 1) > TARGET_SET_EPSILON and not all_cash:
        raise ValueError("Security member target_value values must sum to 100%; cash reserve is separate.")
    if status == "active":
        other = session.scalars(select(TargetSetRecordModel).where(
            TargetSetRecordModel.taxonomy_id == taxonomy.taxonomy_id,
            TargetSetRecordModel.target_set_type == target_set_type,
            TargetSetRecordModel.status == "active",
            TargetSetRecordModel.comparator_taxonomy_node_id == comparator_taxonomy_node_id)).all()
        if any(item.target_set_id != exclude_target_set_id for item in other):
            raise ValueError(f"An active {target_set_type.upper()} target set already exists for this scope.")
    return parent, members


def _remove_scope_target_member(session, *, taxonomy_id: str, scope_node_id: str | None,
        member_type: str, member_id: str) -> None:
    """Remove departed members without inventing budgets for the survivors."""
    has_children = session.scalar(select(TaxonomyNodeRecordModel.taxonomy_node_id).where(
        TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
        TaxonomyNodeRecordModel.parent_taxonomy_node_id == scope_node_id,
        TaxonomyNodeRecordModel.status == "active").limit(1))
    has_assignments = None if scope_node_id is None else session.scalar(
        select(TaxonomyAssignmentRecordModel.assignment_id).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id == scope_node_id,
            TaxonomyAssignmentRecordModel.status == "active").limit(1))
    for target_set in session.scalars(select(TargetSetRecordModel).options(
            selectinload(TargetSetRecordModel.lines)).where(
            TargetSetRecordModel.taxonomy_id == taxonomy_id,
            TargetSetRecordModel.comparator_taxonomy_node_id == scope_node_id)):
        if has_children is None and has_assignments is None:
            session.delete(target_set)
        else:
            for line in list(target_set.lines):
                if line.target_member_type == member_type and line.target_member_id == member_id:
                    target_set.lines.remove(line)
    session.flush()


def list_portfolios(*, portfolio_ids: list[str] | None = None) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolios = session.scalars(
            select(PortfolioRecordModel).where(
                PortfolioRecordModel.portfolio_id.in_(portfolio_ids) if portfolio_ids is not None else True
            ).order_by(
                PortfolioRecordModel.sort_order,
                PortfolioRecordModel.portfolio_name,
                PortfolioRecordModel.portfolio_id,
            )
        ).all()
        return [_serialize_portfolio_row_with_materialized_summary(session, item) for item in portfolios]


def get_portfolio_configuration(portfolio_id: str) -> dict[str, object] | None:
    """Read command metadata without loading or validating derived valuations."""
    with get_session_factory()() as session:
        record = session.get(PortfolioRecordModel, portfolio_id)
        if record is None:
            return None
        return {
            "portfolio_id": record.portfolio_id,
            "portfolio_name": record.portfolio_name,
            "base_currency": record.base_currency,
            "valuation_timezone": record.valuation_timezone,
            "valuation_cutoff_policy": record.valuation_cutoff_policy,
            "inception_date": record.inception_date.isoformat(),
        }


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
        materialized = _serialize_portfolio_row_with_materialized_summary(session, record)
        if materialized.get("valuation_blocked_from"):
            return materialized
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


@contextmanager
def _taxonomy_write_session(portfolio_id: str):
    """Serialize configuration edits before reading facts or capturing a revision."""
    with get_session_factory()() as session:
        session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id,
        ).with_for_update())
        yield session


def create_taxonomy(
    portfolio_id: str,
    *,
    name: str,
    taxonomy_type: str,
    purpose: str | None,
    primary_assignment_scope: str,
    root_allocation_basis: str,
    status: str,
    source_template_ref: str | None,
) -> dict[str, object]:
    with _taxonomy_write_session(portfolio_id) as session:
        record = TaxonomyRecordModel(
            # Saved policy and Research references outlive deletion; a reused
            # name must never bind those references to a new classification.
            taxonomy_id=f"tax-{uuid4().hex}",
            portfolio_id=portfolio_id,
            name=name.strip(),
            taxonomy_type=(taxonomy_type or "custom").strip() or "custom",
            purpose=(purpose or "").strip() or None,
            primary_assignment_scope=primary_assignment_scope,
            root_allocation_basis=(root_allocation_basis or "weight").strip() or "weight",
            status=(status or "active").strip() or "active",
            source_template_ref=(source_template_ref or "").strip() or None,
        )
        session.add(record)
        session.flush()
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=record.taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return _serialize_taxonomy_row(record)


def update_taxonomy(
    portfolio_id: str,
    taxonomy_id: str,
    *,
    _session: Session | None = None,
    name: str | None = UNSET,
    taxonomy_type: str | None = UNSET,
    purpose: str | None = UNSET,
    root_allocation_basis: str | None = UNSET,
    status: str | None = UNSET,
) -> dict[str, object]:
    with nullcontext(_session) if _session is not None else _taxonomy_write_session(portfolio_id) as session:
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

        if name is not UNSET and name is not None:
            record.name = name.strip()
        if taxonomy_type is not UNSET and taxonomy_type is not None:
            record.taxonomy_type = taxonomy_type.strip() or "custom"
        if purpose is not UNSET:
            record.purpose = (purpose or "").strip() or None
        if root_allocation_basis is not UNSET and root_allocation_basis is not None:
            record.root_allocation_basis = root_allocation_basis.strip() or "weight"
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        if record.status != "active":
            research_settings = session.get(ResearchSettingsRecordModel, portfolio_id)
            if research_settings is not None and research_settings.planning_taxonomy_id == taxonomy_id:
                research_settings.planning_taxonomy_id = None
                research_settings.comparator_taxonomy_node_id = None

        session.flush()
        if _session is None:
            _record_taxonomy_configuration_revision_in_session(
                session,
                portfolio_id=portfolio_id,
                taxonomy_id=taxonomy_id,
            )
            _mark_daily_snapshots_stale(
                portfolio_id,
                dirty_from=None,
                session=session,
            )
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
    allocation_basis: str,
    status: str,
) -> dict[str, object]:
    with _taxonomy_write_session(portfolio_id) as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")
        if _is_reserved_cash_label(node_name, node_code):
            raise ValueError("Cash is system-managed for instrument taxonomies.")

        parent_node = None
        if parent_taxonomy_node_id:
            parent_node = _ensure_node_can_accept_children(session, taxonomy_id, parent_taxonomy_node_id)
            parent_node.is_terminal = False

        max_sort_order = session.scalar(
            select(func.max(TaxonomyNodeRecordModel.sort_order)).where(TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)
        )
        resolved_sort_order = int(sort_order) if sort_order is not None else int(max_sort_order or -1) + 1
        record = TaxonomyNodeRecordModel(
            taxonomy_node_id=f"tax-node-{uuid4().hex}",
            taxonomy_id=taxonomy_id,
            parent_taxonomy_node_id=parent_taxonomy_node_id,
            node_name=node_name.strip(),
            node_code=(node_code or "").strip() or None,
            sort_order=resolved_sort_order,
            is_terminal=is_terminal,
            allocation_basis=(allocation_basis or "weight").strip() or "weight",
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.flush()
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return _serialize_taxonomy_node_row(record)


def update_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    *,
    _session: Session | None = None,
    node_name: str | None = UNSET,
    node_code: str | None = UNSET,
    parent_taxonomy_node_id: str | None = UNSET,
    sort_order: int | None = UNSET,
    allocation_basis: str | None = UNSET,
    status: str | None = UNSET,
) -> dict[str, object]:
    with nullcontext(_session) if _session is not None else _taxonomy_write_session(portfolio_id) as session:
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
        resolved_node_name = record.node_name if node_name is UNSET or node_name is None else node_name
        resolved_node_code = record.node_code if node_code is UNSET else node_code
        if _is_reserved_cash_label(
            resolved_node_name,
            resolved_node_code,
        ):
            raise ValueError("Cash is system-managed for instrument taxonomies.")

        if node_name is not UNSET and node_name is not None:
            record.node_name = node_name.strip()
        if node_code is not UNSET:
            record.node_code = (node_code or "").strip() or None
        if sort_order is not UNSET and sort_order is not None:
            record.sort_order = int(sort_order)
        if allocation_basis is not UNSET and allocation_basis is not None:
            record.allocation_basis = allocation_basis.strip() or "weight"
        if status is not UNSET and status is not None:
            record.status = status.strip() or "active"

        if parent_taxonomy_node_id is not UNSET:
            resolved_parent_id = (parent_taxonomy_node_id or "").strip() or None
            old_parent_id = record.parent_taxonomy_node_id
            if resolved_parent_id == taxonomy_node_id:
                raise ValueError("A taxonomy node cannot become its own parent.")

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
            if resolved_parent_id != old_parent_id:
                _remove_scope_target_member(session, taxonomy_id=taxonomy_id, scope_node_id=old_parent_id,
                    member_type=TARGET_MEMBER_NODE, member_id=taxonomy_node_id)
            _refresh_parent_terminal_state(session, old_parent_id)
            _refresh_parent_terminal_state(session, resolved_parent_id)

        session.flush()
        if _session is None:
            _record_taxonomy_configuration_revision_in_session(
                session,
                portfolio_id=portfolio_id,
                taxonomy_id=taxonomy_id,
            )
            _mark_daily_snapshots_stale(
                portfolio_id,
                dirty_from=None,
                session=session,
            )
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


def list_portfolio_instrument_universe(portfolio_id: str, *, _session: Session | None = None) -> list[dict[str, object]]:
    with nullcontext(_session) if _session is not None else get_session_factory()() as session:
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

    with _taxonomy_write_session(normalized_portfolio_id) as session:
        if session.get(PortfolioRecordModel, normalized_portfolio_id) is None:
            return None

        now = _current_utc_timestamp()
        record = session.scalar(
            select(PortfolioInstrumentUniverseRecordModel).where(
                PortfolioInstrumentUniverseRecordModel.portfolio_id
                == normalized_portfolio_id,
                PortfolioInstrumentUniverseRecordModel.instrument_id
                == normalized_instrument_id,
            )
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
                research_pm_approved=False,
                research_pm_approved_at=None,
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


def set_portfolio_instrument_research_pm_approval(
    portfolio_id: str,
    instrument_id: str,
    *,
    pm_approved: bool,
) -> dict[str, object] | None:
    normalized_portfolio_id = str(portfolio_id or "").strip()
    normalized_instrument_id = str(instrument_id or "").strip()
    if not normalized_portfolio_id or not normalized_instrument_id:
        raise ValueError("portfolio_id and instrument_id are required.")

    with _taxonomy_write_session(normalized_portfolio_id) as session:
        record = session.scalar(
            select(PortfolioInstrumentUniverseRecordModel).where(
                PortfolioInstrumentUniverseRecordModel.portfolio_id
                == normalized_portfolio_id,
                PortfolioInstrumentUniverseRecordModel.instrument_id
                == normalized_instrument_id,
            )
        )
        if record is None or record.status != "active":
            return None
        lifecycle = derive_research_lifecycle(
            holding_state=record.holding_state,
            transaction_count=record.transaction_count,
        )
        if lifecycle != "former":
            raise ValueError("PM research eligibility approval is only available for Former instruments.")

        now = _current_utc_timestamp()
        record.research_pm_approved = bool(pm_approved)
        record.research_pm_approved_at = now if pm_approved else None
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

    with _taxonomy_write_session(normalized_portfolio_id) as session:
        record = session.scalar(
            select(PortfolioInstrumentUniverseRecordModel).where(
                PortfolioInstrumentUniverseRecordModel.portfolio_id
                == normalized_portfolio_id,
                PortfolioInstrumentUniverseRecordModel.instrument_id
                == normalized_instrument_id,
            )
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


def list_target_set_integrity_issues(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
    _session: Session | None = None,
) -> list[dict[str, object]]:
    """Report active planning targets that no longer match their live scope.

    Taxonomy membership and target budgets intentionally have separate write
    paths: assigning a new instrument must not invent a risk budget, while
    rejecting the assignment would leave no way to add the corresponding
    target line. This derived validation keeps that workflow explicit. The
    research solver remains fail-closed, and callers can direct the user back
    to Edit Targets before attempting a run.
    """

    with nullcontext(_session) if _session is not None else get_session_factory()() as session:
        taxonomy_statement = select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id,
        )
        if taxonomy_id:
            taxonomy_statement = taxonomy_statement.where(TaxonomyRecordModel.taxonomy_id == taxonomy_id)
        taxonomies = session.scalars(taxonomy_statement).all()
        taxonomies_by_id = {item.taxonomy_id: item for item in taxonomies}
        if not taxonomies_by_id:
            return []

        target_sets = session.scalars(
            select(TargetSetRecordModel)
            .where(
                TargetSetRecordModel.taxonomy_id.in_(list(taxonomies_by_id)),
                TargetSetRecordModel.status == "active",
            )
            .order_by(
                TargetSetRecordModel.taxonomy_id,
                TargetSetRecordModel.comparator_taxonomy_node_id,
                TargetSetRecordModel.target_set_type,
                TargetSetRecordModel.target_set_id,
            )
        ).all()
        if not target_sets:
            return []

        target_set_ids = [item.target_set_id for item in target_sets]
        target_lines = session.scalars(
            select(TargetSetLineRecordModel)
            .where(TargetSetLineRecordModel.target_set_id.in_(target_set_ids))
            .order_by(
                TargetSetLineRecordModel.target_set_id,
                TargetSetLineRecordModel.target_member_type,
                TargetSetLineRecordModel.target_member_id,
                TargetSetLineRecordModel.target_line_id,
            )
        ).all()
        target_lines_by_set_id: dict[str, list[TargetSetLineRecordModel]] = {}
        for line in target_lines:
            target_lines_by_set_id.setdefault(line.target_set_id, []).append(line)

        scope_node_ids = {
            item.comparator_taxonomy_node_id
            for item in target_sets
            if item.comparator_taxonomy_node_id
        }
        scope_nodes_by_id = {
            item.taxonomy_node_id: item
            for item in session.scalars(
                select(TaxonomyNodeRecordModel).where(
                    TaxonomyNodeRecordModel.taxonomy_node_id.in_(list(scope_node_ids))
                )
            ).all()
        }

        issues: list[dict[str, object]] = []
        for target_set in target_sets:
            taxonomy = taxonomies_by_id[target_set.taxonomy_id]
            lines = [
                {
                    "target_member_type": line.target_member_type,
                    "target_member_id": line.target_member_id,
                    "taxonomy_node_id": line.taxonomy_node_id,
                    "target_value": line.target_value,
                    "notes": line.notes,
                }
                for line in target_lines_by_set_id.get(target_set.target_set_id, [])
            ]
            try:
                _validate_target_set_lines(
                    session,
                    taxonomy=taxonomy,
                    comparator_taxonomy_node_id=target_set.comparator_taxonomy_node_id,
                    target_set_type=target_set.target_set_type,
                    status=target_set.status,
                    lines=lines,
                    exclude_target_set_id=target_set.target_set_id,
                )
            except ValueError as error:
                scope_node = scope_nodes_by_id.get(target_set.comparator_taxonomy_node_id)
                issues.append(
                    {
                        "taxonomy_id": target_set.taxonomy_id,
                        "comparator_taxonomy_node_id": target_set.comparator_taxonomy_node_id,
                        "scope_label": scope_node.node_name if scope_node is not None else "Top Level",
                        "target_set_id": target_set.target_set_id,
                        "target_set_type": target_set.target_set_type,
                        "target_set_name": target_set.name,
                        "issue_code": "invalid_active_target_set",
                        "message": str(error),
                    }
                )

        return issues


def create_taxonomy_assignment(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    target_scope: str,
    target_entity_id: str,
    taxonomy_node_id: str,
    status: str,
) -> dict[str, object]:
    with _taxonomy_write_session(portfolio_id) as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")
        if target_scope != "instrument":
            raise ValueError("Taxonomy assignments support Registry instruments only.")

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
            assignment_id=f"tax-assignment-{uuid4().hex}",
            taxonomy_id=taxonomy_id,
            target_scope=target_scope,
            target_entity_id=target_entity_id.strip(),
            taxonomy_node_id=taxonomy_node_id,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        session.flush()
        _refresh_portfolio_instrument_universe_records(
            session,
            portfolio_id,
            {target_entity_id.strip()},
        )
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
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
    with _taxonomy_write_session(portfolio_id) as session:
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
        previous_node_id = record.taxonomy_node_id
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
        if record.taxonomy_node_id != previous_node_id or record.status != "active":
            _remove_scope_target_member(session, taxonomy_id=taxonomy_id, scope_node_id=previous_node_id,
                member_type="instrument", member_id=record.target_entity_id)
        _refresh_portfolio_instrument_universe_records(
            session,
            portfolio_id,
            {record.target_entity_id},
        )
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return _serialize_taxonomy_assignment_row(record)


def create_target_set(
    portfolio_id: str,
    *,
    _session: Session | None = None,
    taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    target_set_type: str,
    name: str,
    status: str,
    notes: str | None,
    lines: list[dict[str, object]],
) -> dict[str, object]:
    with nullcontext(_session) if _session is not None else _taxonomy_write_session(portfolio_id) as session:
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
            status=(status or "active").strip() or "active",
            lines=lines,
        )

        record = TargetSetRecordModel(
            target_set_id=_next_target_set_id(session, taxonomy_id, target_set_type, name),
            taxonomy_id=taxonomy_id,
            comparator_taxonomy_node_id=comparator_taxonomy_node_id,
            target_set_type=target_set_type,
            name=name.strip(),
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
                    target_value=(
                        float(raw_line["target_value"])
                        if raw_line.get("target_value") is not None
                        else None
                    ),
                    notes=(str(raw_line.get("notes")).strip() if raw_line.get("notes") else None),
                )
            )

        session.flush()
        if _session is None:
            _record_taxonomy_configuration_revision_in_session(
                session,
                portfolio_id=portfolio_id,
                taxonomy_id=taxonomy_id,
            )
            _mark_daily_snapshots_stale(
                portfolio_id,
                dirty_from=None,
                session=session,
            )
            session.commit()
        return _serialize_target_set_row(record)


def update_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    target_set_id: str,
    *,
    _session: Session | None = None,
    name: str | None = UNSET,
    status: str | None = UNSET,
    notes: str | None = UNSET,
    lines: list[dict[str, object]] | None = UNSET,
) -> dict[str, object]:
    with nullcontext(_session) if _session is not None else _taxonomy_write_session(portfolio_id) as session:
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
        resolved_lines = (
            [
                {
                    "target_member_type": item.target_member_type,
                    "target_member_id": item.target_member_id,
                    "taxonomy_node_id": item.taxonomy_node_id,
                    "target_value": item.target_value,
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
            status=(record.status if status is UNSET else ((status or "active").strip() or "active")),
            lines=resolved_lines,
            exclude_target_set_id=target_set_id,
        )

        if name is not UNSET and name is not None:
            record.name = name.strip()
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
                        target_value=(
                            float(raw_line["target_value"])
                            if raw_line.get("target_value") is not None
                            else None
                        ),
                        notes=(str(raw_line.get("notes")).strip() if raw_line.get("notes") else None),
                    )
                )

        session.flush()
        if _session is None:
            _record_taxonomy_configuration_revision_in_session(
                session,
                portfolio_id=portfolio_id,
                taxonomy_id=taxonomy_id,
            )
            _mark_daily_snapshots_stale(
                portfolio_id,
                dirty_from=None,
                session=session,
            )
            session.commit()
        return _serialize_target_set_row(record)


def save_taxonomy_target_configuration(
    portfolio_id: str, taxonomy_id: str, *,
    expected_configuration_version: int,
    node_allocation_bases: dict[str, str], target_sets: list[dict[str, object]],
    root_allocation_basis: str | None = None, concentration=None,
) -> dict[str, object]:
    """One editor action commits together; limit-only saves do not change targets."""
    with _taxonomy_write_session(portfolio_id) as session:
        taxonomy = session.get(TaxonomyRecordModel, taxonomy_id)
        if taxonomy is None or taxonomy.portfolio_id != portfolio_id:
            raise ValueError("Taxonomy not found.")
        state = session.get(PortfolioTaxonomyStateModel, portfolio_id)
        if expected_configuration_version != (state.current_version if state else 0):
            from fastapi import HTTPException
            raise HTTPException(409, "Taxonomy configuration changed. Reload before saving.")
        changed_basis_scopes: set[str | None] = set()
        if root_allocation_basis is not None and root_allocation_basis != taxonomy.root_allocation_basis:
            if root_allocation_basis not in {"weight", "risk_budget"}:
                raise ValueError("Unsupported root allocation basis.")
            taxonomy.root_allocation_basis = root_allocation_basis
            changed_basis_scopes.add(None)
        for node_id, basis in node_allocation_bases.items():
            node = session.get(TaxonomyNodeRecordModel, node_id)
            if node is None or node.taxonomy_id != taxonomy_id:
                raise ValueError("Taxonomy node not found.")
            if basis not in {"weight", "risk_budget"}:
                raise ValueError("Unsupported allocation basis.")
            if node.allocation_basis != basis:
                node.allocation_basis = basis
                changed_basis_scopes.add(node_id)
        session.flush()
        for item in target_sets:
            scope = item.get("comparator_taxonomy_node_id")
            stage = str(item["target_set_type"])
            target_set_id = item.get("target_set_id")
            existing = session.get(TargetSetRecordModel, str(target_set_id)) if target_set_id else session.scalar(
                select(TargetSetRecordModel).where(TargetSetRecordModel.taxonomy_id == taxonomy_id,
                    TargetSetRecordModel.comparator_taxonomy_node_id == scope,
                    TargetSetRecordModel.target_set_type == stage, TargetSetRecordModel.status == "active"))
            if target_set_id and (existing is None or existing.taxonomy_id != taxonomy_id):
                raise ValueError("Target set not found.")
            if existing is not None and (existing.comparator_taxonomy_node_id != scope or existing.target_set_type != stage):
                raise ValueError("Target set scope changed; reload the taxonomy before saving.")
            if not item.get("lines"):
                if existing is not None:
                    session.delete(existing)
                    session.flush()
                continue
            values = {key: item.get(key) for key in ("name", "status", "notes", "lines")}
            values["name"] = values["name"] or stage.upper()
            values["status"] = values["status"] or "active"
            if existing is not None:
                update_target_set(portfolio_id, taxonomy_id, existing.target_set_id, _session=session, **values)
            else:
                create_target_set(portfolio_id, taxonomy_id=taxonomy_id,
                    comparator_taxonomy_node_id=scope, target_set_type=stage, _session=session, **values)
        session.flush()
        # A changed basis cannot leave the other stage valid under an obsolete meaning.
        for target_set in session.scalars(select(TargetSetRecordModel).where(
                TargetSetRecordModel.taxonomy_id == taxonomy_id, TargetSetRecordModel.status == "active")):
            if target_set.comparator_taxonomy_node_id in changed_basis_scopes:
                _validate_target_set_lines(session, taxonomy=taxonomy,
                    comparator_taxonomy_node_id=target_set.comparator_taxonomy_node_id,
                    target_set_type=target_set.target_set_type, status=target_set.status,
                    lines=[_serialize_target_set_line_row(line) for line in target_set.lines],
                    exclude_target_set_id=target_set.target_set_id)
        if concentration is not None:
            from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
            from portfolio_app.services.concentration_settings import save_concentration_settings_in_session
            save_concentration_settings_in_session(session, portfolio_id,
                concentration if isinstance(concentration, ConcentrationSettingsUpdate)
                else ConcentrationSettingsUpdate.model_validate(concentration))
        if changed_basis_scopes or target_sets:
            _record_taxonomy_configuration_revision_in_session(session,
                portfolio_id=portfolio_id, taxonomy_id=taxonomy_id)
            _mark_daily_snapshots_stale(portfolio_id, dirty_from=None, session=session)
        session.commit()
        return _serialize_taxonomy_row(taxonomy)


def delete_target_set(
    portfolio_id: str,
    taxonomy_id: str,
    target_set_id: str,
) -> bool:
    with _taxonomy_write_session(portfolio_id) as session:
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
        session.flush()
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return True


def delete_taxonomy(
    portfolio_id: str,
    taxonomy_id: str,
) -> bool:
    with _taxonomy_write_session(portfolio_id) as session:
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
        record.status = "deleted"
        session.flush()
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        session.delete(record)
        session.flush()
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return True


def delete_taxonomy_node(
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
) -> bool:
    with _taxonomy_write_session(portfolio_id) as session:
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

        parent_taxonomy_node_id = record.parent_taxonomy_node_id
        nodes = session.scalars(select(TaxonomyNodeRecordModel).where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)).all()
        children_by_parent: dict[str, list[str]] = {}
        for node in nodes:
            if node.parent_taxonomy_node_id:
                children_by_parent.setdefault(node.parent_taxonomy_node_id, []).append(node.taxonomy_node_id)
        deleted_node_ids: set[str] = set()
        pending_node_ids = [taxonomy_node_id]
        while pending_node_ids:
            node_id = pending_node_ids.pop()
            if node_id in deleted_node_ids:
                continue
            deleted_node_ids.add(node_id)
            pending_node_ids.extend(children_by_parent.get(node_id, []))

        assignments = session.scalars(select(TaxonomyAssignmentRecordModel).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.taxonomy_node_id.in_(deleted_node_ids))).all()
        affected_instrument_ids = {item.target_entity_id for item in assignments}
        for assignment in assignments:
            session.delete(assignment)

        target_sets = session.scalars(select(TargetSetRecordModel).options(
            selectinload(TargetSetRecordModel.lines)).where(
            TargetSetRecordModel.taxonomy_id == taxonomy_id)).all()
        for target_set in target_sets:
            if target_set.comparator_taxonomy_node_id in deleted_node_ids:
                session.delete(target_set)
                continue
            # Surviving scopes keep their other members' exact budgets. The
            # existing integrity checks expose any resulting shortfall.
            for line in list(target_set.lines):
                if line.taxonomy_node_id in deleted_node_ids or (
                    line.target_member_type == "taxonomy_node" and line.target_member_id in deleted_node_ids
                ):
                    target_set.lines.remove(line)

        research_settings = session.get(ResearchSettingsRecordModel, portfolio_id)
        if research_settings is not None and research_settings.planning_taxonomy_id == taxonomy_id:
            if research_settings.comparator_taxonomy_node_id in deleted_node_ids:
                research_settings.comparator_taxonomy_node_id = None
            research_settings.frozen_taxonomy_node_ids_json = [
                node_id for node_id in research_settings.frozen_taxonomy_node_ids_json or []
                if node_id not in deleted_node_ids
            ]
            research_settings.top_sleeve_weight_bounds_json = [
                bound for bound in research_settings.top_sleeve_weight_bounds_json or []
                if bound.get("taxonomy_node_id") not in deleted_node_ids
            ]
        for node in nodes:
            if node.taxonomy_node_id in deleted_node_ids:
                session.delete(node)
        session.flush()
        _refresh_parent_terminal_state(session, parent_taxonomy_node_id)
        _remove_scope_target_member(session, taxonomy_id=taxonomy_id, scope_node_id=parent_taxonomy_node_id,
            member_type=TARGET_MEMBER_NODE, member_id=taxonomy_node_id)
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        session.flush()
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return True


def delete_taxonomy_assignment(
    portfolio_id: str,
    taxonomy_id: str,
    assignment_id: str,
) -> bool:
    with _taxonomy_write_session(portfolio_id) as session:
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
        target_entity_id = record.target_entity_id
        previous_node_id = record.taxonomy_node_id
        session.delete(record)
        session.flush()
        _remove_scope_target_member(session, taxonomy_id=taxonomy_id, scope_node_id=previous_node_id,
            member_type="instrument", member_id=target_entity_id)
        _refresh_portfolio_instrument_universe_records(
            session,
            portfolio_id,
            {target_entity_id},
        )
        _record_taxonomy_configuration_revision_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return True




def _initialize_created_portfolio_access(session, portfolio_id: str) -> None:
    try:
        principal = current_principal()
    except IdentityError:
        return
    from portfolio_app.services.portfolio_access import initialize_access
    session.flush()
    initialize_access(session, portfolio_id, principal)


def _lock_portfolio_id_allocation(session) -> None:
    # Names can resolve to the same slug, including the first portfolio. There
    # is no existing portfolio row to lock in that case; serialize PostgreSQL
    # ID allocation across create and copy until their transaction commits.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(func.hashtext("portfolio_record.id_allocation"))))


def create_portfolio(
    name: str | None = None,
    *,
    base_currency: str,
    inception_date: date,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        _lock_portfolio_id_allocation(session)
        portfolios = session.scalars(select(PortfolioRecordModel)).all()
        resolved_name = (name or "").strip() or "新组合"
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
            base_currency=base_currency,
            valuation_timezone="Asia/Shanghai",
            valuation_cutoff_policy="latest_complete_eod",
            inception_date=inception_date,
            as_of_date=date.today(),
            nav=0.0,
            day_change_value=0.0,
            day_change_pct=0.0,
            securities_count=0,
            sort_order=len(portfolios),
            risk_policy_json=None,
        )
        session.add(record)
        _initialize_created_portfolio_access(session, record.portfolio_id)
        session.commit()
        return _serialize_portfolio_row(record)


def update_portfolio_settings(
    portfolio_id: str,
    *,
    name: str | None = None,
    base_currency: str | None = None,
) -> dict[str, object] | None:
    normalized_name = name.strip() if name is not None else None
    if normalized_name is not None and not 1 <= len(normalized_name) <= 200:
        raise ValueError("Portfolio name must contain 1 to 200 characters.")
    normalized_currency = base_currency.strip().upper() if base_currency is not None else None
    if normalized_currency is not None and normalized_currency not in SUPPORTED_FX_CURRENCIES:
        raise ValueError("Unsupported portfolio base currency.")

    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.scalar(
            select(PortfolioRecordModel)
            .where(PortfolioRecordModel.portfolio_id == portfolio_id)
            .with_for_update()
        )
        if record is None:
            return None
        if normalized_name is not None:
            record.portfolio_name = normalized_name
        if normalized_currency is None or record.base_currency == normalized_currency:
            session.commit()
            return _serialize_portfolio_row_with_materialized_summary(
                session,
                record,
            )

        record.base_currency = normalized_currency
        record.nav = None
        record.day_change_value = None
        record.day_change_pct = None
        session.execute(
            delete(PortfolioDailyContributionSliceModel).where(
                PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id
            )
        )
        session.execute(
            delete(PortfolioDailyHoldingSnapshotModel).where(
                PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id
            )
        )
        session.execute(
            delete(PortfolioDailySnapshotModel).where(
                PortfolioDailySnapshotModel.portfolio_id == portfolio_id
            )
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=None,
            session=session,
        )
        session.commit()
        return _serialize_portfolio_row(record)


def copy_portfolio(portfolio_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        _lock_portfolio_id_allocation(session)
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            return None
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
            inception_date=source.inception_date,
            as_of_date=source.as_of_date,
            nav=source.nav,
            day_change_value=source.day_change_value,
            day_change_pct=source.day_change_pct,
            securities_count=source.securities_count,
            sort_order=len(portfolios),
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
                    account_category=account.account_category,
                    currency=account.currency,
                    institution=account.institution,
                    default_settlement_cash_account_id=(
                        account_id_map.get(account.default_settlement_cash_account_id)
                        if account.default_settlement_cash_account_id
                        else None
                    ),
                    cost_basis_method=account.cost_basis_method,
                    cash_purpose=account.cash_purpose,
                    collateral_reference=account.collateral_reference,
                    opened_at=account.opened_at,
                    closed_at=account.closed_at,
                    status=account.status,
                )
            )

        source_derivative_contracts = session.scalars(
            select(DerivativeContractRecordModel).where(
                DerivativeContractRecordModel.portfolio_id == portfolio_id
            )
        ).all()
        derivative_contract_id_map: dict[str, str] = {}
        for contract in source_derivative_contracts:
            copied_contract_id = contract.derivative_contract_id
            derivative_contract_id_map[contract.derivative_contract_id] = copied_contract_id
            session.add(
                DerivativeContractRecordModel(
                    derivative_contract_id=copied_contract_id,
                    portfolio_id=candidate,
                    account_id=account_id_map[contract.account_id],
                    contract_name=contract.contract_name,
                    contract_type=contract.contract_type,
                    currency=contract.currency,
                    external_reference=contract.external_reference,
                    terms_json=deepcopy(contract.terms_json),
                    row_version=contract.row_version,
                    amendments_json=deepcopy(contract.amendments_json),
                    created_at=contract.created_at,
                )
            )

        source_taxonomies = session.scalars(
            select(TaxonomyRecordModel).where(TaxonomyRecordModel.portfolio_id == portfolio_id)
        ).all()
        taxonomy_id_map: dict[str, str] = {}
        for taxonomy in source_taxonomies:
            taxonomy_id_map[taxonomy.taxonomy_id] = f"tax-{uuid4().hex}"

        for taxonomy in source_taxonomies:
            session.add(
                TaxonomyRecordModel(
                    taxonomy_id=taxonomy_id_map[taxonomy.taxonomy_id],
                    portfolio_id=candidate,
                    name=taxonomy.name,
                    taxonomy_type=taxonomy.taxonomy_type,
                    purpose=taxonomy.purpose,
                    primary_assignment_scope=taxonomy.primary_assignment_scope,
                    root_allocation_basis=taxonomy.root_allocation_basis,
                    status=taxonomy.status,
                    source_template_ref=taxonomy.source_template_ref,
                )
            )

        source_taxonomy_nodes = session.scalars(
            select(TaxonomyNodeRecordModel).where(
                TaxonomyNodeRecordModel.taxonomy_id.in_(list(taxonomy_id_map.keys()))
            )
        ).all()
        taxonomy_node_id_map: dict[str, str] = {}
        for node in source_taxonomy_nodes:
            taxonomy_node_id_map[node.taxonomy_node_id] = f"tax-node-{uuid4().hex}"

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
                    allocation_basis=node.allocation_basis,
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
                    target_value=line.target_value,
                    notes=line.notes,
                )
            )

        session.flush()
        for copied_taxonomy_id in taxonomy_id_map.values():
            _record_taxonomy_configuration_revision_in_session(
                session, portfolio_id=candidate, taxonomy_id=copied_taxonomy_id,
            )

        # Limits are dated portfolio configuration. Keep the full schedule and
        # remap deleted identities too, consistently across every revision.
        concentration_taxonomy_ids = dict(taxonomy_id_map)
        concentration_node_ids = dict(taxonomy_node_id_map)
        for revision in session.scalars(select(ConcentrationPolicyRevisionModel).where(
                ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id).order_by(
                ConcentrationPolicyRevisionModel.revision)):
            settings = deepcopy(revision.settings_json)
            # Original migration evidence belongs to the source portfolio and
            # cannot be replayed as a downgrade of these remapped identities.
            settings["copied_from_portfolio_id"] = portfolio_id
            settings["enabled_taxonomy_ids"] = [
                concentration_taxonomy_ids.setdefault(taxonomy_id, f"tax-{uuid4().hex}")
                for taxonomy_id in settings.get("enabled_taxonomy_ids", [])
            ]
            for limit in settings.get("limits", []):
                if limit["scope"] == "taxonomy":
                    limit["taxonomy_id"] = concentration_taxonomy_ids.setdefault(
                        limit["taxonomy_id"], f"tax-{uuid4().hex}")
                    limit["entity_id"] = concentration_node_ids.setdefault(
                        limit["entity_id"], f"tax-node-{uuid4().hex}")
                elif limit["scope"] == "fcn":
                    # Contract identities are portfolio-local and currently
                    # retained by the copy, but use the same explicit mapping.
                    limit["entity_id"] = derivative_contract_id_map.get(limit["entity_id"], limit["entity_id"])
            for allocation in settings.get("fcn_allocations", []):
                allocation["contract_id"] = derivative_contract_id_map.get(allocation["contract_id"], allocation["contract_id"])
            session.add(ConcentrationPolicyRevisionModel(
                portfolio_id=candidate, revision=revision.revision,
                effective_from=revision.effective_from, settings_json=settings,
                created_by=revision.created_by, created_at=revision.created_at,
            ))

        source_transactions = [
            _serialize_transaction_row(item)
            for item in session.scalars(
                select(TransactionRecordModel)
                .where(TransactionRecordModel.portfolio_id == portfolio_id)
                .order_by(
                    TransactionRecordModel.trade_date,
                    TransactionRecordModel.trade_at,
                    TransactionRecordModel.created_at,
                    TransactionRecordModel.transaction_sequence,
                )
            ).all()
        ]
        source_option_delivery_links = session.scalars(
            select(OptionDeliveryLinkModel).where(
                OptionDeliveryLinkModel.portfolio_id == portfolio_id
            )
        ).all()
        copied_transaction_identities = _allocate_transaction_identities(
            session,
            len(source_transactions),
        )
        transaction_id_map = {str(tx["transaction_id"]): identity[0] for tx, identity in zip(source_transactions, copied_transaction_identities, strict=True)}
        for transaction, (copied_transaction_id, copied_transaction_sequence) in zip(
            source_transactions,
            copied_transaction_identities,
            strict=True,
        ):
            transaction_id_map[str(transaction["transaction_id"])] = copied_transaction_id
            copied_transaction = deepcopy(transaction)
            copied_transaction["transaction_id"] = copied_transaction_id
            copied_transaction["transaction_sequence"] = copied_transaction_sequence
            copied_transaction["portfolio_id"] = candidate
            for selection in copied_transaction.get("lot_selections") or []:
                selection["opening_transaction_id"] = transaction_id_map[selection["opening_transaction_id"]]
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
            for delivery in copied_transaction.get("asset_deliveries") or []:
                delivery["account_id"] = account_id_map[delivery["account_id"]]
                if delivery.get("settlement_cash_account_id"):
                    delivery["settlement_cash_account_id"] = account_id_map[delivery["settlement_cash_account_id"]]
            for flow in copied_transaction.get("settlement_cashflows") or []:
                flow["cash_account_id"] = account_id_map[flow["cash_account_id"]]
            source_quantity = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_quantity",
                projection_key="quantity",
                quantum=QUANTITY_SOURCE_QUANTUM,
            )
            source_price = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_price",
                projection_key="price",
                quantum=PRICE_SOURCE_QUANTUM,
            )
            source_gross_amount = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_gross_amount",
                projection_key="gross_amount",
                quantum=AMOUNT_SOURCE_QUANTUM,
            ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
            source_counter_amount = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_counter_amount",
                projection_key="counter_amount",
                quantum=AMOUNT_SOURCE_QUANTUM,
            )
            source_fx_rate = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_fx_rate",
                projection_key="fx_rate",
                quantum=PRICE_SOURCE_QUANTUM,
            )
            source_fees = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_fees",
                projection_key="fees",
                quantum=AMOUNT_SOURCE_QUANTUM,
            ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
            source_taxes = _transaction_source_from_mapping(
                copied_transaction,
                source_key="source_taxes",
                projection_key="taxes",
                quantum=AMOUNT_SOURCE_QUANTUM,
            ) or Decimal("0").quantize(AMOUNT_SOURCE_QUANTUM)
            session.add(
                TransactionRecordModel(
                    transaction_id=str(copied_transaction["transaction_id"]),
                    transaction_sequence=int(copied_transaction["transaction_sequence"]),
                    portfolio_id=str(copied_transaction["portfolio_id"]),
                    transaction_type=str(copied_transaction["transaction_type"]),
                    lifecycle_event_type=(
                        str(copied_transaction["lifecycle_event_type"])
                        if copied_transaction.get("lifecycle_event_type")
                        else None
                    ),
                    trade_date=date.fromisoformat(str(copied_transaction["trade_date"])),
                    trade_time=str(copied_transaction["trade_time"]),
                    trade_at=str(copied_transaction["trade_at"]),
                    trade_timezone=str(copied_transaction["trade_timezone"]),
                    trade_time_is_estimated=bool(copied_transaction["trade_time_is_estimated"]),
                    settlement_date=date.fromisoformat(str(copied_transaction["settlement_date"])),
                    position_effective_date=(
                        date.fromisoformat(
                            str(copied_transaction["position_effective_date"])
                        )
                        if copied_transaction.get("position_effective_date")
                        else None
                    ),
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
                    derivative_contract_id=(
                        derivative_contract_id_map.get(
                            str(copied_transaction["derivative_contract_id"])
                        )
                        if copied_transaction.get("derivative_contract_id")
                        else None
                    ),
                    quantity=float(source_quantity) if source_quantity is not None else None,
                    source_quantity=source_quantity,
                    price=float(source_price) if source_price is not None else None,
                    source_price=source_price,
                    gross_amount=float(source_gross_amount),
                    source_gross_amount=source_gross_amount,
                    counter_amount=(float(source_counter_amount) if source_counter_amount is not None else None),
                    source_counter_amount=source_counter_amount,
                    fx_rate=float(source_fx_rate) if source_fx_rate is not None else None,
                    source_fx_rate=source_fx_rate,
                    fees=float(source_fees),
                    source_fees=source_fees,
                    fee_category=str(copied_transaction.get("fee_category") or "unknown"),
                    asset_deliveries_json=deepcopy(copied_transaction.get("asset_deliveries") or []),
                    settlement_cashflows_json=deepcopy(copied_transaction.get("settlement_cashflows") or []),
                    lot_selections_json=deepcopy(copied_transaction.get("lot_selections") or []),
                    taxes=float(source_taxes),
                    source_taxes=source_taxes,
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
                    source_system=(
                        str(copied_transaction["source_system"])
                        if copied_transaction.get("source_system")
                        else None
                    ),
                    external_reference=(
                        str(copied_transaction["external_reference"])
                        if copied_transaction.get("external_reference")
                        else None
                    ),
                    note=str(copied_transaction["note"]) if copied_transaction.get("note") else None,
                    created_at=(
                        str(copied_transaction["created_at"])
                        if copied_transaction.get("created_at")
                        else None
                    ),
                    row_version=1,
                )
            )

        for link in source_option_delivery_links:
            session.add(
                OptionDeliveryLinkModel(
                    portfolio_id=candidate,
                    option_transaction_id=transaction_id_map[
                        link.option_transaction_id
                    ],
                    stock_transaction_id=transaction_id_map[
                        link.stock_transaction_id
                    ],
                    underlying_instrument_id=link.underlying_instrument_id,
                    created_at=link.created_at,
                )
            )

        # Rebuilding from transactions/assignments alone loses manually added
        # candidates and changes FCN-underlying eligibility in the copied tree.
        for member in session.scalars(select(PortfolioInstrumentUniverseRecordModel).where(
                PortfolioInstrumentUniverseRecordModel.portfolio_id == portfolio_id)):
            session.add(PortfolioInstrumentUniverseRecordModel(
                portfolio_id=candidate, instrument_id=member.instrument_id,
                instrument_ref_json=deepcopy(member.instrument_ref_json), source=member.source,
                holding_state=member.holding_state, first_transaction_date=member.first_transaction_date,
                last_transaction_date=member.last_transaction_date, transaction_count=member.transaction_count,
                research_pm_approved=member.research_pm_approved,
                research_pm_approved_at=member.research_pm_approved_at, status=member.status,
                created_at=member.created_at, updated_at=member.updated_at,
            ))
        session.flush()
        _refresh_portfolio_instrument_universe_records(session, candidate)
        _initialize_created_portfolio_access(session, candidate)
        session.commit()
        return _serialize_portfolio_row(copied)


def delete_portfolio(portfolio_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
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
        session.execute(
            delete(OptionDeliveryLinkModel).where(
                OptionDeliveryLinkModel.portfolio_id == portfolio_id
            )
        )
        session.execute(delete(TransactionRecordModel).where(TransactionRecordModel.portfolio_id == portfolio_id))
        session.execute(
            delete(DerivativeContractRecordModel).where(
                DerivativeContractRecordModel.portfolio_id == portfolio_id
            )
        )
        session.execute(delete(AccountRecordModel).where(AccountRecordModel.portfolio_id == portfolio_id))
        session.execute(delete(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_id == portfolio_id))

        session.commit()
        return True


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


def list_derivative_contracts(portfolio_id: str) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(DerivativeContractRecordModel)
            .where(DerivativeContractRecordModel.portfolio_id == portfolio_id)
            .order_by(
                DerivativeContractRecordModel.contract_type,
                DerivativeContractRecordModel.contract_name,
                DerivativeContractRecordModel.derivative_contract_id,
            )
        ).all()
        return [_serialize_derivative_contract_row(record) for record in records]


def amend_derivative_contract(portfolio_id: str, contract_id: str, *, expected_row_version: int,
                              terms: dict[str, object], reason: str, reviewed_by: str) -> dict[str, object]:
    from portfolio_app.api.contracts import DerivativeContractCreate

    with get_session_factory()() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            raise ValueError("Portfolio not found.")
        record = session.get(DerivativeContractRecordModel, (portfolio_id, contract_id))
        if record is None:
            raise ValueError("Derivative contract not found.")
        if record.row_version != expected_row_version:
            raise TransactionRowVersionConflictError("Contract changed; reload before amending.")
        validated = DerivativeContractCreate.model_validate({
            "derivative_contract_id": contract_id, "contract_name": record.contract_name,
            "contract_type": record.contract_type, "external_reference": record.external_reference, "terms": terms,
        })
        before = DerivativeContractCreate.model_validate({
            "derivative_contract_id": contract_id, "contract_name": record.contract_name,
            "contract_type": record.contract_type, "terms": record.terms_json,
        }).terms.model_dump(mode="json")
        next_terms = validated.terms.model_dump(mode="json")
        # Contract identity is not a correction of descriptive/settlement terms.
        # Creating another product must use another contract id.
        identity_fields = {"underlying_instrument_id", "option_type", "strike", "contract_multiplier"} if record.contract_type == "option" else {"notional"}
        if any(before.get(key) != next_terms.get(key) for key in identity_fields):
            raise ValueError("Contract identity cannot be amended; create a separate contract for a different payoff.")
        if record.contract_type == "fcn" and {item["instrument_id"] for item in before["underlyings"]} != {item["instrument_id"] for item in next_terms["underlyings"]}:
            raise ValueError("Contract underlying identities cannot be amended.")
        record.terms_json = next_terms
        record.row_version += 1
        record.amendments_json = [*(record.amendments_json or []), {
            "row_version": record.row_version, "before": before, "after": next_terms,
            "reason": reason, "reviewed_by": reviewed_by, "changed_at": _current_utc_timestamp(),
        }]
        session.flush()
        _validate_portfolio_transaction_history(session, portfolio_id)
        transaction_dates = session.scalars(select(TransactionRecordModel.trade_date).where(
            TransactionRecordModel.portfolio_id == portfolio_id,
            TransactionRecordModel.derivative_contract_id == contract_id,
        )).all()
        _mark_daily_snapshots_stale(portfolio_id, dirty_from=min(transaction_dates, default=None), session=session)
        result = _serialize_derivative_contract_row(record)
        session.commit()
        return result


def get_derivative_contract(
    portfolio_id: str,
    derivative_contract_id: str,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(
            DerivativeContractRecordModel,
            (portfolio_id, derivative_contract_id),
        )
        if record is None:
            return None
        return _serialize_derivative_contract_row(record)


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


def list_option_delivery_links(
    portfolio_id: str,
    *,
    underlying_instrument_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(OptionDeliveryLinkModel).where(
            OptionDeliveryLinkModel.portfolio_id == portfolio_id
        )
        normalized_underlying_id = str(underlying_instrument_id or "").strip()
        if normalized_underlying_id:
            statement = statement.where(
                OptionDeliveryLinkModel.underlying_instrument_id
                == normalized_underlying_id
            )
        records = session.scalars(
            statement.order_by(
                OptionDeliveryLinkModel.created_at,
                OptionDeliveryLinkModel.option_transaction_id,
            )
        ).all()
        return [_serialize_option_delivery_link(record) for record in records]


def option_delivery_transaction_ids(
    portfolio_id: str,
    transaction_id: str,
) -> list[str]:
    normalized_transaction_id = str(transaction_id or "").strip()
    if not normalized_transaction_id:
        return []
    session_factory = get_session_factory()
    with session_factory() as session:
        link = session.scalar(
            select(OptionDeliveryLinkModel).where(
                OptionDeliveryLinkModel.portfolio_id == portfolio_id,
                or_(
                    OptionDeliveryLinkModel.option_transaction_id
                    == normalized_transaction_id,
                    OptionDeliveryLinkModel.stock_transaction_id
                    == normalized_transaction_id,
                ),
            )
        )
        if link is None:
            return [normalized_transaction_id]
        return [link.option_transaction_id, link.stock_transaction_id]


def get_transaction_idempotency_result(
    portfolio_id: str,
    *,
    idempotency_key: str | None,
    operation: str,
    request_payload: dict[str, Any],
) -> list[dict[str, object]] | None:
    normalized_key = _normalize_transaction_idempotency_key(idempotency_key)
    if normalized_key is None:
        return None
    request_hash = _transaction_request_hash([request_payload])
    session_factory = get_session_factory()
    with session_factory() as session:
        return _idempotency_result_in_session(
            session,
            portfolio_id=portfolio_id,
            idempotency_key=normalized_key,
            operation=operation,
            request_hash=request_hash,
        )


def list_transaction_change_logs(
    portfolio_id: str,
    *,
    transaction_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(TransactionChangeLogModel).where(
            TransactionChangeLogModel.portfolio_id == portfolio_id
        )
        if transaction_id:
            statement = statement.where(
                TransactionChangeLogModel.transaction_id == transaction_id
            )
        records = session.scalars(
            statement.order_by(
                TransactionChangeLogModel.changed_at,
                TransactionChangeLogModel.change_id,
            )
        ).all()
        return [_serialize_transaction_change_log_row(item) for item in records]


def _validate_account_storage_contract(
    session,
    *,
    portfolio_id: str,
    account_type: str,
    account_category: str,
    currency: str,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
) -> str:
    account_category = validate_account_category(
        account_type=account_type,
        account_category=account_category,
    )
    if account_category == "cash":
        if default_settlement_cash_account_id is not None:
            raise ValueError("Cash accounts must not carry a default settlement account.")
        if cost_basis_method is not None:
            raise ValueError("Cash accounts must not carry a cost-basis method.")
        return account_category

    if not default_settlement_cash_account_id:
        raise ValueError("Holding accounts require a default settlement cash account.")
    if cost_basis_method not in {"fifo", "moving_average"}:
        raise ValueError("Holding accounts require FIFO or moving-average cost basis.")
    settlement_account = session.scalar(
        select(AccountRecordModel).where(
            AccountRecordModel.portfolio_id == portfolio_id,
            AccountRecordModel.account_id == default_settlement_cash_account_id,
        )
    )
    if settlement_account is None or settlement_account.account_type != "deposit_account":
        raise ValueError("Default settlement account must be a cash account in this portfolio.")
    if settlement_account.currency.upper() != currency.upper():
        raise ValueError("Settlement cash mapping must use the same currency.")
    return account_category


def _validate_cash_purpose(category, purpose, reference):
    if category != "cash" and (purpose is not None or reference is not None):
        raise ValueError("Cash purpose and collateral reference belong to cash accounts only.")
    if purpose not in {None, "operating", "margin", "collateral", "financing"}:
        raise ValueError("Unknown cash purpose.")
    if reference and purpose != "collateral":
        raise ValueError("Collateral reference requires a collateral cash account.")


def create_account(
    portfolio_id: str,
    *,
    account_name: str,
    account_type: str,
    account_category: str,
    currency: str,
    institution: str | None,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
    opened_at: date | None,
    closed_at: date | None,
    status: str,
    cash_purpose: str | None = None,
    collateral_reference: str | None = None,
) -> dict[str, object]:
    _validate_cash_purpose(account_category, cash_purpose, collateral_reference)
    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            raise ValueError("Portfolio no longer exists.")
        normalized_account_category = _validate_account_storage_contract(
            session,
            portfolio_id=portfolio_id,
            account_type=account_type,
            account_category=account_category,
            currency=currency,
            default_settlement_cash_account_id=default_settlement_cash_account_id,
            cost_basis_method=cost_basis_method,
        )
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
            cash_purpose=cash_purpose,
            collateral_reference=collateral_reference,
            account_type=account_type,
            account_category=normalized_account_category,
            currency=currency.upper(),
            institution=(institution or "").strip() or None,
            default_settlement_cash_account_id=default_settlement_cash_account_id,
            cost_basis_method=cost_basis_method,
            opened_at=opened_at,
            closed_at=closed_at,
            status=(status or "active").strip() or "active",
        )
        session.add(record)
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=opened_at,
            session=session,
        )
        session.commit()
        return _serialize_account_row(record)


def update_account(
    portfolio_id: str,
    account_id: str,
    *,
    account_name: str,
    account_category: str,
    institution: str | None,
    default_settlement_cash_account_id: str | None,
    cost_basis_method: str | None,
    opened_at: date | None,
    closed_at: date | None,
    status: str,
    cash_purpose: str | None = None,
    collateral_reference: str | None = None,
) -> dict[str, object] | None:
    _validate_cash_purpose(account_category, cash_purpose, collateral_reference)
    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            return None
        record = session.scalar(
            select(AccountRecordModel).where(
                AccountRecordModel.portfolio_id == portfolio_id,
                AccountRecordModel.account_id == account_id,
            )
        )
        if record is None:
            return None

        previous_account_category = record.account_category
        settlement_history_dates: list[date] = []
        for tx in session.scalars(select(TransactionRecordModel).where(
            TransactionRecordModel.portfolio_id == portfolio_id,
            TransactionRecordModel.derivative_contract_id.is_not(None),
        )):
            economic_date = tx.position_effective_date or tx.trade_date
            for leg in tx.asset_deliveries_json or []:
                if leg["account_id"] == account_id:
                    settlement_history_dates.extend([economic_date, date.fromisoformat(leg["delivery_date"]) if leg.get("delivery_date") else economic_date])
                if leg.get("settlement_cash_account_id") == account_id:
                    settlement_history_dates.extend([economic_date, date.fromisoformat(leg["fee_settlement_date"]) if leg.get("fee_settlement_date") else economic_date])
            for flow in tx.settlement_cashflows_json or []:
                if flow["cash_account_id"] == account_id:
                    recognized = date.fromisoformat(flow["recognition_date"]) if flow.get("recognition_date") else economic_date
                    settlement_history_dates.extend([recognized, date.fromisoformat(flow["settlement_date"]) if flow.get("settlement_date") else recognized])
        if settlement_history_dates and (account_category != record.account_category or cost_basis_method != record.cost_basis_method):
            raise ValueError("Account category and cost method cannot change after FCN settlement history exists.")
        if any((opened_at and day < opened_at) or (closed_at and day > closed_at) for day in settlement_history_dates):
            raise ValueError("Account dates must include its FCN settlement history.")
        next_account_category = _validate_account_storage_contract(
            session,
            portfolio_id=portfolio_id,
            account_type=record.account_type,
            account_category=account_category,
            currency=record.currency,
            default_settlement_cash_account_id=default_settlement_cash_account_id,
            cost_basis_method=cost_basis_method,
        )
        if next_account_category != previous_account_category:
            transaction_history_count = session.scalar(
                select(func.count()).select_from(TransactionRecordModel).where(
                    TransactionRecordModel.portfolio_id == portfolio_id,
                    or_(
                        TransactionRecordModel.account_id == account_id,
                        TransactionRecordModel.counterparty_account_id == account_id,
                    ),
                )
            )
            derivative_contract_count = session.scalar(
                select(func.count()).select_from(DerivativeContractRecordModel).where(
                    DerivativeContractRecordModel.portfolio_id == portfolio_id,
                    DerivativeContractRecordModel.account_id == account_id,
                )
            )
            if int(transaction_history_count or 0) or int(derivative_contract_count or 0):
                raise ValueError(
                    "Account category cannot change after transaction or contract history exists."
                )

        previous_opened_at = record.opened_at
        previous_cost_basis_method = record.cost_basis_method
        if cost_basis_method != previous_cost_basis_method:
            has_asset_history = session.scalar(
                select(TransactionRecordModel.transaction_id).where(
                    TransactionRecordModel.portfolio_id == portfolio_id,
                    or_(
                        TransactionRecordModel.account_id == account_id,
                        TransactionRecordModel.counterparty_account_id == account_id,
                    ),
                    or_(
                        TransactionRecordModel.instrument_id.is_not(None),
                        TransactionRecordModel.derivative_contract_id.is_not(None),
                        TransactionRecordModel.transfer_object_type == "position",
                    ),
                ).limit(1)
            )
            if has_asset_history is not None:
                raise ValueError(
                    "Cost basis method cannot change after asset transaction history exists. "
                    "Use a new account for a different method; historical results must not be silently restated."
                )

        record.account_name = account_name.strip()
        record.cash_purpose = cash_purpose
        record.collateral_reference = collateral_reference
        record.account_category = next_account_category
        record.institution = (institution or "").strip() or None
        record.default_settlement_cash_account_id = default_settlement_cash_account_id
        record.cost_basis_method = cost_basis_method
        record.opened_at = opened_at
        record.closed_at = closed_at
        record.status = (status or "active").strip() or "active"
        dirty_from = min(
            (
                candidate
                for candidate in (previous_opened_at, opened_at)
                if candidate is not None
            ),
            default=None,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=dirty_from,
            session=session,
        )
        session.commit()
        return _serialize_account_row(record)


def list_transactions(
    portfolio_id: str,
    *,
    account_id: str | None = None,
    asset_domain: str | None = None,
    asset_subtype: str | None = None,
    transaction_type: str | None = None,
    position_reference_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        has_deliveries = cast(TransactionRecordModel.asset_deliveries_json, String).not_in(["[]", "null"])
        has_cashflows = cast(TransactionRecordModel.settlement_cashflows_json, String).not_in(["[]", "null"])
        statement = select(TransactionRecordModel).options(
            selectinload(TransactionRecordModel.derivative_contract),
        ).where(
            TransactionRecordModel.portfolio_id == portfolio_id
        )
        if account_id:
            statement = statement.where(
                or_(
                    TransactionRecordModel.account_id == account_id,
                    TransactionRecordModel.settlement_cash_account_id == account_id,
                    has_deliveries,
                    has_cashflows,
                    and_(
                        TransactionRecordModel.transaction_type == "fx_conversion",
                        TransactionRecordModel.counterparty_account_id == account_id,
                    ),
                )
            )
        if asset_domain == "security":
            statement = statement.where(or_(TransactionRecordModel.instrument_id.is_not(None), has_deliveries))
        elif asset_domain == "derivative":
            statement = statement.where(
                TransactionRecordModel.derivative_contract_id.is_not(None)
            )
        elif asset_domain == "cash":
            statement = statement.where(
                TransactionRecordModel.instrument_id.is_(None),
                TransactionRecordModel.derivative_contract_id.is_(None),
            )
        if asset_subtype in {"fcn", "option"}:
            statement = statement.where(
                TransactionRecordModel.derivative_contract.has(
                    DerivativeContractRecordModel.contract_type == asset_subtype
                )
            )
        if transaction_type:
            statement = statement.where(TransactionRecordModel.transaction_type == transaction_type)
        if position_reference_id:
            statement = statement.where(
                or_(
                    TransactionRecordModel.instrument_id == position_reference_id,
                    TransactionRecordModel.derivative_contract_id
                    == position_reference_id,
                    has_deliveries,
                )
            )
        if start_date is not None:
            statement = statement.where(TransactionRecordModel.trade_date >= start_date)
        if end_date is not None:
            statement = statement.where(TransactionRecordModel.trade_date <= end_date)

        records = session.scalars(
            statement.order_by(
                TransactionRecordModel.trade_date.desc(),
                TransactionRecordModel.trade_at.desc(),
                TransactionRecordModel.created_at.desc(),
                TransactionRecordModel.transaction_sequence.desc(),
                TransactionRecordModel.settlement_date.desc(),
            )
        ).all()
        return [_serialize_transaction_row(item) for item in records if (
            not account_id or item.account_id == account_id or item.settlement_cash_account_id == account_id
            or (item.transaction_type == "fx_conversion" and item.counterparty_account_id == account_id)
            or any(account_id in {leg["account_id"], leg.get("settlement_cash_account_id")} for leg in item.asset_deliveries_json or [])
            or any(flow["cash_account_id"] == account_id for flow in item.settlement_cashflows_json or [])
        ) and (
            not position_reference_id or position_reference_id in {item.instrument_id, item.derivative_contract_id}
            or any(leg["instrument_id"] == position_reference_id for leg in item.asset_deliveries_json or [])
        )]


def _mark_daily_snapshots_stale(
    portfolio_id: str,
    *,
    dirty_from: date | None = None,
    session=None,
) -> None:
    from portfolio_app.services.daily_snapshots import mark_portfolio_daily_snapshots_stale

    mark_portfolio_daily_snapshots_stale(
        portfolio_id,
        dirty_from=dirty_from,
        session=session,
    )


def _resolve_derivative_contract_for_transaction(
    session,
    *,
    portfolio_id: str,
    account_id: str,
    currency: str,
    derivative_contract_id: str | None = None,
    derivative_contract: dict[str, object] | None = None,
) -> DerivativeContractRecordModel | None:
    normalized_id = str(derivative_contract_id or "").strip()
    if not normalized_id:
        if derivative_contract is not None:
            raise ValueError(
                "Inline derivative contract creation requires derivative_contract_id."
            )
        return None

    existing = session.get(
        DerivativeContractRecordModel,
        (portfolio_id, normalized_id),
    )
    if existing is not None:
        if existing.account_id != account_id:
            raise ValueError("Derivative contract belongs to another account.")
        if existing.currency != currency.upper():
            raise ValueError("Derivative contract currency must match the transaction.")
        if derivative_contract is not None:
            expected = {
                "derivative_contract_id": normalized_id,
                "contract_name": existing.contract_name,
                "contract_type": existing.contract_type,
                "external_reference": existing.external_reference,
                "terms": existing.terms_json,
            }
            supplied = {
                "derivative_contract_id": str(
                    derivative_contract.get("derivative_contract_id") or ""
                ).strip(),
                "contract_name": str(
                    derivative_contract.get("contract_name") or ""
                ).strip(),
                "contract_type": str(
                    derivative_contract.get("contract_type") or ""
                ).strip(),
                "external_reference": (
                    str(derivative_contract.get("external_reference") or "").strip()
                    or None
                ),
                "terms": derivative_contract.get("terms"),
            }
            if supplied != expected:
                raise ValueError(
                    "Derivative contract terms are immutable; reference the existing "
                    "contract without supplying changed terms."
                )
        return existing

    if derivative_contract is None:
        raise ValueError("Derivative contract does not exist in this portfolio.")
    if str(derivative_contract.get("derivative_contract_id") or "").strip() != normalized_id:
        raise ValueError("derivative_contract_id does not match the inline contract.")
    account = session.get(AccountRecordModel, account_id)
    if account is None or account.portfolio_id != portfolio_id:
        raise ValueError("Derivative contract account does not belong to the portfolio.")
    contract_name = str(derivative_contract.get("contract_name") or "").strip()
    contract_type = str(derivative_contract.get("contract_type") or "").strip().lower()
    terms = derivative_contract.get("terms")
    if not contract_name or contract_type not in {"fcn", "option"} or not isinstance(terms, dict):
        raise ValueError("Inline derivative contract is incomplete.")
    external_reference = (
        str(derivative_contract.get("external_reference") or "").strip() or None
    )
    if external_reference is not None:
        existing_reference_id = session.scalar(
            select(DerivativeContractRecordModel.derivative_contract_id)
            .where(
                DerivativeContractRecordModel.portfolio_id == portfolio_id,
                DerivativeContractRecordModel.external_reference
                == external_reference,
            )
            .limit(1)
        )
        if existing_reference_id is not None:
            raise ValueError(
                "Derivative contract external_reference already belongs to "
                f"'{existing_reference_id}' in this portfolio."
            )
    record = DerivativeContractRecordModel(
        derivative_contract_id=normalized_id,
        portfolio_id=portfolio_id,
        account_id=account_id,
        contract_name=contract_name,
        contract_type=contract_type,
        currency=currency.upper(),
        external_reference=external_reference,
        terms_json=deepcopy(terms),
        created_at=_current_utc_timestamp(),
    )
    session.add(record)
    session.flush()
    return record


def create_transaction(
    portfolio_id: str,
    *,
    transaction_type: str,
    lifecycle_event_type: str | None = None,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract_id: str | None = None,
    derivative_contract: dict[str, object] | None = None,
    quantity: Decimal | float | None,
    price: Decimal | float | None,
    gross_amount: Decimal | float,
    counter_amount: Decimal | float | None,
    fx_rate: Decimal | float | None,
    fees: Decimal | float,
    taxes: Decimal | float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    source_system: str | None = None,
    external_reference: str | None = None,
    note: str | None,
    fee_category: str = "unknown",
    created_at: str | None = None,
    position_effective_date: date | None = None,
    idempotency_key: str | None = None,
    idempotency_payload: dict[str, Any] | None = None,
    idempotency_operation: str = "create",
    asset_deliveries: list[dict[str, object]] | None = None,
    settlement_cashflows: list[dict[str, object]] | None = None,
    lot_selections: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    records = create_transactions(
        portfolio_id=portfolio_id,
        records=[
            {
                "asset_deliveries": asset_deliveries or [],
                "settlement_cashflows": settlement_cashflows or [],
                "lot_selections": lot_selections or [],
                "transaction_type": transaction_type,
                "lifecycle_event_type": lifecycle_event_type,
                "trade_date": trade_date,
                "trade_time": trade_time,
                "settlement_date": settlement_date,
                "position_effective_date": position_effective_date,
                "entitlement_date": entitlement_date,
                "acquisition_date": acquisition_date,
                "account_id": account_id,
                "settlement_cash_account_id": settlement_cash_account_id,
                "instrument_id": instrument_id,
                "instrument_ref": instrument_ref,
                "derivative_contract_id": derivative_contract_id,
                "derivative_contract": derivative_contract,
                "quantity": quantity,
                "price": price,
                "gross_amount": gross_amount,
                "counter_amount": counter_amount,
                "fx_rate": fx_rate,
                "fees": fees,
                "fee_category": fee_category,
                "taxes": taxes,
                "currency": currency,
                "transfer_scope": transfer_scope,
                "transfer_object_type": transfer_object_type,
                "transfer_group_id": transfer_group_id,
                "counterparty_account_id": counterparty_account_id,
                "source_system": source_system,
                "external_reference": external_reference,
                "note": note,
                "created_at": created_at,
            }
        ],
        idempotency_key=idempotency_key,
        idempotency_payload=idempotency_payload,
        idempotency_operation=idempotency_operation,
    )
    return records[0]


def create_transactions(
    portfolio_id: str,
    *,
    records: list[dict[str, Any]],
    option_delivery_pair: tuple[int, int, str] | None = None,
    idempotency_key: str | None = None,
    idempotency_payload: dict[str, Any] | None = None,
    idempotency_operation: str = "create_batch",
) -> list[dict[str, object]]:
    if not records:
        return []

    normalized_idempotency_key = _normalize_transaction_idempotency_key(
        idempotency_key
    )
    request_hash = (
        _transaction_request_hash(
            [idempotency_payload] if idempotency_payload is not None else records
        )
        if normalized_idempotency_key is not None
        else None
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            raise ValueError("Portfolio no longer exists.")
        if normalized_idempotency_key is not None and request_hash is not None:
            replayed = _idempotency_result_in_session(
                session,
                portfolio_id=portfolio_id,
                idempotency_key=normalized_idempotency_key,
                operation=idempotency_operation,
                request_hash=request_hash,
            )
            if replayed is not None:
                session.rollback()
                return replayed
        requested_transfer_groups: dict[str, list[dict[str, Any]]] = {}
        for values in records:
            transaction_type = str(values.get("transaction_type") or "").strip()
            transfer_group_id = str(values.get("transfer_group_id") or "").strip()
            if transaction_type in {"transfer_in", "transfer_out"} and not transfer_group_id:
                raise ValueError("Transfer transactions require a transfer_group_id.")
            if transfer_group_id and transaction_type not in {"transfer_in", "transfer_out"}:
                raise ValueError("Only transfer transactions may carry a transfer_group_id.")
            if transfer_group_id:
                requested_transfer_groups.setdefault(transfer_group_id, []).append(values)
        for transfer_group_id, group_records in requested_transfer_groups.items():
            group_transaction_types = [
                str(values.get("transaction_type") or "").strip()
                for values in group_records
            ]
            if len(group_records) != 2 or sorted(group_transaction_types) != [
                "transfer_in",
                "transfer_out",
            ]:
                raise ValueError(
                    f"Transfer group '{transfer_group_id}' must contain exactly one "
                    "transfer_out and one transfer_in transaction."
                )
        requested_transfer_group_ids = sorted(requested_transfer_groups)
        if requested_transfer_group_ids:
            existing_transfer_group_id = session.scalar(
                select(TransactionRecordModel.transfer_group_id)
                .where(
                    TransactionRecordModel.portfolio_id == portfolio_id,
                    TransactionRecordModel.transfer_group_id.in_(
                        requested_transfer_group_ids
                    ),
                )
                .limit(1)
            )
            if existing_transfer_group_id is not None:
                raise ValueError(
                    f"Transfer group '{existing_transfer_group_id}' already exists."
                )
        transaction_identities = _allocate_transaction_identities(session, len(records))
        records = resolve_import_lot_references(records, [identity[0] for identity in transaction_identities])
        created: list[TransactionRecordModel] = []
        for values, (transaction_id, transaction_sequence) in zip(
            records,
            transaction_identities,
            strict=True,
        ):
            record = TransactionRecordModel(
                transaction_id=transaction_id,
                transaction_sequence=transaction_sequence,
                portfolio_id=portfolio_id,
                row_version=1,
            )
            derivative_contract_record = _resolve_derivative_contract_for_transaction(
                session,
                portfolio_id=portfolio_id,
                account_id=str(values["account_id"]),
                currency=str(values["currency"]),
                derivative_contract_id=(
                    str(values["derivative_contract_id"])
                    if values.get("derivative_contract_id")
                    else None
                ),
                derivative_contract=(
                    values["derivative_contract"]
                    if isinstance(values.get("derivative_contract"), dict)
                    else None
                ),
            )
            _apply_transaction_record(
                record,
                asset_deliveries=values.get("asset_deliveries") or [],
                settlement_cashflows=values.get("settlement_cashflows") or [],
                lot_selections=values.get("lot_selections") or [],
                transaction_type=str(values["transaction_type"]),
                lifecycle_event_type=(
                    str(values["lifecycle_event_type"])
                    if values.get("lifecycle_event_type")
                    else None
                ),
                trade_date=values["trade_date"],
                trade_time=values.get("trade_time"),
                settlement_date=values["settlement_date"],
                position_effective_date=values.get("position_effective_date"),
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
                derivative_contract_id=(
                    derivative_contract_record.derivative_contract_id
                    if derivative_contract_record is not None
                    else None
                ),
                quantity=values.get("quantity"),
                price=values.get("price"),
                gross_amount=values["gross_amount"],
                counter_amount=values.get("counter_amount"),
                fx_rate=values.get("fx_rate"),
                fees=values["fees"],
                fee_category=str(values.get("fee_category") or "unknown"),
                taxes=values["taxes"],
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
                source_system=(
                    str(values["source_system"])
                    if values.get("source_system")
                    else None
                ),
                external_reference=(
                    str(values["external_reference"])
                    if values.get("external_reference")
                    else None
                ),
                note=str(values["note"]) if values.get("note") is not None else None,
                created_at=str(values.get("created_at") or _current_utc_timestamp()),
            )
            session.add(record)
            session.flush()
            created.append(record)
        delivery_pairs = [(index, index + 1, str(values["_option_delivery_underlying_id"])) for index, values in enumerate(records) if values.get("_option_delivery_underlying_id")]
        if option_delivery_pair is not None:
            delivery_pairs.append(option_delivery_pair)
        for option_index, stock_index, underlying_instrument_id in delivery_pairs:
            if (
                option_index == stock_index
                or option_index < 0
                or stock_index < 0
                or option_index >= len(created)
                or stock_index >= len(created)
            ):
                raise ValueError("Option delivery pair indexes are invalid.")
            option_record = created[option_index]
            stock_record = created[stock_index]
            normalized_underlying_id = str(underlying_instrument_id or "").strip()
            if not normalized_underlying_id:
                raise ValueError("Option delivery requires an underlying instrument.")
            lifecycle_event_type = str(option_record.lifecycle_event_type or "")
            if lifecycle_event_type not in {
                "option_long_exercise",
                "option_writer_assignment",
            }:
                raise ValueError("Option delivery requires a physical option outcome fact.")
            contract_record = session.get(
                DerivativeContractRecordModel,
                (portfolio_id, str(option_record.derivative_contract_id or "")),
            )
            terms = contract_record.terms_json if contract_record is not None else None
            if (
                contract_record is None
                or contract_record.contract_type != "option"
                or not isinstance(terms, dict)
                or str(terms.get("underlying_instrument_id") or "").strip()
                != normalized_underlying_id
            ):
                raise ValueError("Option delivery underlying does not match the contract.")
            option_type = str(terms.get("option_type") or "").strip().lower()
            expected_stock_type = {
                ("option_long_exercise", "call"): "buy",
                ("option_long_exercise", "put"): "sell",
                ("option_writer_assignment", "call"): "sell",
                ("option_writer_assignment", "put"): "buy",
            }.get((lifecycle_event_type, option_type))
            if (
                expected_stock_type is None
                or stock_record.transaction_type not in ({"sell", "short_sell"} if expected_stock_type == "sell" else {expected_stock_type})
                or str(stock_record.instrument_id or "") != normalized_underlying_id
            ):
                raise ValueError("Linked stock delivery direction does not match the option outcome.")
            option_quantity = option_record.source_quantity
            stock_quantity = stock_record.source_quantity
            multiplier = _transaction_source_decimal(
                terms.get("contract_multiplier"),
                quantum=QUANTITY_SOURCE_QUANTUM,
                field_name="contract_multiplier",
            )
            strike = _transaction_source_decimal(
                terms.get("strike"),
                quantum=PRICE_SOURCE_QUANTUM,
                field_name="strike",
            )
            if (
                option_quantity is None
                or stock_quantity is None
                or multiplier is None
                or strike is None
                or stock_quantity != option_quantity * multiplier
                or stock_record.source_price != strike
            ):
                raise ValueError("Linked stock delivery quantity or strike does not match the option contract.")
            session.add(
                OptionDeliveryLinkModel(
                    portfolio_id=portfolio_id,
                    option_transaction_id=option_record.transaction_id,
                    stock_transaction_id=stock_record.transaction_id,
                    underlying_instrument_id=normalized_underlying_id,
                    created_at=_current_utc_timestamp(),
                )
            )
            session.flush()
        _validate_portfolio_transaction_history(session, portfolio_id)
        serialized_created = [_serialize_transaction_row(record) for record in created]
        for serialized_record in serialized_created:
            _append_transaction_change_log(
                session,
                portfolio_id=portfolio_id,
                transaction_id=str(serialized_record["transaction_id"]),
                change_type="create",
                row_version=1,
                before=None,
                after=serialized_record,
                request_idempotency_key=normalized_idempotency_key,
            )
        if normalized_idempotency_key is not None and request_hash is not None:
            session.add(
                TransactionIdempotencyRecordModel(
                    portfolio_id=portfolio_id,
                    idempotency_key=normalized_idempotency_key,
                    operation=idempotency_operation,
                    request_hash=request_hash,
                    transaction_ids_json=[record.transaction_id for record in created],
                    created_at=_transaction_change_timestamp(),
                )
            )
        dirty_from = min(
            (
                affected_date
                for record in serialized_created
                for affected_date in transaction_affected_dates(record)
            ),
            default=None,
        )
        affected_instrument_ids = {
            str(record.instrument_id or "").strip()
            for record in created
            if str(record.instrument_id or "").strip()
        }
        affected_instrument_ids.update(str(leg["instrument_id"]) for record in created for leg in record.asset_deliveries_json or [])
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=dirty_from,
            session=session,
        )
        session.commit()
        return serialized_created


def update_transaction(
    portfolio_id: str,
    transaction_id: str,
    *,
    transaction_type: str,
    lifecycle_event_type: str | None = None,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract_id: str | None = None,
    derivative_contract: dict[str, object] | None = None,
    quantity: Decimal | float | None,
    price: Decimal | float | None,
    gross_amount: Decimal | float,
    counter_amount: Decimal | float | None,
    fx_rate: Decimal | float | None,
    fees: Decimal | float,
    taxes: Decimal | float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    source_system: str | None = None,
    external_reference: str | None = None,
    note: str | None,
    fee_category: str = "unknown",
    created_at: str | None = None,
    position_effective_date: date | None = None,
    expected_row_version: int | None = None,
    asset_deliveries: list[dict[str, object]] | None = None,
    settlement_cashflows: list[dict[str, object]] | None = None,
    lot_selections: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            return None
        record = session.scalar(
            select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.transaction_id == transaction_id,
            )
        )
        if record is None:
            return None
        current_row_version = int(record.row_version)
        if (
            expected_row_version is not None
            and int(expected_row_version) != current_row_version
        ):
            raise TransactionRowVersionConflictError(
                "Transaction row version is stale; reload and retry "
                f"(expected {expected_row_version}, current {current_row_version})."
            )
        before = _serialize_transaction_row(record)
        previous_instrument_id = str(record.instrument_id or "").strip()
        derivative_contract_record = _resolve_derivative_contract_for_transaction(
            session,
            portfolio_id=portfolio_id,
            account_id=account_id,
            currency=currency,
            derivative_contract_id=derivative_contract_id,
            derivative_contract=derivative_contract,
        )
        _apply_transaction_record(
            record,
            asset_deliveries=asset_deliveries or [],
            settlement_cashflows=settlement_cashflows or [],
            lot_selections=lot_selections or [],
            transaction_type=transaction_type,
            lifecycle_event_type=lifecycle_event_type,
            trade_date=trade_date,
            trade_time=trade_time,
            settlement_date=settlement_date,
            position_effective_date=position_effective_date,
            entitlement_date=entitlement_date,
            acquisition_date=acquisition_date,
            account_id=account_id,
            settlement_cash_account_id=settlement_cash_account_id,
            instrument_id=instrument_id,
            instrument_ref=instrument_ref,
            derivative_contract_id=(
                derivative_contract_record.derivative_contract_id
                if derivative_contract_record is not None
                else None
            ),
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            counter_amount=counter_amount,
            fx_rate=fx_rate,
            fees=fees,
            fee_category=fee_category,
            taxes=taxes,
            currency=currency,
            transfer_scope=transfer_scope,
            transfer_object_type=transfer_object_type,
            transfer_group_id=transfer_group_id,
            counterparty_account_id=counterparty_account_id,
            source_system=source_system,
            external_reference=external_reference,
            note=note,
            created_at=created_at or record.created_at or _current_utc_timestamp(),
        )
        record.row_version = current_row_version + 1
        affected_instrument_ids = {
            instrument_id
            for instrument_id in {previous_instrument_id, str(record.instrument_id or "").strip()}
            if instrument_id
        }
        affected_instrument_ids.update(str(leg["instrument_id"]) for leg in [*(before.get("asset_deliveries") or []), *(record.asset_deliveries_json or [])])
        session.flush()
        _validate_portfolio_transaction_history(session, portfolio_id)
        after = _serialize_transaction_row(record)
        dirty_from = min(
            (
                affected_date
                for payload in (before, after)
                for affected_date in transaction_affected_dates(payload)
            ),
            default=None,
        )
        _append_transaction_change_log(
            session,
            portfolio_id=portfolio_id,
            transaction_id=transaction_id,
            change_type="update",
            row_version=current_row_version + 1,
            before=before,
            after=after,
        )
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=dirty_from,
            session=session,
        )
        session.commit()
        return after


def delete_transactions(
    portfolio_id: str,
    *,
    transaction_ids: list[str],
    expected_row_versions: dict[str, int],
) -> list[dict[str, object]]:
    normalized_transaction_ids = [transaction_id.strip() for transaction_id in transaction_ids if transaction_id.strip()]
    if not normalized_transaction_ids:
        return []
    requested_transaction_ids = set(normalized_transaction_ids)
    if set(expected_row_versions) != requested_transaction_ids:
        raise TransactionRowVersionConflictError(
            "Transaction delete scope changed; reload and retry with row versions "
            "for the complete delete scope."
        )

    session_factory = get_session_factory()
    with session_factory() as session:
        if not _lock_portfolio_for_transaction_mutation(session, portfolio_id):
            return []
        delivery_links = session.scalars(
            select(OptionDeliveryLinkModel).where(
                OptionDeliveryLinkModel.portfolio_id == portfolio_id,
                or_(
                    OptionDeliveryLinkModel.option_transaction_id.in_(
                        normalized_transaction_ids
                    ),
                    OptionDeliveryLinkModel.stock_transaction_id.in_(
                        normalized_transaction_ids
                    ),
                ),
            )
        ).all()
        for link in delivery_links:
            pair_ids = {
                link.option_transaction_id,
                link.stock_transaction_id,
            }
            if not pair_ids.issubset(requested_transaction_ids):
                raise ValueError(
                    "Physical option settlement transactions must be deleted as a pair."
                )
        records = session.scalars(
            select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.transaction_id.in_(normalized_transaction_ids),
            )
        ).all()
        records_by_id = {record.transaction_id: record for record in records}
        for transaction_id, expected_row_version in expected_row_versions.items():
            record = records_by_id.get(transaction_id)
            if record is None:
                raise TransactionRowVersionConflictError(
                    f"Transaction '{transaction_id}' was deleted; reload and retry."
                )
            current_row_version = int(record.row_version)
            if current_row_version != expected_row_version:
                raise TransactionRowVersionConflictError(
                    "Transaction row version is stale; reload and retry "
                    f"(expected {expected_row_version}, current {current_row_version})."
                )
        serialized = [_serialize_transaction_row(record) for record in records]
        affected_instrument_ids = {
            str(record.instrument_id or "").strip()
            for record in records
            if str(record.instrument_id or "").strip()
        }
        affected_instrument_ids.update(str(leg["instrument_id"]) for record in records for leg in record.asset_deliveries_json or [])
        for link in delivery_links:
            session.delete(link)
        for record in records:
            session.delete(record)
        session.flush()
        _validate_portfolio_transaction_history(session, portfolio_id)
        for serialized_record in serialized:
            _append_transaction_change_log(
                session,
                portfolio_id=portfolio_id,
                transaction_id=str(serialized_record["transaction_id"]),
                change_type="delete",
                row_version=int(serialized_record["row_version"]) + 1,
                before=serialized_record,
                after=None,
            )
        _refresh_portfolio_instrument_universe_records(session, portfolio_id, affected_instrument_ids)
        dirty_from = min(
            (
                affected_date
                for record in serialized
                for affected_date in transaction_affected_dates(record)
            ),
            default=None,
        )
        _mark_daily_snapshots_stale(
            portfolio_id,
            dirty_from=dirty_from,
            session=session,
        )
        session.commit()
        return serialized


def _apply_transaction_record(
    record: TransactionRecordModel,
    *,
    transaction_type: str,
    lifecycle_event_type: str | None,
    trade_date: date,
    trade_time: str | None,
    settlement_date: date,
    position_effective_date: date | None,
    entitlement_date: date | None,
    acquisition_date: date | None,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract_id: str | None,
    quantity: Decimal | float | None,
    price: Decimal | float | None,
    gross_amount: Decimal | float,
    counter_amount: Decimal | float | None,
    fx_rate: Decimal | float | None,
    fees: Decimal | float,
    fee_category: str,
    taxes: Decimal | float,
    currency: str,
    transfer_scope: str | None,
    transfer_object_type: str | None,
    transfer_group_id: str | None,
    counterparty_account_id: str | None,
    source_system: str | None,
    external_reference: str | None,
    note: str | None,
    created_at: str,
    asset_deliveries: list[dict[str, object]] | None = None,
    settlement_cashflows: list[dict[str, object]] | None = None,
    lot_selections: list[dict[str, object]] | None = None,
) -> None:
    if instrument_id and derivative_contract_id:
        raise ValueError(
            f"Transaction '{record.transaction_id}' may not reference both an "
            "instrument and a derivative contract."
        )
    if isinstance(instrument_ref, dict):
        _validate_instrument_ref_contract(
            instrument_ref,
            context=f"Transaction '{record.transaction_id}'",
            expected_instrument_id=instrument_id,
        )
    elif instrument_id:
        raise ValueError(f"Transaction '{record.transaction_id}' with instrument_id requires instrument_ref.")

    source_quantity = _transaction_source_decimal(
        quantity,
        quantum=QUANTITY_SOURCE_QUANTUM,
        field_name="quantity",
    )
    source_price = _transaction_source_decimal(
        price,
        quantum=PRICE_SOURCE_QUANTUM,
        field_name="price",
    )
    source_gross_amount = _transaction_source_decimal(
        gross_amount,
        quantum=AMOUNT_SOURCE_QUANTUM,
        field_name="gross_amount",
    )
    source_counter_amount = _transaction_source_decimal(
        counter_amount,
        quantum=AMOUNT_SOURCE_QUANTUM,
        field_name="counter_amount",
    )
    source_fx_rate = _transaction_source_decimal(
        fx_rate,
        quantum=PRICE_SOURCE_QUANTUM,
        field_name="fx_rate",
    )
    source_fees = _transaction_source_decimal(
        fees,
        quantum=AMOUNT_SOURCE_QUANTUM,
        field_name="fees",
    )
    source_taxes = _transaction_source_decimal(
        taxes,
        quantum=AMOUNT_SOURCE_QUANTUM,
        field_name="taxes",
    )
    if source_gross_amount is None or source_fees is None or source_taxes is None:
        raise ValueError("Transaction gross_amount, fees, and taxes are required.")

    resolved_timing = resolve_trade_timing(trade_date=trade_date, trade_time=trade_time)
    record.transaction_type = transaction_type
    record.asset_deliveries_json = deepcopy(asset_deliveries or [])
    record.settlement_cashflows_json = deepcopy(settlement_cashflows or [])
    record.lot_selections_json = deepcopy(lot_selections or [])
    record.lifecycle_event_type = lifecycle_event_type
    record.trade_date = trade_date
    record.trade_time = str(resolved_timing["trade_time"])
    record.trade_at = str(resolved_timing["trade_at"])
    record.trade_timezone = str(resolved_timing["trade_timezone"])
    record.trade_time_is_estimated = bool(resolved_timing["trade_time_is_estimated"])
    record.settlement_date = settlement_date
    record.position_effective_date = position_effective_date
    record.entitlement_date = entitlement_date
    record.acquisition_date = acquisition_date
    record.account_id = account_id
    record.settlement_cash_account_id = settlement_cash_account_id
    record.instrument_id = instrument_id
    record.instrument_ref_json = deepcopy(instrument_ref) if isinstance(instrument_ref, dict) else None
    record.derivative_contract_id = derivative_contract_id
    record.source_quantity = source_quantity
    record.quantity = float(source_quantity) if source_quantity is not None else None
    record.source_price = source_price
    record.price = float(source_price) if source_price is not None else None
    record.source_gross_amount = source_gross_amount
    record.gross_amount = float(source_gross_amount)
    record.source_counter_amount = source_counter_amount
    record.counter_amount = float(source_counter_amount) if source_counter_amount is not None else None
    record.source_fx_rate = source_fx_rate
    record.fx_rate = float(source_fx_rate) if source_fx_rate is not None else None
    record.source_fees = source_fees
    record.fees = float(source_fees)
    record.fee_category = fee_category or "unknown"
    record.source_taxes = source_taxes
    record.taxes = float(source_taxes)
    record.currency = currency.upper()
    record.transfer_scope = transfer_scope
    record.transfer_object_type = transfer_object_type
    record.transfer_group_id = transfer_group_id
    record.counterparty_account_id = counterparty_account_id
    record.source_system = (source_system or "").strip() or None
    record.external_reference = (external_reference or "").strip() or None
    record.note = (note or "").strip() or None
    record.created_at = created_at
