from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.assemblers import (
    resolve_transaction_flow_scope,
    resolve_transaction_net_cash_effect,
    serialize_transaction,
    summarize_transactions,
)
from portfolio_app.api.transaction_command_errors import (
    raise_transaction_command_validation_error,
)
from portfolio_app.api.contracts import (
    InternalTransferCreateRequest,
    SharedInstrumentListResponse,
    TransactionBatchResponse,
    TransactionCreateRequest,
    TransactionDeleteRequest,
    TransactionDeleteResponse,
    TransactionExecutionQuoteResponse,
    TransactionListResponse,
    TransactionListSummary,
    TransactionRecord,
    TransactionRevisionHistoryResponse,
    TransactionRevisionRecord,
    TransactionUpdateRequest,
    TransactionWorkspaceResponse,
)
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_registry_instrument,
    list_registry_instruments,
)
from portfolio_app.services.execution_quotes import get_execution_quote
from portfolio_app.services.portfolio_store import (
    create_transaction,
    create_transactions,
    delete_transactions,
    get_account,
    get_portfolio,
    get_transaction,
    get_transaction_revision_history,
    list_accounts,
    list_transactions,
    update_transaction,
)
from portfolio_app.services.transaction_revisions import (
    TransactionRevisionConflictError,
    TransactionRevisionNoOpError,
    TransactionRevisionPayloadError,
)
from portfolio_app.services.transaction_command_validator import (
    TransactionCommandValidationError,
)


router = APIRouter()

POSITION_ASSET_TYPES = {"fund", "etf", "bond", "equity", "other"}
ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES = {"buy", "dividend_reinvestment", "opening_balance"}
INCOME_ASSET_TYPES: dict[str, set[str]] = {
    "dividend": {"fund", "etf", "equity"},
    "dividend_reinvestment": {"fund", "etf", "equity"},
    "coupon": {"bond"},
    "return_of_capital": {"fund", "etf", "equity"},
    "maturity_redemption": {"bond"},
}


def _resolve_flow_scope(transaction_type: str) -> str:
    return resolve_transaction_flow_scope(transaction_type)


def _resolve_net_cash_effect(record: dict[str, object]) -> Decimal | None:
    return resolve_transaction_net_cash_effect(record)


def _raise_transaction_revision_error(error: Exception, *, portfolio_id: str) -> NoReturn:
    if isinstance(error, TransactionRevisionConflictError):
        lifecycle_status = "active"
        if error.transaction_id:
            history = get_transaction_revision_history(
                portfolio_id,
                error.transaction_id,
            )
            if history is not None:
                lifecycle_status = str(history.get("lifecycle_status") or "active")
        raise HTTPException(
            status_code=409,
            detail={
                "code": "transaction_revision_conflict",
                "message": str(error),
                "reason_code": error.code,
                "transaction_id": error.transaction_id,
                "expected_revision_id": error.expected_revision_id,
                "expected_revision_number": error.expected_revision_number,
                "actual_revision_id": error.actual_revision_id,
                "actual_revision_number": error.actual_revision_number,
                "lifecycle_status": lifecycle_status,
            },
        ) from error
    if isinstance(error, TransactionRevisionNoOpError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "transaction_revision_no_op",
                "message": str(error),
                "transaction_id": error.transaction_id,
                "revision_id": error.revision_id,
                "revision_number": error.revision_number,
            },
        ) from error
    if isinstance(error, TransactionRevisionPayloadError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "transaction_revision_invalid",
                "message": str(error),
            },
        ) from error
    raise error


def _serialize_transaction(
    portfolio_id: str,
    record: dict[str, object],
    account_lookup: dict[str, dict[str, object]],
) -> TransactionRecord:
    return serialize_transaction(portfolio_id, record, account_lookup)


def _build_summary(records: list[dict[str, object]]) -> TransactionListSummary:
    return summarize_transactions(records)


def _load_instrument_ref(instrument_id: str) -> dict[str, object]:
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
        "identifiers": instrument.get("identifiers", []),
    }


