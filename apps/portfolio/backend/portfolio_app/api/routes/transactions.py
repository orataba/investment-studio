from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from portfolio_app.api.assemblers import (
    resolve_transaction_flow_scope,
    resolve_transaction_net_cash_effect,
    serialize_transaction,
    summarize_transactions,
)
from portfolio_app.api.contracts import (
    AccountRecord,
    AssetCoreContract,
    DerivationBoundaryStatus,
    InternalTransferCreateRequest,
    SharedInstrumentListResponse,
    LedgerPostingListSummary,
    PositionLotListSummary,
    TransactionBatchResponse,
    TransactionCreateRequest,
    TransactionDeleteResponse,
    TransactionListResponse,
    TransactionListSummary,
    TransactionPositionPreviewResponse,
    TransactionRecord,
    TransactionWorkspaceResponse,
)
from portfolio_app.services.ledger import (
    build_position_lots,
    estimate_position_cost_basis,
    estimate_position_quantity,
    estimate_position_remaining_cost_basis,
    list_ledger_postings,
    summarize_ledger_postings,
    summarize_position_lots,
)
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_registry_instrument,
    list_registry_instruments,
)
from portfolio_app.services.daily_snapshots import refresh_portfolio_daily_snapshots
from portfolio_app.services.portfolio_store import (
    create_transaction,
    create_transactions,
    delete_transactions,
    get_account,
    get_portfolio,
    get_transaction,
    list_accounts,
    list_transactions,
    resolve_trade_timing,
    update_transaction,
)


router = APIRouter()

POSITION_ASSET_TYPES = {"fund", "bond", "equity", "other"}
ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES = {"buy", "dividend_reinvestment", "opening_balance"}
INCOME_ASSET_TYPES: dict[str, set[str]] = {
    "dividend": {"fund", "equity"},
    "dividend_reinvestment": {"fund", "equity"},
    "coupon": {"bond"},
    "return_of_capital": {"fund", "equity"},
    "maturity_redemption": {"bond"},
}


def _queue_daily_snapshot_refresh(background_tasks: BackgroundTasks | None, portfolio_id: str) -> None:
    if background_tasks is not None:
        background_tasks.add_task(refresh_portfolio_daily_snapshots, portfolio_id)


def _resolve_flow_scope(transaction_type: str) -> str:
    return resolve_transaction_flow_scope(transaction_type)


def _resolve_net_cash_effect(record: dict[str, object]) -> float | None:
    return resolve_transaction_net_cash_effect(record)


def _serialize_transaction(
    portfolio_id: str,
    record: dict[str, object],
    account_lookup: dict[str, dict[str, object]],
) -> TransactionRecord:
    return serialize_transaction(portfolio_id, record, account_lookup)


def _build_summary(records: list[dict[str, object]]) -> TransactionListSummary:
    return summarize_transactions(records)


def _load_instrument_ref(asset_id: str) -> dict[str, object]:
    try:
        instrument = get_registry_instrument(asset_id)
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if instrument is None:
        raise HTTPException(status_code=400, detail="Instrument not found in shared registry.")

    return {
        "asset_id": instrument["asset_id"],
        "asset_name": instrument["asset_name"],
        "asset_type": instrument["asset_type"],
        "currency": instrument["currency"],
        "identifiers": instrument.get("identifiers", []),
    }


def _serialize_instrument_option(record: dict[str, object]) -> dict[str, object]:
    coverage_state = str(record.get("coverage_state") or "").strip() or None
    if coverage_state is None:
        coverage_state = "complete" if record.get("latest_market_data") else "unavailable"
    return {
        "asset_core": {
            "asset_id": record["asset_id"],
            "asset_name": record["asset_name"],
            "asset_type": record["asset_type"],
            "currency": record["currency"],
            "identifiers": record.get("identifiers", []),
        },
        "coverage_state": coverage_state,
        "latest_market_data": record.get("latest_market_data", []),
        "quote_selection_policy": record.get("quote_selection_policy", {}),
    }


def _currency_of(account: dict[str, object] | None) -> str:
    return str((account or {}).get("currency") or "").upper()


def _parse_account_boundary_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return None
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            return None
    return None


def _validate_account_fact_window(
    account: dict[str, object],
    *,
    event_dates: list[date],
    role_label: str,
) -> None:
    account_label = str(account.get("account_name") or account.get("account_id") or role_label)
    opened_at = _parse_account_boundary_date(account.get("opened_at"))
    closed_at = _parse_account_boundary_date(account.get("closed_at"))
    account_status = str(account.get("status") or "").strip().lower()

    if account_status == "closed" and closed_at is None:
        raise HTTPException(
            status_code=400,
            detail=f"{role_label} '{account_label}' is closed and cannot accept new facts.",
        )

    for event_date in sorted(set(event_dates)):
        if opened_at is not None and event_date < opened_at:
            raise HTTPException(
                status_code=400,
                detail=f"{role_label} '{account_label}' is not open on {event_date.isoformat()}.",
            )
        if closed_at is not None and event_date > closed_at:
            raise HTTPException(
                status_code=400,
                detail=f"{role_label} '{account_label}' is closed on {event_date.isoformat()}.",
            )