def _serialize_instrument_option(record: dict[str, object]) -> dict[str, object]:
    coverage_state = str(record.get("coverage_state") or "").strip() or None
    if coverage_state is None:
        coverage_state = "complete" if record.get("latest_market_data") else "unavailable"
    return {
        "instrument_core": {
            "instrument_id": record["instrument_id"],
            "instrument_name": record["instrument_name"],
            "instrument_type": record["instrument_type"],
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


def _transfer_group_transaction_ids(portfolio_id: str, transfer_group_id: str) -> list[str]:
    return [
        str(record.get("transaction_id") or "")
        for record in list_transactions(portfolio_id)
        if str(record.get("transfer_group_id") or "") == transfer_group_id
    ]


def _is_position_instrument_type(instrument_type: str) -> bool:
    return instrument_type in POSITION_ASSET_TYPES


def _normalized_allowed_instrument_types(account: dict[str, object] | None) -> list[str]:
    raw_values = (account or {}).get("allowed_instrument_types")
    if not isinstance(raw_values, list):
        return []

    normalized: list[str] = []
    for raw_value in raw_values:
        value = str(raw_value or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _validate_account_instrument_scope(
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

    allowed_instrument_types = _normalized_allowed_instrument_types(account)
    if not allowed_instrument_types:
        return

    instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
    if instrument_type in allowed_instrument_types:
        return

    account_name = str(account.get("account_name") or account.get("account_id") or "Selected account")
    raise HTTPException(
        status_code=400,
        detail=(
            f"{account_name} only accepts {', '.join(allowed_instrument_types)} instruments. "
            f"'{instrument_type}' is out of scope for inbound positions."
        ),
    )


def _validate_instrument_transaction_compatibility(
    *,
    transaction_type: str,
    instrument_ref: dict[str, object] | None,
) -> None:
    if instrument_ref is None:
        return

    instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
    if not instrument_type:
        return

    if transaction_type in {"buy", "sell", "opening_balance"}:
        if not _is_position_instrument_type(instrument_type):
            raise HTTPException(
                status_code=400,
                detail=f"{transaction_type.replace('_', ' ').title()} is not supported for instrument type '{instrument_type}'.",
            )
        return

    if transaction_type in {"fee", "tax"}:
        if not _is_position_instrument_type(instrument_type):
            raise HTTPException(
                status_code=400,
                detail=f"{transaction_type.title()} instrument selection is not supported for instrument type '{instrument_type}'.",
            )
        return

    allowed_instrument_types = INCOME_ASSET_TYPES.get(transaction_type)
    if allowed_instrument_types is None:
        return
    if instrument_type not in allowed_instrument_types:
        raise HTTPException(
            status_code=400,
            detail=f"{transaction_type.replace('_', ' ').title()} is not supported for instrument type '{instrument_type}'.",
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
    instrument_id: str | None = None,
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
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
    )

    return TransactionListResponse(
        portfolio_id=portfolio_id,
        summary=_build_summary(records),
        transactions=[_serialize_transaction(portfolio_id, item, account_lookup) for item in records],
    )


@router.get("/{portfolio_id}/transactions/workspace", response_model=TransactionWorkspaceResponse)
def get_transaction_workspace(
    portfolio_id: str,
    account_id: str | None = None,
    transaction_type: str | None = None,
    instrument_id: str | None = None,
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
        instrument_id=instrument_id,
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

    return TransactionWorkspaceResponse(
        portfolio_id=portfolio_id,
        summary=_build_summary(filtered_records),
        selected_transaction_id=selected_transaction_id,
        transactions=serialized_transactions,
        selected_transaction=selected_transaction,
    )


@router.get(
    "/{portfolio_id}/transactions/execution-quote",
    response_model=TransactionExecutionQuoteResponse,
)
def get_transaction_execution_quote(
    portfolio_id: str,
    instrument_id: str,
    as_of_date: date = Query(...),
) -> TransactionExecutionQuoteResponse:
    """Resolve a canonical same-date trading reference for transaction entry.

    The shared trading role is authoritative and uses exact-only freshness, so
    a prior observation is reported with lineage but never proposed as price.
    """

    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        quote = get_execution_quote(
            instrument_id,
            as_of_date=as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if quote is None:
        raise HTTPException(status_code=404, detail="Instrument not found in shared registry.")

    return TransactionExecutionQuoteResponse.model_validate(
        {
            "portfolio_id": portfolio_id,
            **quote,
        }
    )


def _persist_transaction_record(
    *,
    portfolio_id: str,
    payload: TransactionCreateRequest | TransactionUpdateRequest,
    existing_transaction: dict[str, object] | None = None,
) -> TransactionRecord:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account = get_account(portfolio_id, payload.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="Account not found")
    settlement_date = payload.settlement_date
    if settlement_date is None:
        raise RuntimeError("Validated transaction request is missing settlement_date.")
    pending_created_at = str(existing_transaction.get("created_at") or "").strip() if existing_transaction else ""
    if not pending_created_at:
        pending_created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _validate_account_fact_window(
        account,
        event_dates=[payload.trade_date, settlement_date],
        role_label="Account",
    )
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
    instrument_id = payload.instrument_id
    if instrument_id:
        instrument_ref = _load_instrument_ref(instrument_id)

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

    if transaction_type in {"deposit", "withdrawal"} and instrument_id is not None:
        raise HTTPException(status_code=400, detail="Cash-flow transactions must not reference instrument.")

    if transaction_type == "fx_conversion" and instrument_id is not None:
        raise HTTPException(status_code=400, detail="FX conversion must not reference instrument.")

    if transaction_type == "interest" and instrument_id is not None:
        raise HTTPException(status_code=400, detail="Interest transaction must not reference instrument.")

    if transaction_type == "opening_balance" and account_type == "deposit_account" and instrument_id is not None:
        raise HTTPException(status_code=400, detail="Cash opening balance must not reference instrument.")

    if transaction_type == "opening_balance" and account_type == "securities_account" and instrument_ref is None:
        raise HTTPException(status_code=400, detail="Instrument opening balance requires instrument.")

    if transaction_type in {"fee", "tax"} and account_type == "deposit_account" and instrument_id is not None:
        raise HTTPException(status_code=400, detail="Deposit-account fee and tax must not reference instrument.")
    if transaction_type in {"fee", "tax"} and account_type == "deposit_account" and settlement_cash_account_id is not None:
        raise HTTPException(status_code=400, detail="Deposit-account fee and tax must not carry settlement cash account.")

    _validate_instrument_transaction_compatibility(
        transaction_type=transaction_type,
        instrument_ref=instrument_ref,
    )
    _validate_account_instrument_scope(
        account=account,
        instrument_ref=instrument_ref,
        transaction_type=transaction_type,
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

    transaction_values = {
        "transaction_type": transaction_type,
        "trade_date": payload.trade_date,
        "trade_time": payload.trade_time,
        "settlement_date": settlement_date,
        "entitlement_date": payload.entitlement_date,
        "acquisition_date": payload.acquisition_date,
        "account_id": payload.account_id,
        "settlement_cash_account_id": settlement_cash_account_id or None,
        "instrument_id": instrument_id,
        "instrument_ref": instrument_ref,
        "quantity": payload.quantity,
        "price": payload.price,
        "gross_amount": payload.gross_amount,
        "counter_amount": payload.counter_amount,
        "quoted_fx_rate": payload.quoted_fx_rate,
        "consideration_basis": payload.consideration_basis,
        "fees": payload.fees,
        "taxes": payload.taxes,
        "currency": payload.currency,
        "transfer_scope": payload.transfer_scope,
        "transfer_object_type": payload.transfer_object_type,
        "transfer_group_id": payload.transfer_group_id,
        "counterparty_account_id": (
            str(fx_conversion_target_account.get("account_id") or "")
            if fx_conversion_target_account is not None
            else payload.counterparty_account_id
        ),
        "note": payload.note,
    }
    try:
        if existing_transaction is not None:
            if not isinstance(payload, TransactionUpdateRequest):
                raise RuntimeError("Transaction update requires revision metadata.")
            persisted_record = update_transaction(
                portfolio_id,
                str(existing_transaction.get("transaction_id") or ""),
                expected_revision_id=payload.expected_revision_id,
                expected_revision_number=payload.expected_revision_number,
                actor=payload.actor.model_dump(),
                change_reason=payload.change_reason,
                **transaction_values,
            )
        else:
            persisted_record = create_transaction(
                portfolio_id=portfolio_id,
                actor=payload.actor.model_dump(),
                change_reason=payload.change_reason,
                created_at=pending_created_at,
                **transaction_values,
            )
    except (
        TransactionRevisionConflictError,
        TransactionRevisionNoOpError,
        TransactionRevisionPayloadError,
    ) as error:
        _raise_transaction_revision_error(error, portfolio_id=portfolio_id)
    except TransactionCommandValidationError as error:
        raise_transaction_command_validation_error(error)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction history changed; reload and retry. {error}",
        ) from error
    if persisted_record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return _serialize_transaction(portfolio_id, persisted_record, account_lookup)


@router.post("/{portfolio_id}/transactions", response_model=TransactionRecord)
def create_transaction_record(
    portfolio_id: str,
    payload: TransactionCreateRequest,
) -> TransactionRecord:
    return _persist_transaction_record(
        portfolio_id=portfolio_id,
        payload=payload,
    )


@router.put("/{portfolio_id}/transactions/{transaction_id}", response_model=TransactionRecord)
def update_transaction_record(
    portfolio_id: str,
    transaction_id: str,
    payload: TransactionUpdateRequest,
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
    )


@router.get(
    "/{portfolio_id}/transactions/{transaction_id}/revisions",
    response_model=TransactionRevisionHistoryResponse,
)
def get_transaction_revision_records(
    portfolio_id: str,
    transaction_id: str,
) -> TransactionRevisionHistoryResponse:
    history = get_transaction_revision_history(portfolio_id, transaction_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Transaction history not found")
    return TransactionRevisionHistoryResponse.model_validate(history)


@router.delete("/{portfolio_id}/transactions/{transaction_id}", response_model=TransactionDeleteResponse)
def delete_transaction_record(
    portfolio_id: str,
    transaction_id: str,
    payload: TransactionDeleteRequest,
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
    current_records = {
        str(record.get("transaction_id") or ""): record
        for record in list_transactions(portfolio_id)
        if str(record.get("transaction_id") or "") in set(transaction_ids)
    }
    expected_revisions = {
        current_transaction_id: (
            str(record.get("revision_id") or ""),
            int(record.get("revision_number") or 0),
        )
        for current_transaction_id, record in current_records.items()
    }
    expected_revisions[transaction_id] = (
        payload.expected_revision_id,
        payload.expected_revision_number,
    )
    try:
        deleted_records = delete_transactions(
            portfolio_id,
            transaction_ids=transaction_ids,
            expected_revisions=expected_revisions,
            actor=payload.actor.model_dump(),
            change_reason=payload.change_reason,
        )
    except (
        TransactionRevisionConflictError,
        TransactionRevisionNoOpError,
        TransactionRevisionPayloadError,
    ) as error:
        _raise_transaction_revision_error(error, portfolio_id=portfolio_id)
    except TransactionCommandValidationError as error:
        raise_transaction_command_validation_error(error)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Deleting this fact would invalidate later position history. {error}",
        ) from error
    revision_records: list[TransactionRevisionRecord] = []
    for deleted_transaction_id in transaction_ids:
        history = get_transaction_revision_history(portfolio_id, deleted_transaction_id)
        if history is None or not history.get("revisions"):
            raise RuntimeError(
                f"Deleted transaction '{deleted_transaction_id}' has no revision history."
            )
        revisions = history["revisions"]
        if not isinstance(revisions, list) or not isinstance(revisions[-1], dict):
            raise RuntimeError(
                f"Deleted transaction '{deleted_transaction_id}' has invalid revision history."
            )
        revision_records.append(TransactionRevisionRecord.model_validate(revisions[-1]))
    mutation_id = str(deleted_records[0].get("last_mutation_id") or "") if deleted_records else ""
    return TransactionDeleteResponse(
        portfolio_id=portfolio_id,
        mutation_id=mutation_id,
        deleted_count=len(deleted_records),
        deleted_transaction_ids=[
            str(record.get("transaction_id") or "")
            for record in deleted_records
        ],
        transfer_group_id=transfer_group_id,
        revisions=revision_records,
    )


@router.post("/{portfolio_id}/transactions/internal-transfer", response_model=TransactionBatchResponse)
def create_internal_transfer_records(
    portfolio_id: str,
    payload: InternalTransferCreateRequest,
) -> TransactionBatchResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    from_account = get_account(portfolio_id, payload.from_account_id)
    to_account = get_account(portfolio_id, payload.to_account_id)
    if from_account is None or to_account is None:
        raise HTTPException(status_code=400, detail="Transfer accounts not found.")
    settlement_date = payload.settlement_date or payload.trade_date
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
        instrument_id = None
        instrument_ref = None
    else:
        if from_account.get("account_type") != "securities_account" or to_account.get("account_type") != "securities_account":
            raise HTTPException(status_code=400, detail="Position transfer requires securities accounts on both legs.")
        instrument_id = payload.instrument_id
        if not instrument_id:
            raise HTTPException(status_code=400, detail="Position transfer requires instrument.")
        instrument_ref = _load_instrument_ref(instrument_id)
        _validate_instrument_transaction_compatibility(
            transaction_type="opening_balance",
            instrument_ref=instrument_ref,
        )
        _validate_account_instrument_scope(
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
    transferred_amount = payload.gross_amount
    if transferred_amount is None:
        raise RuntimeError("Validated internal transfer is missing gross_amount.")

    transfer_group_id = payload.transfer_group_id or f"trf-{uuid4().hex[:12]}"
    trade_date = payload.trade_date

    try:
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
                    "instrument_id": instrument_id,
                    "instrument_ref": instrument_ref,
                    "quantity": payload.quantity,
                    "price": None,
                    "gross_amount": transferred_amount,
                    "counter_amount": None,
                    "quoted_fx_rate": None,
                    "consideration_basis": None,
                    "fees": Decimal("0"),
                    "taxes": Decimal("0"),
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
                    "instrument_id": instrument_id,
                    "instrument_ref": instrument_ref,
                    "quantity": payload.quantity,
                    "price": None,
                    "gross_amount": transferred_amount,
                    "counter_amount": None,
                    "quoted_fx_rate": None,
                    "consideration_basis": None,
                    "fees": Decimal("0"),
                    "taxes": Decimal("0"),
                    "currency": transfer_currency,
                    "transfer_scope": "internal_portfolio",
                    "transfer_object_type": transfer_object_type,
                    "transfer_group_id": transfer_group_id,
                    "counterparty_account_id": payload.from_account_id,
                    "note": payload.note,
                    "created_at": pending_created_at,
                },
            ],
            actor=payload.actor.model_dump(),
            change_reason=payload.change_reason or "Internal transfer recorded",
        )
    except (
        TransactionRevisionConflictError,
        TransactionRevisionNoOpError,
        TransactionRevisionPayloadError,
    ) as error:
        _raise_transaction_revision_error(error, portfolio_id=portfolio_id)
    except TransactionCommandValidationError as error:
        raise_transaction_command_validation_error(error)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction history changed; reload and retry. {error}",
        ) from error
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return TransactionBatchResponse(
        portfolio_id=portfolio_id,
        mutation_id=(
            str(created_records[0].get("last_mutation_id") or "")
            if created_records
            else ""
        ),
        created_count=len(created_records),
        transfer_group_id=transfer_group_id,
        transactions=[_serialize_transaction(portfolio_id, item, account_lookup) for item in created_records],
    )