def _exclude_transactions(
    records: list[dict[str, object]],
    *,
    transaction_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    if not transaction_ids:
        return records
    return [
        record
        for record in records
        if str(record.get("transaction_id") or "") not in transaction_ids
    ]


def _list_transactions_as_of_trade_date(
    portfolio_id: str,
    trade_date: date,
    *,
    exclude_transaction_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    return _exclude_transactions(
        list_transactions(portfolio_id, end_date=trade_date),
        transaction_ids=exclude_transaction_ids,
    )


def _transaction_sort_key(record: dict[str, object]) -> tuple[str, str, str, str, str]:
    return (
        str(record.get("trade_date") or ""),
        str(record.get("trade_at") or ""),
        str(record.get("created_at") or ""),
        str(record.get("transaction_id") or ""),
        str(record.get("settlement_date") or ""),
    )


def _pending_transaction_sort_key(
    *,
    trade_date: date,
    trade_at: str,
    created_at: str,
    settlement_date: date,
) -> tuple[str, str, str, str, str]:
    return (
        trade_date.isoformat(),
        trade_at,
        created_at,
        "~pending",
        settlement_date.isoformat(),
    )


def _list_transactions_as_of_trade_moment(
    portfolio_id: str,
    *,
    trade_date: date,
    trade_at: str,
    created_at: str,
    settlement_date: date,
    exclude_transaction_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    pending_sort_key = _pending_transaction_sort_key(
        trade_date=trade_date,
        trade_at=trade_at,
        created_at=created_at,
        settlement_date=settlement_date,
    )
    return [
        record
        for record in _list_transactions_as_of_trade_date(
            portfolio_id,
            trade_date,
            exclude_transaction_ids=exclude_transaction_ids,
        )
        if _transaction_sort_key(record) <= pending_sort_key
    ]


def _list_transactions_as_of_entitlement_moment(
    portfolio_id: str,
    *,
    entitlement_date: date,
    trade_date: date,
    trade_at: str,
    created_at: str,
    settlement_date: date,
    exclude_transaction_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    if entitlement_date < trade_date:
        return _list_transactions_as_of_trade_date(
            portfolio_id,
            entitlement_date,
            exclude_transaction_ids=exclude_transaction_ids,
        )
    return _list_transactions_as_of_trade_moment(
        portfolio_id,
        trade_date=trade_date,
        trade_at=trade_at,
        created_at=created_at,
        settlement_date=settlement_date,
        exclude_transaction_ids=exclude_transaction_ids,
    )


def _account_cost_methods(portfolio_id: str) -> dict[str, str]:
    return {
        str(account_item.get("account_id") or ""): str(account_item.get("cost_basis_method") or "fifo")
        for account_item in list_accounts(portfolio_id)
        if account_item.get("account_type") == "securities_account"
    }


def _transfer_group_transaction_ids(portfolio_id: str, transfer_group_id: str) -> list[str]:
    return [
        str(record.get("transaction_id") or "")
        for record in list_transactions(portfolio_id)
        if str(record.get("transfer_group_id") or "") == transfer_group_id
    ]


def _is_position_asset_type(asset_type: str) -> bool:
    return asset_type in POSITION_ASSET_TYPES


def _normalized_allowed_asset_types(account: dict[str, object] | None) -> list[str]:
    raw_values = (account or {}).get("allowed_asset_types")
    if not isinstance(raw_values, list):
        return []

    normalized: list[str] = []
    for raw_value in raw_values:
        value = str(raw_value or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _validate_account_asset_scope(
    *,
    account: dict[str, object],
    instrument_ref: dict[str, object] | None,
    transaction_type: str,
) -> None:
    if (
        instrument_ref is None
        or str(account.get("account_type") or "") != "securities_account"
        or transaction_type not in ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES
    ):
        return

    allowed_asset_types = _normalized_allowed_asset_types(account)
    if not allowed_asset_types:
        return

    asset_type = str(instrument_ref.get("asset_type") or "").strip().lower()
    if asset_type in allowed_asset_types:
        return

    account_name = str(account.get("account_name") or account.get("account_id") or "Selected account")
    raise HTTPException(
        status_code=400,
        detail=(
            f"{account_name} only accepts {', '.join(allowed_asset_types)} assets. "
            f"'{asset_type}' is out of scope for inbound positions."
        ),
    )


def _validate_instrument_transaction_compatibility(
    *,
    transaction_type: str,
    instrument_ref: dict[str, object] | None,
) -> None:
    if instrument_ref is None:
        return

    asset_type = str(instrument_ref.get("asset_type") or "").strip().lower()
    if not asset_type:
        return

    if transaction_type in {"buy", "sell", "opening_balance"}:
        if not _is_position_asset_type(asset_type):
            raise HTTPException(
                status_code=400,
                detail=f"{transaction_type.replace('_', ' ').title()} is not supported for asset type '{asset_type}'.",
            )
        return

    if transaction_type in {"fee", "tax"}:
        if not _is_position_asset_type(asset_type):
            raise HTTPException(
                status_code=400,
                detail=f"{transaction_type.title()} instrument selection is not supported for asset type '{asset_type}'.",
            )
        return

    allowed_asset_types = INCOME_ASSET_TYPES.get(transaction_type)
    if allowed_asset_types is None:
        return
    if asset_type not in allowed_asset_types:
        raise HTTPException(
            status_code=400,
            detail=f"{transaction_type.replace('_', ' ').title()} is not supported for asset type '{asset_type}'.",
        )


def _validate_transaction_currency(
    *,
    transaction_type: str,
    transaction_currency: str,
    account: dict[str, object],
    settlement_cash_account: dict[str, object] | None,
    instrument_ref: dict[str, object] | None,
) -> None:
    if transaction_type == "fx_conversion":
        expected_currency = _currency_of(account)
        if expected_currency and transaction_currency != expected_currency:
            raise HTTPException(status_code=400, detail="FX conversion source currency must match account currency.")
        return

    if instrument_ref is not None and transaction_type not in {"fee", "tax"}:
        expected_currency = _currency_of(instrument_ref)
        if expected_currency and transaction_currency != expected_currency:
            raise HTTPException(status_code=400, detail="Transaction currency must match instrument currency.")

    if settlement_cash_account is not None:
        expected_currency = _currency_of(settlement_cash_account)
        if expected_currency and transaction_currency != expected_currency:
            raise HTTPException(status_code=400, detail="Settlement cash account currency must match transaction currency.")
        return

    expected_currency = _currency_of(account)
    if expected_currency and transaction_currency != expected_currency:
        raise HTTPException(status_code=400, detail="Transaction currency must match account currency.")


def _validate_securities_account_currency_alignment(
    *,
    account: dict[str, object],
    settlement_cash_account: dict[str, object] | None,
    instrument_ref: dict[str, object] | None,
) -> None:
    if str(account.get("account_type") or "") != "securities_account":
        return

    account_currency = _currency_of(account)
    instrument_currency = _currency_of(instrument_ref)
    if account_currency and instrument_currency and account_currency != instrument_currency:
        raise HTTPException(status_code=400, detail="Securities account currency must match instrument currency.")

    settlement_currency = _currency_of(settlement_cash_account)
    if account_currency and settlement_currency and account_currency != settlement_currency:
        raise HTTPException(
            status_code=400,
            detail="Securities account currency must match settlement cash account currency.",
        )


def _validate_fx_conversion(
    *,
    portfolio_id: str,
    payload: TransactionCreateRequest,
    account: dict[str, object],
) -> dict[str, object]:
    if str(account.get("account_type") or "") != "deposit_account":
        raise HTTPException(status_code=400, detail="FX conversion requires deposit_account.")

    counterparty_account_id = str(payload.counterparty_account_id or "").strip()
    if not counterparty_account_id:
        raise HTTPException(status_code=400, detail="FX conversion requires counterparty account.")

    counterparty_account = get_account(portfolio_id, counterparty_account_id)
    if counterparty_account is None:
        raise HTTPException(status_code=400, detail="FX conversion counterparty account not found.")
    if str(counterparty_account.get("account_type") or "") != "deposit_account":
        raise HTTPException(status_code=400, detail="FX conversion target must be deposit_account.")
    if str(counterparty_account.get("account_id") or "") == str(account.get("account_id") or ""):
        raise HTTPException(status_code=400, detail="FX conversion requires distinct source and target accounts.")

    source_currency = _currency_of(account)
    target_currency = _currency_of(counterparty_account)
    if source_currency == target_currency:
        raise HTTPException(status_code=400, detail="FX conversion accounts must use different currencies.")
    if payload.currency.upper() != source_currency:
        raise HTTPException(status_code=400, detail="FX conversion transaction currency must match source account.")

    source_amount = float(payload.gross_amount or 0.0)
    target_amount = float(payload.counter_amount or 0.0)
    fx_rate = float(payload.fx_rate or 0.0)
    if source_amount <= 0:
        raise HTTPException(status_code=400, detail="FX conversion requires positive source amount.")
    implied_rate = target_amount / source_amount if source_amount > 0 else 0.0
    if abs(implied_rate - fx_rate) > 1e-4:
        raise HTTPException(status_code=400, detail="counter_amount must match gross_amount multiplied by fx_rate.")

    return counterparty_account


@router.get("/{portfolio_id}/instruments", response_model=SharedInstrumentListResponse)
def list_portfolio_instruments(portfolio_id: str) -> SharedInstrumentListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        instruments = list_registry_instruments()
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return SharedInstrumentListResponse(
        portfolio_id=portfolio_id,
        instruments=[_serialize_instrument_option(item) for item in instruments],
    )


@router.get("/{portfolio_id}/transactions", response_model=TransactionListResponse)
def list_transaction_records(
    portfolio_id: str,
    account_id: str | None = None,
    transaction_type: str | None = None,
    asset_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> TransactionListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    records = list_transactions(
        portfolio_id,
        account_id=account_id,
        transaction_type=transaction_type,
        asset_id=asset_id,
        start_date=start_date,
        end_date=end_date,
    )

    return TransactionListResponse(
        portfolio_id=portfolio_id,
        summary=_build_summary(records),
        derivation_boundary=DerivationBoundaryStatus(),
        transactions=[_serialize_transaction(portfolio_id, item, account_lookup) for item in records],
    )


@router.get("/{portfolio_id}/transactions/workspace", response_model=TransactionWorkspaceResponse)
def get_transaction_workspace(
    portfolio_id: str,
    account_id: str | None = None,
    transaction_type: str | None = None,
    asset_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    transaction_id: str | None = None,
) -> TransactionWorkspaceResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    account_lookup = {item["account_id"]: item for item in accounts}
    filtered_records = list_transactions(
        portfolio_id,
        account_id=account_id,
        transaction_type=transaction_type,
        asset_id=asset_id,
        start_date=start_date,
        end_date=end_date,
    )
    serialized_transactions = [
        _serialize_transaction(portfolio_id, item, account_lookup) for item in filtered_records
    ]
    selected_transaction = next(
        (item for item in serialized_transactions if item.transaction_id == transaction_id),
        serialized_transactions[0] if serialized_transactions else None,
    )
    selected_transaction_id = selected_transaction.transaction_id if selected_transaction else None

    all_transactions = list_transactions(portfolio_id)
    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in accounts
        if account.get("account_type") == "securities_account"
    }
    account_currency_map = {
        str(account.get("account_id") or ""): str(account.get("currency") or "")
        for account in accounts
    }
    ledger_postings_raw = (
        list_ledger_postings(
            portfolio_id,
            all_transactions,
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            transaction_id=selected_transaction_id,
        )
        if selected_transaction_id
        else []
    )

    related_position_lots_raw: list[dict[str, object]] = []
    if selected_transaction and selected_transaction.asset_id:
        candidate_position_lots = build_position_lots(
            portfolio_id,
            accounts,
            all_transactions,
            account_id=selected_transaction.account.account_id,
            asset_id=selected_transaction.asset_id,
        )
        related_position_lots_raw = [
            item
            for item in candidate_position_lots
            if item.get("opened_by_transaction_id") == selected_transaction.transaction_id
            or any(
                realization.get("transaction_id") == selected_transaction.transaction_id
                for realization in item.get("realizations", [])
            )
        ]
        if not related_position_lots_raw:
            related_position_lots_raw = candidate_position_lots

    return TransactionWorkspaceResponse(
        portfolio_id=portfolio_id,
        summary=_build_summary(filtered_records),
        derivation_boundary=DerivationBoundaryStatus(
            ledger_postings="next_layer",
            positions="next_layer",
            position_lots="next_layer",
            holdings="not_started",
            snapshot="not_started",
        ),
        selected_transaction_id=selected_transaction_id,
        transactions=serialized_transactions,
        selected_transaction=selected_transaction,
        ledger_summary=LedgerPostingListSummary.model_validate(summarize_ledger_postings(ledger_postings_raw)),
        ledger_postings=ledger_postings_raw,
        related_position_lot_summary=PositionLotListSummary.model_validate(
            summarize_position_lots(related_position_lots_raw)
        ),
        related_position_lots=related_position_lots_raw,
    )


@router.get("/{portfolio_id}/transactions/position-preview", response_model=TransactionPositionPreviewResponse)
def get_transaction_position_preview(
    portfolio_id: str,
    account_id: str,
    asset_id: str,
    as_of_date: date = Query(...),
    trade_time: str | None = None,
    exclude_transaction_id: str | None = None,
) -> TransactionPositionPreviewResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if get_account(portfolio_id, account_id) is None:
        raise HTTPException(status_code=400, detail="Account not found")

    _load_instrument_ref(asset_id)
    resolved_trade_timing = resolve_trade_timing(
        trade_date=as_of_date,
        trade_time=trade_time,
    )
    pending_created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    excluded_transaction_ids = {exclude_transaction_id} if exclude_transaction_id else None
    transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
        portfolio_id,
        trade_date=as_of_date,
        trade_at=str(resolved_trade_timing["trade_at"]),
        created_at=pending_created_at,
        settlement_date=as_of_date,
        exclude_transaction_ids=excluded_transaction_ids,
    )
    quantity = estimate_position_quantity(
        portfolio_id,
        transactions_as_of_trade_date,
        account_id=account_id,
        asset_id=asset_id,
        account_cost_methods=_account_cost_methods(portfolio_id),
    )
    return TransactionPositionPreviewResponse(
        portfolio_id=portfolio_id,
        account_id=account_id,
        asset_id=asset_id,
        as_of_date=as_of_date,
        trade_at=str(resolved_trade_timing["trade_at"]),
        quantity=quantity,
    )


def _persist_transaction_record(
    *,
    portfolio_id: str,
    payload: TransactionCreateRequest,
    existing_transaction: dict[str, object] | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> TransactionRecord:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account = get_account(portfolio_id, payload.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="Account not found")
    settlement_date = payload.settlement_date or payload.trade_date
    entitlement_date = payload.entitlement_date or payload.trade_date
    resolved_trade_timing = resolve_trade_timing(
        trade_date=payload.trade_date,
        trade_time=payload.trade_time,
    )
    pending_created_at = str(existing_transaction.get("created_at") or "").strip() if existing_transaction else ""
    if not pending_created_at:
        pending_created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    excluded_transaction_ids = (
        {str(existing_transaction.get("transaction_id") or "").strip()}
        if existing_transaction is not None
        else None
    )
    _validate_account_fact_window(
        account,
        event_dates=[payload.trade_date, settlement_date],
        role_label="Account",
    )
    account_cost_methods = _account_cost_methods(portfolio_id)

    transaction_type = payload.transaction_type
    account_type = str(account.get("account_type") or "")
    transfer_object_type = payload.transfer_object_type
    if transaction_type in {"transfer_in", "transfer_out"}:
        raise HTTPException(
            status_code=400,
            detail="Use /transactions/internal-transfer for paired internal transfer facts.",
        )
    if transaction_type in {"deposit", "withdrawal"} and account_type != "deposit_account":
        raise HTTPException(status_code=400, detail="Cash-flow transactions require deposit_account.")
    if transaction_type == "interest" and account_type != "deposit_account":
        raise HTTPException(status_code=400, detail="Interest transactions require deposit_account.")
    if transaction_type == "fx_conversion" and account_type != "deposit_account":
        raise HTTPException(status_code=400, detail="FX conversion requires deposit_account.")
    if transaction_type in {
        "buy",
        "sell",
        "dividend",
        "dividend_reinvestment",
        "coupon",
        "return_of_capital",
        "maturity_redemption",
    } and account_type != "securities_account":
        raise HTTPException(status_code=400, detail="Instrument income and trade transactions require securities_account.")
    if transaction_type in {"transfer_in", "transfer_out"}:
        if transfer_object_type == "cash" and account_type != "deposit_account":
            raise HTTPException(status_code=400, detail="Cash transfer requires deposit_account.")
        if transfer_object_type == "position" and account_type != "securities_account":
            raise HTTPException(status_code=400, detail="Position transfer requires securities_account.")

    settlement_cash_account = None
    settlement_cash_account_id = payload.settlement_cash_account_id
    fx_conversion_target_account = None
    if transaction_type == "fx_conversion":
        settlement_cash_account_id = None
        fx_conversion_target_account = _validate_fx_conversion(
            portfolio_id=portfolio_id,
            payload=payload,
            account=account,
        )
    if transaction_type in {"buy", "sell", "dividend", "coupon", "return_of_capital", "maturity_redemption"} or (
        transaction_type in {"fee", "tax"} and account_type == "securities_account"
    ):
        settlement_cash_account_id = settlement_cash_account_id or str(
            account.get("default_settlement_cash_account_id") or ""
        )
        if not settlement_cash_account_id:
            raise HTTPException(status_code=400, detail="This transaction requires settlement cash account.")
        settlement_cash_account = get_account(portfolio_id, settlement_cash_account_id)
        if settlement_cash_account is None:
            raise HTTPException(status_code=400, detail="Settlement cash account not found.")
        if settlement_cash_account.get("account_type") != "deposit_account":
            raise HTTPException(status_code=400, detail="Settlement cash account must be deposit_account.")

    if transaction_type == "opening_balance" and settlement_cash_account_id:
        settlement_cash_account = get_account(portfolio_id, settlement_cash_account_id)

    if settlement_cash_account is not None:
        _validate_account_fact_window(
            settlement_cash_account,
            event_dates=[settlement_date],
            role_label="Settlement cash account",
        )
    if fx_conversion_target_account is not None:
        _validate_account_fact_window(
            fx_conversion_target_account,
            event_dates=[settlement_date],
            role_label="FX conversion target account",
        )

    instrument_ref = None
    asset_id = payload.asset_id
    if asset_id:
        instrument_ref = _load_instrument_ref(asset_id)

    if transaction_type in {
        "buy",
        "sell",
        "dividend",
        "dividend_reinvestment",
        "coupon",
        "return_of_capital",
        "maturity_redemption",
    } and instrument_ref is None:
        raise HTTPException(status_code=400, detail="This transaction type requires instrument.")

    if transaction_type in {"deposit", "withdrawal"} and asset_id is not None:
        raise HTTPException(status_code=400, detail="Cash-flow transactions must not reference instrument.")

    if transaction_type == "fx_conversion" and asset_id is not None:
        raise HTTPException(status_code=400, detail="FX conversion must not reference instrument.")

    if transaction_type == "interest" and asset_id is not None:
        raise HTTPException(status_code=400, detail="Interest transaction must not reference instrument.")

    if transaction_type == "opening_balance" and account_type == "deposit_account" and asset_id is not None:
        raise HTTPException(status_code=400, detail="Cash opening balance must not reference instrument.")

    if transaction_type == "opening_balance" and account_type == "securities_account" and instrument_ref is None:
        raise HTTPException(status_code=400, detail="Security opening balance requires instrument.")

    if transaction_type in {"fee", "tax"} and account_type == "deposit_account" and asset_id is not None:
        raise HTTPException(status_code=400, detail="Deposit-account fee and tax must not reference instrument.")
    if transaction_type in {"fee", "tax"} and account_type == "deposit_account" and settlement_cash_account_id is not None:
        raise HTTPException(status_code=400, detail="Deposit-account fee and tax must not carry settlement cash account.")

    _validate_instrument_transaction_compatibility(
        transaction_type=transaction_type,
        instrument_ref=instrument_ref,
    )
    _validate_account_asset_scope(
        account=account,
        instrument_ref=instrument_ref,
        transaction_type=transaction_type,
    )

    transactions_as_of_trade_date: list[dict[str, object]] | None = None
    if transaction_type in {"sell", "maturity_redemption", "dividend_reinvestment"} and asset_id:
        transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
            portfolio_id,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=pending_created_at,
            settlement_date=settlement_date,
            exclude_transaction_ids=excluded_transaction_ids,
        )

    transactions_as_of_entitlement_date: list[dict[str, object]] | None = None
    if transaction_type in {"dividend", "coupon"} or (transaction_type in {"fee", "tax"} and asset_id):
        if asset_id:
            transactions_as_of_entitlement_date = _list_transactions_as_of_entitlement_moment(
                portfolio_id,
                entitlement_date=entitlement_date,
                trade_date=payload.trade_date,
                trade_at=str(resolved_trade_timing["trade_at"]),
                created_at=pending_created_at,
                settlement_date=settlement_date,
                exclude_transaction_ids=excluded_transaction_ids,
            )

    if transaction_type in {"sell", "maturity_redemption"} and asset_id:
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_trade_date or [],
            account_id=payload.account_id,
            asset_id=asset_id,
            account_cost_methods=account_cost_methods,
        )
        requested_quantity = float(payload.quantity or 0.0)
        if requested_quantity > available_quantity + 1e-9:
            raise HTTPException(status_code=400, detail="Transaction quantity exceeds account position as of trade_date.")

    if transaction_type in {"dividend", "coupon"} or (transaction_type in {"fee", "tax"} and asset_id):
        if asset_id:
            available_quantity = estimate_position_quantity(
                portfolio_id,
                transactions_as_of_entitlement_date or [],
                account_id=payload.account_id,
                asset_id=asset_id,
                account_cost_methods=account_cost_methods,
            )
            if available_quantity <= 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail="Instrument-linked income and expense requires account position as of entitlement_date.",
                )

    if transaction_type == "dividend_reinvestment" and asset_id:
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_trade_date or [],
            account_id=payload.account_id,
            asset_id=asset_id,
            account_cost_methods=account_cost_methods,
        )
        if available_quantity <= 1e-9:
            raise HTTPException(
                status_code=400,
                detail="Dividend reinvestment requires account position as of trade_date.",
            )

    if transaction_type == "return_of_capital" and asset_id:
        transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
            portfolio_id,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=pending_created_at,
            settlement_date=settlement_date,
            exclude_transaction_ids=excluded_transaction_ids,
        )
        available_cost_basis = estimate_position_remaining_cost_basis(
            portfolio_id,
            transactions_as_of_trade_date or [],
            account_id=payload.account_id,
            asset_id=asset_id,
            account_cost_methods=account_cost_methods,
        )
        if float(payload.gross_amount or 0.0) > available_cost_basis + 1e-9:
            raise HTTPException(
                status_code=400,
                detail="Return of capital exceeds account position cost basis as of trade_date.",
            )

    _validate_transaction_currency(
        transaction_type=transaction_type,
        transaction_currency=payload.currency.upper(),
        account=account,
        settlement_cash_account=settlement_cash_account,
        instrument_ref=instrument_ref,
    )
    _validate_securities_account_currency_alignment(
        account=account,
        settlement_cash_account=settlement_cash_account,
        instrument_ref=instrument_ref,
    )

    persisted_record = (
        update_transaction(
            portfolio_id,
            str(existing_transaction.get("transaction_id") or ""),
            transaction_type=transaction_type,
            trade_date=payload.trade_date,
            trade_time=payload.trade_time,
            settlement_date=settlement_date,
            entitlement_date=payload.entitlement_date,
            acquisition_date=payload.acquisition_date,
            account_id=payload.account_id,
            settlement_cash_account_id=settlement_cash_account_id or None,
            asset_id=asset_id,
            instrument_ref=instrument_ref,
            quantity=payload.quantity,
            price=payload.price,
            gross_amount=payload.gross_amount,
            counter_amount=payload.counter_amount,
            fx_rate=payload.fx_rate,
            fees=payload.fees,
            taxes=payload.taxes,
            currency=payload.currency,
            transfer_scope=payload.transfer_scope,
            transfer_object_type=payload.transfer_object_type,
            transfer_group_id=payload.transfer_group_id,
            counterparty_account_id=(
                str(fx_conversion_target_account.get("account_id") or "")
                if fx_conversion_target_account is not None
                else payload.counterparty_account_id
            ),
            note=payload.note,
            created_at=pending_created_at,
        )
        if existing_transaction is not None
        else create_transaction(
            portfolio_id=portfolio_id,
            transaction_type=transaction_type,
            trade_date=payload.trade_date,
            trade_time=payload.trade_time,
            settlement_date=settlement_date,
            entitlement_date=payload.entitlement_date,
            acquisition_date=payload.acquisition_date,
            account_id=payload.account_id,
            settlement_cash_account_id=settlement_cash_account_id or None,
            asset_id=asset_id,
            instrument_ref=instrument_ref,
            quantity=payload.quantity,
            price=payload.price,
            gross_amount=payload.gross_amount,
            counter_amount=payload.counter_amount,
            fx_rate=payload.fx_rate,
            fees=payload.fees,
            taxes=payload.taxes,
            currency=payload.currency,
            transfer_scope=payload.transfer_scope,
            transfer_object_type=payload.transfer_object_type,
            transfer_group_id=payload.transfer_group_id,
            counterparty_account_id=(
                str(fx_conversion_target_account.get("account_id") or "")
                if fx_conversion_target_account is not None
                else payload.counterparty_account_id
            ),
            note=payload.note,
            created_at=pending_created_at,
        )
    )
    if persisted_record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _queue_daily_snapshot_refresh(background_tasks, portfolio_id)
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return _serialize_transaction(portfolio_id, persisted_record, account_lookup)


@router.post("/{portfolio_id}/transactions", response_model=TransactionRecord)
def create_transaction_record(
    portfolio_id: str,
    payload: TransactionCreateRequest,
    background_tasks: BackgroundTasks,
) -> TransactionRecord:
    return _persist_transaction_record(
        portfolio_id=portfolio_id,
        payload=payload,
        background_tasks=background_tasks,
    )


@router.put("/{portfolio_id}/transactions/{transaction_id}", response_model=TransactionRecord)
def update_transaction_record(
    portfolio_id: str,
    transaction_id: str,
    payload: TransactionCreateRequest,
    background_tasks: BackgroundTasks,
) -> TransactionRecord:
    existing_transaction = get_transaction(portfolio_id, transaction_id)
    if existing_transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if existing_transaction.get("transfer_group_id"):
        raise HTTPException(
            status_code=400,
            detail="Paired internal transfer facts must be deleted and recreated as a batch.",
        )
    if str(existing_transaction.get("transaction_type") or "") in {"transfer_in", "transfer_out"}:
        raise HTTPException(
            status_code=400,
            detail="Paired internal transfer facts must be deleted and recreated as a batch.",
        )
    return _persist_transaction_record(
        portfolio_id=portfolio_id,
        payload=payload,
        existing_transaction=existing_transaction,
        background_tasks=background_tasks,
    )


@router.delete("/{portfolio_id}/transactions/{transaction_id}", response_model=TransactionDeleteResponse)
def delete_transaction_record(
    portfolio_id: str,
    transaction_id: str,
    background_tasks: BackgroundTasks,
) -> TransactionDeleteResponse:
    existing_transaction = get_transaction(portfolio_id, transaction_id)
    if existing_transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    transfer_group_id = str(existing_transaction.get("transfer_group_id") or "").strip() or None
    transaction_ids = (
        _transfer_group_transaction_ids(portfolio_id, transfer_group_id)
        if transfer_group_id
        else [transaction_id]
    )
    deleted_records = delete_transactions(
        portfolio_id,
        transaction_ids=transaction_ids,
    )
    _queue_daily_snapshot_refresh(background_tasks, portfolio_id)
    return TransactionDeleteResponse(
        portfolio_id=portfolio_id,
        deleted_count=len(deleted_records),
        deleted_transaction_ids=[
            str(record.get("transaction_id") or "")
            for record in deleted_records
        ],
        transfer_group_id=transfer_group_id,
    )


@router.post("/{portfolio_id}/transactions/internal-transfer", response_model=TransactionBatchResponse)
def create_internal_transfer_records(
    portfolio_id: str,
    payload: InternalTransferCreateRequest,
    background_tasks: BackgroundTasks,
) -> TransactionBatchResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    from_account = get_account(portfolio_id, payload.from_account_id)
    to_account = get_account(portfolio_id, payload.to_account_id)
    if from_account is None or to_account is None:
        raise HTTPException(status_code=400, detail="Transfer accounts not found.")
    settlement_date = payload.settlement_date or payload.trade_date
    resolved_trade_timing = resolve_trade_timing(
        trade_date=payload.trade_date,
        trade_time=payload.trade_time,
    )
    pending_created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _validate_account_fact_window(
        from_account,
        event_dates=[payload.trade_date, settlement_date],
        role_label="Source account",
    )
    _validate_account_fact_window(
        to_account,
        event_dates=[payload.trade_date, settlement_date],
        role_label="Destination account",
    )

    transfer_object_type = payload.transfer_object_type
    if transfer_object_type == "cash":
        if from_account.get("account_type") != "deposit_account" or to_account.get("account_type") != "deposit_account":
            raise HTTPException(status_code=400, detail="Cash transfer requires deposit accounts on both legs.")
        transfer_currency = str(from_account.get("currency") or "").upper()
        if transfer_currency != str(to_account.get("currency") or "").upper():
            raise HTTPException(status_code=400, detail="Cash transfer accounts must share the same currency.")
        asset_id = None
        instrument_ref = None
    else:
        if from_account.get("account_type") != "securities_account" or to_account.get("account_type") != "securities_account":
            raise HTTPException(status_code=400, detail="Position transfer requires securities accounts on both legs.")
        asset_id = payload.asset_id
        if not asset_id:
            raise HTTPException(status_code=400, detail="Position transfer requires instrument.")
        instrument_ref = _load_instrument_ref(asset_id)
        _validate_instrument_transaction_compatibility(
            transaction_type="opening_balance",
            instrument_ref=instrument_ref,
        )
        _validate_account_asset_scope(
            account=to_account,
            instrument_ref=instrument_ref,
            transaction_type="opening_balance",
        )
        transfer_currency = str(instrument_ref.get("currency") or "").upper()
        if _currency_of(from_account) != transfer_currency or _currency_of(to_account) != transfer_currency:
            raise HTTPException(
                status_code=400,
                detail="Position transfer accounts must share the same currency as the instrument.",
            )
        account_cost_methods = {
            str(account_item.get("account_id") or ""): str(account_item.get("cost_basis_method") or "fifo")
            for account_item in list_accounts(portfolio_id)
            if account_item.get("account_type") == "securities_account"
        }
        transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
            portfolio_id,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=pending_created_at,
            settlement_date=settlement_date,
        )
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_trade_date,
            account_id=payload.from_account_id,
            asset_id=asset_id,
            account_cost_methods=account_cost_methods,
        )
        requested_quantity = float(payload.quantity or 0.0)
        if requested_quantity > available_quantity + 1e-9:
            raise HTTPException(status_code=400, detail="Transfer quantity exceeds source position as of trade_date.")

    transferred_amount = payload.gross_amount
    if transfer_object_type == "position":
        estimated_transferred_amount = estimate_position_cost_basis(
            portfolio_id,
            transactions_as_of_trade_date,
            account_id=payload.from_account_id,
            asset_id=asset_id or "",
            quantity=float(payload.quantity or 0.0),
            account_cost_methods=account_cost_methods,
        )
        if estimated_transferred_amount < -1e-9:
            raise HTTPException(
                status_code=400,
                detail="Unable to derive transferred cost basis from current source position.",
            )
        if transferred_amount is not None and transferred_amount > 0:
            if abs(float(transferred_amount) - estimated_transferred_amount) > 1e-6:
                raise HTTPException(
                    status_code=400,
                    detail="Position transfer gross_amount must match source cost basis as of trade_date.",
                )
        transferred_amount = estimated_transferred_amount

    transfer_group_id = payload.transfer_group_id or f"trf-{uuid4().hex[:12]}"
    trade_date = payload.trade_date

    created_records = create_transactions(
        portfolio_id=portfolio_id,
        records=[
            {
                "transaction_type": "transfer_out",
                "trade_date": trade_date,
                "trade_time": payload.trade_time,
                "settlement_date": settlement_date,
                "entitlement_date": None,
                "acquisition_date": None,
                "account_id": payload.from_account_id,
                "settlement_cash_account_id": None,
                "asset_id": asset_id,
                "instrument_ref": instrument_ref,
                "quantity": payload.quantity,
                "price": None,
                "gross_amount": float(transferred_amount or 0.0),
                "counter_amount": None,
                "fx_rate": None,
                "fees": 0,
                "taxes": 0,
                "currency": transfer_currency,
                "transfer_scope": "internal_portfolio",
                "transfer_object_type": transfer_object_type,
                "transfer_group_id": transfer_group_id,
                "counterparty_account_id": payload.to_account_id,
                "note": payload.note,
                "created_at": pending_created_at,
            },
            {
                "transaction_type": "transfer_in",
                "trade_date": trade_date,
                "trade_time": payload.trade_time,
                "settlement_date": settlement_date,
                "entitlement_date": None,
                "acquisition_date": None,
                "account_id": payload.to_account_id,
                "settlement_cash_account_id": None,
                "asset_id": asset_id,
                "instrument_ref": instrument_ref,
                "quantity": payload.quantity,
                "price": None,
                "gross_amount": float(transferred_amount or 0.0),
                "counter_amount": None,
                "fx_rate": None,
                "fees": 0,
                "taxes": 0,
                "currency": transfer_currency,
                "transfer_scope": "internal_portfolio",
                "transfer_object_type": transfer_object_type,
                "transfer_group_id": transfer_group_id,
                "counterparty_account_id": payload.from_account_id,
                "note": payload.note,
                "created_at": pending_created_at,
            },
        ],
    )
    _queue_daily_snapshot_refresh(background_tasks, portfolio_id)
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return TransactionBatchResponse(
        portfolio_id=portfolio_id,
        created_count=len(created_records),
        transfer_group_id=transfer_group_id,
        transactions=[_serialize_transaction(portfolio_id, item, account_lookup) for item in created_records],
    )
