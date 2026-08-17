from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy.exc import IntegrityError

from portfolio_app.api.assemblers import (
    serialize_transactions,
    summarize_transactions,
)
from portfolio_app.api.contracts import (
    _amount_contract_matches_display_price,
    AccountRecord,
    DerivativeContractCreate,
    DerivativeContractListResponse,
    DerivativeContractRecord,
    InstrumentCoreContract,
    OptionContractTerms,
    DerivationBoundaryStatus,
    InternalTransferCreateRequest,
    SharedInstrumentListResponse,
    LedgerPostingListSummary,
    PositionLotListSummary,
    POSITION_EFFECTIVE_COMMAND_TYPES,
    TransactionBatchResponse,
    TransactionChangeLogResponse,
    TransactionChangeLogSummary,
    TransactionCreateRequest,
    TransactionCsvImportRequest,
    TransactionCsvImportResponse,
    TransactionCsvPreviewRequest,
    TransactionCsvPreviewResponse,
    TransactionCsvPreviewRow,
    TransactionDeleteRequest,
    TransactionDeleteResponse,
    TransactionExecutionQuoteResponse,
    TransactionAssetDomain,
    TransactionAssetSubtype,
    TransactionListResponse,
    TransactionPositionPreviewResponse,
    TransactionRecord,
    TransactionUpdateRequest,
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
    validate_derivative_contract_event,
    validate_transaction_position_history,
)
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_registry_instrument,
    list_registry_instruments,
)
from portfolio_app.services.execution_quotes import get_execution_quote_on_or_before
from portfolio_app.services.account_categories import (
    account_category_from_record,
    asset_account_category,
)
from portfolio_app.services.transaction_pricing import transaction_price_scale
from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.transaction_csv import (
    MAX_CSV_BYTES,
    parse_transaction_csv,
    render_transaction_csv,
    render_transaction_csv_template,
    transaction_csv_digest,
)
from portfolio_app.services.transaction_files import transaction_upload_to_csv
from portfolio_app.services.transaction_xlsx import (
    XLSX_MEDIA_TYPE,
    render_transaction_xlsx,
    render_transaction_xlsx_template,
)
from portfolio_app.services.portfolio_store import (
    TransactionIdempotencyConflictError,
    TransactionIdempotencyKeyError,
    TransactionRowVersionConflictError,
    create_transaction,
    create_transactions,
    delete_transactions,
    get_account,
    get_derivative_contract,
    get_portfolio,
    get_transaction,
    get_transaction_idempotency_result,
    list_derivative_contracts,
    list_accounts,
    list_transaction_change_logs,
    list_transactions,
    resolve_trade_timing,
    update_transaction,
)
from portfolio_app.services.transaction_dates import transaction_execution_sort_key


router = APIRouter()

POSITION_INSTRUMENT_TYPES = {"fund", "etf", "equity", "other"}
INCOME_ASSET_TYPES: dict[str, set[str]] = {
    "dividend": {"fund", "etf", "equity"},
    "dividend_reinvestment": {"fund", "etf", "equity"},
    "coupon": {"fcn"},
    "return_of_capital": {"fund", "etf", "equity"},
    "maturity_redemption": {"fcn", "option"},
    "option_write": {"option"},
    "option_buy_to_close": {"option"},
}
LIFECYCLE_EVENT_INSTRUMENT_TYPES: dict[str, set[str]] = {
    "fcn_knock_in": {"fcn"},
    "fcn_knock_out": {"fcn"},
    "fcn_maturity": {"fcn"},
    "option_long_expiry": {"option"},
    "option_long_cash_settlement": {"option"},
    "option_writer_expiry": {"option"},
    "option_writer_cash_settlement": {"option"},
}
TRANSACTION_CREATE_IDEMPOTENCY_OPERATION = "create_transaction"
INTERNAL_TRANSFER_IDEMPOTENCY_OPERATION = "create_internal_transfer"
TRANSACTION_CSV_IMPORT_IDEMPOTENCY_OPERATION = "import_transactions_csv"


def _request_payload_for_idempotency(
    payload: TransactionCreateRequest | InternalTransferCreateRequest,
) -> dict[str, object]:
    return payload.model_dump(mode="json", exclude_none=False)


def _idempotency_replay_or_error(
    portfolio_id: str,
    *,
    idempotency_key: str | None,
    operation: str,
    request_payload: dict[str, object],
) -> list[dict[str, object]] | None:
    try:
        return get_transaction_idempotency_result(
            portfolio_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_payload=request_payload,
        )
    except TransactionIdempotencyKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TransactionIdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
        "broker_identifiers": instrument.get("broker_identifiers", []),
        "identifiers": instrument.get("identifiers", []),
    }


def _load_derivative_contract_ref(
    *,
    portfolio_id: str,
    account_id: str,
    currency: str,
    derivative_contract_id: str,
    inline_contract: DerivativeContractCreate | None,
) -> dict[str, object]:
    normalized_id = derivative_contract_id.strip()
    existing = get_derivative_contract(
        portfolio_id,
        normalized_id,
    )
    if existing is not None:
        if str(existing.get("account_id") or "") != account_id:
            raise HTTPException(
                status_code=400,
                detail="Derivative contract belongs to another account.",
            )
        if str(existing.get("currency") or "").upper() != currency.upper():
            raise HTTPException(
                status_code=400,
                detail="Derivative contract currency must match the transaction.",
            )
        if inline_contract is not None:
            supplied = inline_contract.model_dump(mode="json")
            persisted = {
                "derivative_contract_id": existing["derivative_contract_id"],
                "contract_name": existing["contract_name"],
                "contract_type": existing["contract_type"],
                "external_reference": existing.get("external_reference"),
                "terms": existing["terms"],
            }
            if supplied != persisted:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Derivative contract terms are immutable; select the existing "
                        "contract or create a new contract identity."
                    ),
                )
        return existing

    if inline_contract is None:
        raise HTTPException(
            status_code=400,
            detail="Derivative contract not found in this portfolio.",
        )
    terms = inline_contract.terms.model_dump(mode="json")
    if inline_contract.contract_type == "option":
        registry_instrument_ids = [str(terms["underlying_instrument_id"])]
    else:
        registry_instrument_ids = [
            str(item["instrument_id"])
            for item in terms["underlyings"]
        ]
    for registry_instrument_id in dict.fromkeys(registry_instrument_ids):
        _load_instrument_ref(registry_instrument_id)
    return {
        **inline_contract.model_dump(mode="json"),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "currency": currency.upper(),
        "created_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _validated_option_contract_ref(
    derivative_contract: dict[str, object] | None,
) -> dict[str, object]:
    raw = (
        derivative_contract.get("terms")
        if isinstance(derivative_contract, dict)
        else None
    )
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Option contract terms are required for option transactions.",
        )
    try:
        contract = OptionContractTerms.model_validate(raw)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Option contract terms are incomplete or invalid.",
        ) from error
    return contract.model_dump(mode="json")


def _validate_derivative_contract_event_request(
    *,
    payload: TransactionCreateRequest,
    derivative_contract: dict[str, object] | None,
    position_effective_date: date | None,
    settlement_cash_account_id: str | None,
) -> None:
    if derivative_contract is None:
        return
    transaction = payload.model_dump(mode="json", exclude_none=False)
    transaction["derivative_contract"] = derivative_contract
    transaction["position_effective_date"] = (
        position_effective_date.isoformat()
        if position_effective_date is not None
        else None
    )
    transaction["settlement_cash_account_id"] = settlement_cash_account_id
    try:
        validate_derivative_contract_event(transaction)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _requires_settlement_cash(
    *,
    payload: TransactionCreateRequest,
    account_type: str,
) -> bool:
    transaction_type = payload.transaction_type
    lifecycle_event_type = payload.lifecycle_event_type
    if transaction_type == "lifecycle_event":
        return lifecycle_event_type == "option_writer_cash_settlement"
    no_cash_long_option_closure = (
        transaction_type == "maturity_redemption"
        and lifecycle_event_type == "option_long_expiry"
    )
    return (
        transaction_type
        in {
            "buy",
            "sell",
            "option_write",
            "option_buy_to_close",
            "dividend",
            "coupon",
            "return_of_capital",
            "maturity_redemption",
        }
        and not no_cash_long_option_closure
    ) or (
        transaction_type in {"fee", "tax"}
        and account_type == "securities_account"
    )


def _validate_asset_amount_contract(
    *,
    payload: TransactionCreateRequest,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None,
) -> None:
    if instrument_ref is None and derivative_contract is None:
        return
    if payload.price is None:
        return
    if payload.transaction_type not in {
        "buy",
        "sell",
        "option_write",
        "option_buy_to_close",
        "dividend_reinvestment",
        "opening_balance",
    }:
        return
    price_scale = transaction_price_scale(
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract,
    )
    if _amount_contract_matches_display_price(
        quantity=payload.quantity,
        price=payload.price,
        gross_amount=payload.gross_amount,
        price_scale=price_scale,
    ):
        return
    if payload.transaction_type == "opening_balance":
        contract_label = "security opening balance"
    elif payload.transaction_type == "dividend_reinvestment":
        contract_label = "dividend reinvestment"
    elif payload.transaction_type in {"option_write", "option_buy_to_close"}:
        contract_label = "short option trade"
    else:
        contract_label = "buy and sell"
    raise HTTPException(
        status_code=422,
        detail=(
            "gross_amount must equal quantity multiplied by price for "
            f"{contract_label} (price scale {price_scale:g})."
        ),
    )


def _serialize_instrument_option(record: dict[str, object]) -> dict[str, object]:
    return {
        "instrument_core": {
            "instrument_id": record["instrument_id"],
            "instrument_name": record["instrument_name"],
            "instrument_type": record["instrument_type"],
            "currency": record["currency"],
            "identifiers": record["identifiers"],
            "broker_identifiers": record.get("broker_identifiers", []),
        },
        "coverage_state": record["coverage_state"],
        "latest_market_data": record["latest_market_data"],
        "quote_selection_policy": record["quote_selection_policy"],
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


def _pending_transaction_sort_key(
    *,
    trade_date: date,
    trade_at: str,
    created_at: str,
    settlement_date: date,
) -> tuple[str, str, str, int, str]:
    return (
        trade_date.isoformat(),
        trade_at,
        created_at,
        2**63 - 1,
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
        if transaction_execution_sort_key(record) <= pending_sort_key
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


def _asset_type(
    *,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None,
) -> str | None:
    if derivative_contract is not None:
        return str(derivative_contract.get("contract_type") or "").strip().lower() or None
    if instrument_ref is not None:
        return str(instrument_ref.get("instrument_type") or "").strip().lower() or None
    return None


def _validate_account_asset_category(
    *,
    account: dict[str, object],
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None,
) -> None:
    if (
        (instrument_ref is None and derivative_contract is None)
        or str(account.get("account_type") or "") != "securities_account"
    ):
        return

    asset_type = _asset_type(
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract,
    )
    try:
        account_category = account_category_from_record(account)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    asset_category = asset_account_category(
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract,
    )
    if asset_category == account_category:
        return

    account_name = str(account.get("account_name") or account.get("account_id") or "Selected account")
    raise HTTPException(
        status_code=400,
        detail=(
            f"{account_name} is a {account_category.title()} holding account. "
            f"'{asset_type}' requires a holding account in the "
            f"{str(asset_category or 'supported').title()} category."
        ),
    )


def _validate_asset_transaction_compatibility(
    *,
    transaction_type: str,
    lifecycle_event_type: str | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None,
) -> None:
    if instrument_ref is not None and derivative_contract is not None:
        raise HTTPException(
            status_code=400,
            detail="A transaction cannot reference both an instrument and a derivative contract.",
        )
    asset_type = _asset_type(
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract,
    )
    if asset_type is None:
        return

    if lifecycle_event_type is not None:
        allowed_types = LIFECYCLE_EVENT_INSTRUMENT_TYPES.get(lifecycle_event_type)
        if allowed_types is None or asset_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{lifecycle_event_type.replace('_', ' ').title()} is not "
                    f"supported for asset type '{asset_type}'."
                ),
            )
        if derivative_contract is None:
            raise HTTPException(
                status_code=400,
                detail="Derivative lifecycle events require a Portfolio contract.",
            )
    if transaction_type in {"buy", "sell", "opening_balance", "lifecycle_event"}:
        if derivative_contract is None and asset_type not in POSITION_INSTRUMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{transaction_type.replace('_', ' ').title()} is not "
                    f"supported for instrument type '{asset_type}'."
                ),
            )
        return

    option_action = resolve_option_action(
        transaction_type,
        derivative_contract=derivative_contract,
        contract_type=asset_type if derivative_contract is not None else None,
    )
    if option_action is not None:
        if derivative_contract is None or asset_type != "option":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{transaction_type.replace('_', ' ').title()} requires a "
                    "Portfolio option contract."
                ),
            )
        return

    if transaction_type in {"fee", "tax"}:
        if derivative_contract is None and asset_type not in POSITION_INSTRUMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{transaction_type.title()} selection is not supported for "
                    f"instrument type '{asset_type}'."
                ),
            )
        return

    allowed_types = INCOME_ASSET_TYPES.get(transaction_type)
    if allowed_types is not None and asset_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{transaction_type.replace('_', ' ').title()} is not supported "
                f"for asset type '{asset_type}'."
            ),
        )


def _validate_transaction_currency(
    *,
    transaction_type: str,
    transaction_currency: str,
    account: dict[str, object],
    settlement_cash_account: dict[str, object] | None,
    instrument_ref: dict[str, object] | None,
    derivative_contract: dict[str, object] | None,
) -> None:
    if transaction_type == "fx_conversion":
        expected_currency = _currency_of(account)
        if expected_currency and transaction_currency != expected_currency:
            raise HTTPException(status_code=400, detail="FX conversion source currency must match account currency.")
        return

    asset_ref = derivative_contract or instrument_ref
    if asset_ref is not None and transaction_type not in {"fee", "tax"}:
        expected_currency = _currency_of(asset_ref)
        if expected_currency and transaction_currency != expected_currency:
            raise HTTPException(status_code=400, detail="Transaction currency must match asset currency.")

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
    derivative_contract: dict[str, object] | None,
) -> None:
    if str(account.get("account_type") or "") != "securities_account":
        return

    account_currency = _currency_of(account)
    asset_currency = _currency_of(derivative_contract or instrument_ref)
    if account_currency and asset_currency and account_currency != asset_currency:
        raise HTTPException(status_code=400, detail="Securities account currency must match asset currency.")

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


def _prepare_csv_transaction_values(
    *,
    portfolio_id: str,
    payload: TransactionCreateRequest,
    created_at: str,
    batch_derivative_contracts: dict[str, DerivativeContractCreate] | None = None,
) -> dict[str, object]:
    account = get_account(portfolio_id, payload.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="Account not found")

    transaction_type = payload.transaction_type
    lifecycle_event_type = payload.lifecycle_event_type
    account_type = str(account.get("account_type") or "")
    settlement_date = payload.settlement_date or payload.trade_date
    resolved_position_effective_date = (
        payload.position_effective_date or payload.trade_date
        if transaction_type in POSITION_EFFECTIVE_COMMAND_TYPES
        else None
    )
    _validate_account_fact_window(
        account,
        event_dates=[
            payload.trade_date,
            settlement_date,
            *(
                [resolved_position_effective_date]
                if resolved_position_effective_date is not None
                else []
            ),
        ],
        role_label="Account",
    )

    deposit_only_types = {"deposit", "withdrawal", "interest", "fx_conversion"}
    securities_only_types = {
        "buy",
        "sell",
        "option_write",
        "option_buy_to_close",
        "dividend",
        "dividend_reinvestment",
        "coupon",
        "return_of_capital",
        "maturity_redemption",
        "lifecycle_event",
    }
    if transaction_type in deposit_only_types and account_type != "deposit_account":
        raise HTTPException(
            status_code=400,
            detail=f"{transaction_type.replace('_', ' ').title()} requires deposit_account.",
        )
    if transaction_type in securities_only_types and account_type != "securities_account":
        raise HTTPException(
            status_code=400,
            detail=f"{transaction_type.replace('_', ' ').title()} requires securities_account.",
        )

    settlement_cash_account_id = payload.settlement_cash_account_id
    settlement_cash_account = None
    fx_conversion_target_account = None
    if transaction_type == "fx_conversion":
        settlement_cash_account_id = None
        fx_conversion_target_account = _validate_fx_conversion(
            portfolio_id=portfolio_id,
            payload=payload,
            account=account,
        )

    requires_settlement_cash = _requires_settlement_cash(
        payload=payload,
        account_type=account_type,
    )
    if requires_settlement_cash:
        settlement_cash_account_id = settlement_cash_account_id or str(
            account.get("default_settlement_cash_account_id") or ""
        )
        if not settlement_cash_account_id:
            raise HTTPException(
                status_code=400,
                detail="This transaction requires settlement cash account.",
            )
        settlement_cash_account = get_account(
            portfolio_id,
            settlement_cash_account_id,
        )
        if settlement_cash_account is None or str(
            settlement_cash_account.get("account_type") or ""
        ) != "deposit_account":
            raise HTTPException(
                status_code=400,
                detail="Settlement cash account must be deposit_account.",
            )
        _validate_account_fact_window(
            settlement_cash_account,
            event_dates=[settlement_date],
            role_label="Settlement cash account",
        )
    elif transaction_type == "opening_balance" and settlement_cash_account_id:
        settlement_cash_account = get_account(
            portfolio_id,
            settlement_cash_account_id,
        )

    if fx_conversion_target_account is not None:
        _validate_account_fact_window(
            fx_conversion_target_account,
            event_dates=[settlement_date],
            role_label="FX conversion target account",
        )

    instrument_id = payload.instrument_id
    instrument_ref = _load_instrument_ref(instrument_id) if instrument_id else None
    derivative_contract_id = payload.derivative_contract_id
    inline_derivative_contract = payload.derivative_contract
    if (
        inline_derivative_contract is None
        and derivative_contract_id
        and batch_derivative_contracts is not None
    ):
        inline_derivative_contract = batch_derivative_contracts.get(
            derivative_contract_id
        )
    derivative_contract_ref = None
    if derivative_contract_id:
        derivative_contract_ref = _load_derivative_contract_ref(
            portfolio_id=portfolio_id,
            account_id=payload.account_id,
            currency=payload.currency,
            derivative_contract_id=derivative_contract_id,
            inline_contract=inline_derivative_contract,
        )
    has_asset_reference = (
        instrument_ref is not None or derivative_contract_ref is not None
    )
    option_action = resolve_option_action(
        transaction_type,
        derivative_contract=derivative_contract_ref,
    )
    asset_required_types = securities_only_types - {"dividend_reinvestment"}
    if transaction_type in asset_required_types and not has_asset_reference:
        raise HTTPException(
            status_code=400,
            detail="This transaction type requires an instrument or derivative contract.",
        )
    if transaction_type == "dividend_reinvestment" and instrument_ref is None:
        raise HTTPException(
            status_code=400,
            detail="Dividend reinvestment requires instrument.",
        )
    if transaction_type == "opening_balance":
        if account_type == "deposit_account" and has_asset_reference:
            raise HTTPException(
                status_code=400,
                detail="Cash opening balance must not reference an asset.",
            )
        if account_type == "securities_account" and not has_asset_reference:
            raise HTTPException(
                status_code=400,
                detail="Security opening balance requires an instrument or derivative contract.",
            )
    if (
        transaction_type in {"fee", "tax"}
        and account_type == "deposit_account"
        and has_asset_reference
    ):
        raise HTTPException(
            status_code=400,
            detail="Deposit-account fee and tax must not reference an asset.",
        )
    if (
        transaction_type in {"fee", "tax"}
        and account_type == "deposit_account"
        and settlement_cash_account_id is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Deposit-account fee and tax must not carry settlement cash account.",
        )

    _validate_asset_transaction_compatibility(
        transaction_type=transaction_type,
        lifecycle_event_type=lifecycle_event_type,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_account_asset_category(
        account=account,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_asset_amount_contract(
        payload=payload,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )

    _validate_derivative_contract_event_request(
        payload=payload,
        derivative_contract=derivative_contract_ref,
        position_effective_date=resolved_position_effective_date,
        settlement_cash_account_id=settlement_cash_account_id or None,
    )

    _validate_transaction_currency(
        transaction_type=transaction_type,
        transaction_currency=payload.currency.upper(),
        account=account,
        settlement_cash_account=settlement_cash_account,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_securities_account_currency_alignment(
        account=account,
        settlement_cash_account=settlement_cash_account,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )

    resolved_timing = resolve_trade_timing(
        trade_date=payload.trade_date,
        trade_time=payload.trade_time,
    )
    return {
        "transaction_type": transaction_type,
        "lifecycle_event_type": lifecycle_event_type,
        "trade_date": payload.trade_date,
        "trade_time": payload.trade_time,
        "trade_at": resolved_timing["trade_at"],
        "settlement_date": settlement_date,
        "position_effective_date": resolved_position_effective_date,
        "entitlement_date": payload.entitlement_date,
        "acquisition_date": payload.acquisition_date,
        "account_id": payload.account_id,
        "settlement_cash_account_id": settlement_cash_account_id or None,
        "instrument_id": instrument_id,
        "instrument_ref": instrument_ref,
        "derivative_contract_id": derivative_contract_id,
        "derivative_contract": derivative_contract_ref,
        "option_action": option_action,
        "quantity": payload.quantity,
        "price": payload.price,
        "gross_amount": payload.gross_amount,
        "counter_amount": payload.counter_amount,
        "fx_rate": payload.fx_rate,
        "fees": payload.fees,
        "fee_category": payload.fee_category,
        "taxes": payload.taxes,
        "currency": payload.currency,
        "transfer_scope": None,
        "transfer_object_type": None,
        "transfer_group_id": None,
        "counterparty_account_id": (
            str(fx_conversion_target_account.get("account_id") or "")
            if fx_conversion_target_account is not None
            else payload.counterparty_account_id
        ),
        "source_system": payload.source_system,
        "external_reference": payload.external_reference,
        "note": payload.note,
        "created_at": created_at,
    }


def _prepare_internal_transfer_values(
    *,
    portfolio_id: str,
    payload: InternalTransferCreateRequest,
    created_at: str,
    transfer_group_id: str,
    expected_currency: str | None = None,
    pending_records: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    from_account = get_account(portfolio_id, payload.from_account_id)
    to_account = get_account(portfolio_id, payload.to_account_id)
    if from_account is None or to_account is None:
        raise HTTPException(status_code=400, detail="Transfer accounts not found.")
    settlement_date = payload.settlement_date or payload.trade_date
    resolved_trade_timing = resolve_trade_timing(
        trade_date=payload.trade_date,
        trade_time=payload.trade_time,
    )
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
        if (
            from_account.get("account_type") != "deposit_account"
            or to_account.get("account_type") != "deposit_account"
        ):
            raise HTTPException(
                status_code=400,
                detail="Cash transfer requires deposit accounts on both legs.",
            )
        transfer_currency = _currency_of(from_account)
        if transfer_currency != _currency_of(to_account):
            raise HTTPException(
                status_code=400,
                detail="Cash transfer accounts must share the same currency.",
            )
        instrument_id = None
        instrument_ref = None
        transactions_as_of_trade_date: list[dict[str, object]] = []
        account_cost_methods: dict[str, str] = {}
    else:
        if (
            from_account.get("account_type") != "securities_account"
            or to_account.get("account_type") != "securities_account"
        ):
            raise HTTPException(
                status_code=400,
                detail="Position transfer requires securities accounts on both legs.",
            )
        instrument_id = payload.instrument_id
        if not instrument_id:
            raise HTTPException(
                status_code=400,
                detail="Position transfer requires instrument.",
            )
        instrument_ref = _load_instrument_ref(instrument_id)
        _validate_asset_transaction_compatibility(
            transaction_type="opening_balance",
            lifecycle_event_type=None,
            instrument_ref=instrument_ref,
            derivative_contract=None,
        )
        _validate_account_asset_category(
            account=from_account,
            instrument_ref=instrument_ref,
            derivative_contract=None,
        )
        _validate_account_asset_category(
            account=to_account,
            instrument_ref=instrument_ref,
            derivative_contract=None,
        )
        transfer_currency = str(instrument_ref.get("currency") or "").upper()
        if (
            _currency_of(from_account) != transfer_currency
            or _currency_of(to_account) != transfer_currency
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Position transfer accounts must share the same currency as the instrument."
                ),
            )
        account_cost_methods = _account_cost_methods(portfolio_id)
        transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
            portfolio_id,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=created_at,
            settlement_date=settlement_date,
        )
        if pending_records:
            existing_sequences = [
                int(record.get("transaction_sequence") or 0)
                for record in transactions_as_of_trade_date
            ]
            next_sequence = max(existing_sequences, default=0) + 1
            pending_sort_key = _pending_transaction_sort_key(
                trade_date=payload.trade_date,
                trade_at=str(resolved_trade_timing["trade_at"]),
                created_at=created_at,
                settlement_date=settlement_date,
            )
            pending_candidates = [
                {
                    **record,
                    "transaction_id": f"csv-pending-{index:06d}",
                    "transaction_sequence": next_sequence + index - 1,
                    "portfolio_id": portfolio_id,
                }
                for index, record in enumerate(pending_records, start=1)
            ]
            pending_as_of = [
                record
                for record in pending_candidates
                if transaction_execution_sort_key(record) <= pending_sort_key
            ]
            transactions_as_of_trade_date = [
                *transactions_as_of_trade_date,
                *pending_as_of,
            ]
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_trade_date,
            account_id=payload.from_account_id,
            position_reference_id=instrument_id,
            account_cost_methods=account_cost_methods,
            as_of_date=payload.trade_date,
        )
        requested_quantity = float(payload.quantity or 0.0)
        if requested_quantity > available_quantity + 1e-9:
            raise HTTPException(
                status_code=400,
                detail="Transfer quantity exceeds source position as of trade_date.",
            )

    if expected_currency and expected_currency.upper() != transfer_currency:
        raise HTTPException(
            status_code=400,
            detail="Internal transfer currency must match the transferred cash or position.",
        )

    transferred_amount = payload.gross_amount
    if transfer_object_type == "position":
        estimated_transferred_amount = estimate_position_cost_basis(
            portfolio_id,
            transactions_as_of_trade_date,
            account_id=payload.from_account_id,
            position_reference_id=instrument_id or "",
            quantity=float(payload.quantity or 0.0),
            account_cost_methods=account_cost_methods,
            as_of_date=payload.trade_date,
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
                    detail=(
                        "Position transfer gross_amount must match source cost basis as of trade_date."
                    ),
                )
        transferred_amount = estimated_transferred_amount

    common_values: dict[str, object] = {
        "trade_date": payload.trade_date,
        "trade_time": payload.trade_time,
        "trade_at": resolved_trade_timing["trade_at"],
        "settlement_date": settlement_date,
        "entitlement_date": None,
        "acquisition_date": None,
        "settlement_cash_account_id": None,
        "instrument_id": instrument_id,
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
        "note": payload.note,
        "created_at": created_at,
    }
    return [
        {
            **common_values,
            "transaction_type": "transfer_out",
            "account_id": payload.from_account_id,
            "counterparty_account_id": payload.to_account_id,
        },
        {
            **common_values,
            "transaction_type": "transfer_in",
            "account_id": payload.to_account_id,
            "counterparty_account_id": payload.from_account_id,
        },
    ]


def _build_transaction_csv_preview(
    *,
    portfolio_id: str,
    request: TransactionCsvPreviewRequest,
) -> tuple[TransactionCsvPreviewResponse, list[dict[str, object]]]:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        headers, parsed_rows = parse_transaction_csv(
            request.csv_text,
            default_source_system=request.default_source_system,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    preview_digest = transaction_csv_digest(
        request.csv_text,
        default_source_system=request.default_source_system,
    )
    created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )
    response_rows: list[TransactionCsvPreviewRow] = []
    prepared_records: list[dict[str, object]] = []
    valid_payloads: list[TransactionCreateRequest] = []
    batch_errors: list[str] = []
    batch_derivative_contracts: dict[str, DerivativeContractCreate] = {}
    batch_contract_scopes: dict[str, tuple[str, str]] = {}
    for parsed_row in parsed_rows:
        transaction = parsed_row.transaction
        if transaction is None or not transaction.derivative_contract_id:
            continue
        contract_id = transaction.derivative_contract_id
        scope = (transaction.account_id, transaction.currency)
        previous_scope = batch_contract_scopes.setdefault(contract_id, scope)
        if previous_scope != scope:
            batch_errors.append(
                f"Derivative contract '{contract_id}' is used across multiple "
                "accounts or currencies in one import file."
            )
        if transaction.derivative_contract is None:
            continue
        previous_contract = batch_derivative_contracts.setdefault(
            contract_id,
            transaction.derivative_contract,
        )
        if previous_contract != transaction.derivative_contract:
            batch_errors.append(
                f"Derivative contract '{contract_id}' has conflicting immutable terms "
                "in the import file."
            )
    for parsed_row in parsed_rows:
        if parsed_row.transaction is None and parsed_row.internal_transfer is None:
            response_rows.append(
                TransactionCsvPreviewRow(
                    row_number=parsed_row.row_number,
                    errors=list(parsed_row.errors),
                )
            )
            continue
        if parsed_row.internal_transfer is not None:
            internal_transfer = parsed_row.internal_transfer
            transfer_group_id = (
                "trf-"
                + uuid5(
                    NAMESPACE_URL,
                    f"{portfolio_id}:csv:{preview_digest}:{parsed_row.row_number}",
                ).hex[:12]
            )
            try:
                transfer_values = _prepare_internal_transfer_values(
                    portfolio_id=portfolio_id,
                    payload=internal_transfer,
                    created_at=created_at,
                    transfer_group_id=transfer_group_id,
                    expected_currency=internal_transfer.currency,
                    pending_records=prepared_records,
                )
            except (HTTPException, InstrumentRegistryError, ValueError) as error:
                detail = (
                    str(error.detail)
                    if isinstance(error, HTTPException)
                    else str(error)
                )
                response_rows.append(
                    TransactionCsvPreviewRow(
                        row_number=parsed_row.row_number,
                        internal_transfer=internal_transfer,
                        errors=[detail],
                    )
                )
                continue
            prepared_records.extend(transfer_values)
            response_rows.append(
                TransactionCsvPreviewRow(
                    row_number=parsed_row.row_number,
                    internal_transfer=internal_transfer,
                )
            )
            continue
        assert parsed_row.transaction is not None
        try:
            values = _prepare_csv_transaction_values(
                portfolio_id=portfolio_id,
                payload=parsed_row.transaction,
                created_at=created_at,
                batch_derivative_contracts=batch_derivative_contracts,
            )
        except (HTTPException, InstrumentRegistryError) as error:
            detail = (
                str(error.detail)
                if isinstance(error, HTTPException)
                else str(error)
            )
            response_rows.append(
                TransactionCsvPreviewRow(
                    row_number=parsed_row.row_number,
                    transaction=parsed_row.transaction,
                    errors=[detail],
                )
            )
            continue
        prepared_records.append(values)
        valid_payloads.append(parsed_row.transaction)
        response_rows.append(
            TransactionCsvPreviewRow(
                row_number=parsed_row.row_number,
                transaction=parsed_row.transaction,
            )
        )

    existing_records = list_transactions(portfolio_id)
    if (
        len(response_rows) == len(parsed_rows)
        and not any(row.errors for row in response_rows)
        and not batch_errors
    ):
        next_preview_sequence = max(
            (
                int(record["transaction_sequence"])
                for record in existing_records
            ),
            default=0,
        ) + 1
        synthetic_records: list[dict[str, object]] = []
        for index, values in enumerate(prepared_records, start=1):
            synthetic_records.append(
                {
                    **values,
                    "transaction_id": f"preview-{index:06d}",
                    "transaction_sequence": next_preview_sequence + index - 1,
                    "portfolio_id": portfolio_id,
                }
            )
        try:
            validate_transaction_position_history(
                portfolio_id,
                [*existing_records, *synthetic_records],
                account_cost_methods=_account_cost_methods(portfolio_id),
            )
        except ValueError as error:
            batch_errors.append(str(error))

    source_identities: set[tuple[str, str]] = set()
    duplicate_source_identities: set[tuple[str, str]] = set()
    for payload in valid_payloads:
        if payload.source_system and payload.external_reference:
            identity = (payload.source_system, payload.external_reference)
            if identity in source_identities:
                duplicate_source_identities.add(identity)
            source_identities.add(identity)
    if duplicate_source_identities:
        batch_errors.append(
            "Import file repeats source_system/external_reference identities: "
            + ", ".join(
                f"{source}/{reference}"
                for source, reference in sorted(duplicate_source_identities)
            )
            + "."
        )
    existing_source_identities = {
        (
            str(record.get("source_system") or ""),
            str(record.get("external_reference") or ""),
        )
        for record in existing_records
        if record.get("source_system") and record.get("external_reference")
    }
    already_imported_identities = source_identities & existing_source_identities
    if already_imported_identities:
        batch_errors.append(
            "Source identities already exist in this portfolio: "
            + ", ".join(
                f"{source}/{reference}"
                for source, reference in sorted(already_imported_identities)
            )
            + "."
        )

    warnings: list[str] = []
    missing_source_identity_count = sum(
        1
        for payload in valid_payloads
        if not payload.source_system or not payload.external_reference
    )
    if missing_source_identity_count:
        warnings.append(
            f"{missing_source_identity_count} row(s) lack a complete source identity; "
            "only the batch Idempotency-Key protects replay."
        )

    row_error_count = sum(1 for row in response_rows if row.errors)
    response = TransactionCsvPreviewResponse(
        portfolio_id=portfolio_id,
        preview_digest=preview_digest,
        headers=list(headers),
        row_count=len(response_rows),
        valid_count=sum(1 for row in response_rows if not row.errors),
        error_count=row_error_count + len(batch_errors),
        warnings=warnings,
        batch_errors=batch_errors,
        rows=response_rows,
    )
    return response, prepared_records


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


@router.get(
    "/{portfolio_id}/derivative-contracts",
    response_model=DerivativeContractListResponse,
)
def list_portfolio_derivative_contracts(
    portfolio_id: str,
) -> DerivativeContractListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return DerivativeContractListResponse(
        portfolio_id=portfolio_id,
        derivative_contracts=[
            DerivativeContractRecord.model_validate(record)
            for record in list_derivative_contracts(portfolio_id)
        ],
    )


@router.get("/{portfolio_id}/transactions", response_model=TransactionListResponse)
def list_transaction_records(
    portfolio_id: str,
    account_id: str | None = None,
    asset_domain: TransactionAssetDomain | None = None,
    asset_subtype: TransactionAssetSubtype | None = None,
    transaction_type: str | None = None,
    position_reference_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> TransactionListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    records = list_transactions(
        portfolio_id,
        account_id=account_id,
        asset_domain=asset_domain,
        asset_subtype=asset_subtype,
        transaction_type=transaction_type,
        position_reference_id=position_reference_id,
        start_date=start_date,
        end_date=end_date,
    )
    return TransactionListResponse(
        portfolio_id=portfolio_id,
        summary=summarize_transactions(records),
        derivation_boundary=DerivationBoundaryStatus(),
        transactions=serialize_transactions(portfolio_id, records, account_lookup),
    )


@router.get("/{portfolio_id}/transactions.csv")
def download_transaction_csv(portfolio_id: str) -> Response:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    content = render_transaction_csv(list_transactions(portfolio_id))
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{portfolio_id}-transactions.csv"'
            )
        },
    )


@router.get("/{portfolio_id}/transactions/csv-template")
def download_transaction_csv_template(portfolio_id: str) -> Response:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return Response(
        content=render_transaction_csv_template(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{portfolio_id}-transaction-import-template.csv"'
            )
        },
    )


@router.get("/{portfolio_id}/transactions.xlsx")
def download_transaction_xlsx(portfolio_id: str) -> Response:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return Response(
        content=render_transaction_xlsx(list_transactions(portfolio_id)),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{portfolio_id}-transactions.xlsx"'
            )
        },
    )


@router.get("/{portfolio_id}/transactions/xlsx-template")
def download_transaction_xlsx_template(portfolio_id: str) -> Response:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return Response(
        content=render_transaction_xlsx_template(),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{portfolio_id}-transaction-import-template.xlsx"'
            )
        },
    )


@router.post(
    "/{portfolio_id}/transactions/csv/preview",
    response_model=TransactionCsvPreviewResponse,
)
def preview_transaction_csv(
    portfolio_id: str,
    payload: TransactionCsvPreviewRequest,
) -> TransactionCsvPreviewResponse:
    preview, _prepared_records = _build_transaction_csv_preview(
        portfolio_id=portfolio_id,
        request=payload,
    )
    return preview


@router.post(
    "/{portfolio_id}/transactions/csv/import",
    response_model=TransactionCsvImportResponse,
)
def import_transaction_csv(
    portfolio_id: str,
    payload: TransactionCsvImportRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> TransactionCsvImportResponse:
    expected_digest = transaction_csv_digest(
        payload.csv_text,
        default_source_system=payload.default_source_system,
    )
    if payload.preview_digest != expected_digest:
        raise HTTPException(
            status_code=409,
            detail="Transaction file content changed after preview; preview the file again.",
        )

    idempotency_payload = {
        "preview_digest": expected_digest,
        "default_source_system": payload.default_source_system,
    }
    replayed = _idempotency_replay_or_error(
        portfolio_id,
        idempotency_key=idempotency_key,
        operation=TRANSACTION_CSV_IMPORT_IDEMPOTENCY_OPERATION,
        request_payload=idempotency_payload,
    )
    if replayed is not None:
        account_lookup = {
            item["account_id"]: item for item in list_accounts(portfolio_id)
        }
        return TransactionCsvImportResponse(
            portfolio_id=portfolio_id,
            preview_digest=expected_digest,
            created_count=len(replayed),
            transactions=serialize_transactions(
                portfolio_id,
                replayed,
                account_lookup,
            ),
        )

    preview, prepared_records = _build_transaction_csv_preview(
        portfolio_id=portfolio_id,
        request=TransactionCsvPreviewRequest(
            csv_text=payload.csv_text,
            default_source_system=payload.default_source_system,
        ),
    )
    if preview.error_count:
        raise HTTPException(
            status_code=422,
            detail=preview.model_dump(mode="json"),
        )

    try:
        created = create_transactions(
            portfolio_id=portfolio_id,
            records=prepared_records,
            idempotency_key=idempotency_key,
            idempotency_payload=idempotency_payload,
            idempotency_operation=TRANSACTION_CSV_IMPORT_IDEMPOTENCY_OPERATION,
        )
    except TransactionIdempotencyKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TransactionIdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail=(
                "A transaction with the same portfolio/source_system/"
                "external_reference already exists."
            ),
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    account_lookup = {
        item["account_id"]: item for item in list_accounts(portfolio_id)
    }
    return TransactionCsvImportResponse(
        portfolio_id=portfolio_id,
        preview_digest=preview.preview_digest,
        created_count=len(created),
        transactions=serialize_transactions(
            portfolio_id,
            created,
            account_lookup,
        ),
    )


async def _read_transaction_upload(file: UploadFile) -> str:
    try:
        content = await file.read(MAX_CSV_BYTES + 1)
    finally:
        await file.close()
    try:
        return transaction_upload_to_csv(file.filename, content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/{portfolio_id}/transactions/files/preview",
    response_model=TransactionCsvPreviewResponse,
)
async def preview_transaction_file(
    portfolio_id: str,
    file: UploadFile = File(...),
    default_source_system: str | None = Form(
        default="portfolio_file_upload",
        max_length=100,
    ),
) -> TransactionCsvPreviewResponse:
    csv_text = await _read_transaction_upload(file)
    return preview_transaction_csv(
        portfolio_id,
        TransactionCsvPreviewRequest(
            csv_text=csv_text,
            default_source_system=default_source_system,
        ),
    )


@router.post(
    "/{portfolio_id}/transactions/files/import",
    response_model=TransactionCsvImportResponse,
)
async def import_transaction_file(
    portfolio_id: str,
    file: UploadFile = File(...),
    preview_digest: str = Form(..., min_length=64, max_length=64),
    default_source_system: str | None = Form(
        default="portfolio_file_upload",
        max_length=100,
    ),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> TransactionCsvImportResponse:
    csv_text = await _read_transaction_upload(file)
    return import_transaction_csv(
        portfolio_id,
        TransactionCsvImportRequest(
            csv_text=csv_text,
            default_source_system=default_source_system,
            preview_digest=preview_digest,
        ),
        idempotency_key,
    )


@router.get(
    "/{portfolio_id}/transactions/change-log",
    response_model=TransactionChangeLogResponse,
)
def list_transaction_change_log_records(
    portfolio_id: str,
    transaction_id: str | None = None,
) -> TransactionChangeLogResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    changes = list_transaction_change_logs(
        portfolio_id,
        transaction_id=transaction_id,
    )
    return TransactionChangeLogResponse(
        portfolio_id=portfolio_id,
        summary=TransactionChangeLogSummary(change_count=len(changes)),
        changes=changes,
    )


@router.get("/{portfolio_id}/transactions/workspace", response_model=TransactionWorkspaceResponse)
def get_transaction_workspace(
    portfolio_id: str,
    account_id: str | None = None,
    asset_domain: TransactionAssetDomain | None = None,
    asset_subtype: TransactionAssetSubtype | None = None,
    transaction_type: str | None = None,
    position_reference_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    transaction_id: str | None = None,
) -> TransactionWorkspaceResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    account_lookup = {item["account_id"]: item for item in accounts}
    all_transactions = list_transactions(portfolio_id)
    filtered_records = list_transactions(
        portfolio_id,
        account_id=account_id,
        asset_domain=asset_domain,
        asset_subtype=asset_subtype,
        transaction_type=transaction_type,
        position_reference_id=position_reference_id,
        start_date=start_date,
        end_date=end_date,
    )
    serialized_transactions = serialize_transactions(
        portfolio_id,
        filtered_records,
        account_lookup,
    )
    selected_transaction = next(
        (item for item in serialized_transactions if item.transaction_id == transaction_id),
        serialized_transactions[0] if serialized_transactions else None,
    )
    selected_transaction_id = selected_transaction.transaction_id if selected_transaction else None

    selected_transaction_record = next(
        (
            record
            for record in all_transactions
            if str(record.get("transaction_id") or "") == selected_transaction_id
        ),
        None,
    )
    selected_transfer_group_id = str(
        (selected_transaction_record or {}).get("transfer_group_id") or ""
    ).strip()
    delete_scope_records = (
        [
            record
            for record in all_transactions
            if str(record.get("transfer_group_id") or "").strip()
            == selected_transfer_group_id
        ]
        if selected_transfer_group_id
        else ([selected_transaction_record] if selected_transaction_record else [])
    )
    delete_scope_row_versions = {
        str(record["transaction_id"]): int(record["row_version"])
        for record in delete_scope_records
    }
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
    if selected_transaction and selected_transaction.instrument_id:
        candidate_position_lots = build_position_lots(
            portfolio_id,
            accounts,
            all_transactions,
            account_id=selected_transaction.account.account_id,
            position_reference_id=(
                selected_transaction.derivative_contract_id
                or selected_transaction.instrument_id
            ),
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

    change_log = (
        list_transaction_change_logs(
            portfolio_id,
            transaction_id=selected_transaction_id,
        )
        if selected_transaction_id
        else []
    )

    return TransactionWorkspaceResponse(
        portfolio_id=portfolio_id,
        summary=summarize_transactions(filtered_records),
        derivation_boundary=DerivationBoundaryStatus(
            ledger_postings="next_layer",
            positions="next_layer",
            position_lots="next_layer",
            holdings="not_started",
            snapshot="not_started",
        ),
        selected_transaction_id=selected_transaction_id,
        position_reference_ids=sorted(
            {
                str(item.get("instrument_id") or item.get("derivative_contract_id"))
                for item in all_transactions
                if item.get("instrument_id") or item.get("derivative_contract_id")
            }
        ),
        transactions=serialized_transactions,
        selected_transaction=selected_transaction,
        delete_scope_row_versions=delete_scope_row_versions,
        ledger_summary=LedgerPostingListSummary.model_validate(summarize_ledger_postings(ledger_postings_raw)),
        ledger_postings=ledger_postings_raw,
        related_position_lot_summary=PositionLotListSummary.model_validate(
            summarize_position_lots(related_position_lots_raw)
        ),
        related_position_lots=related_position_lots_raw,
        change_log_summary=TransactionChangeLogSummary(change_count=len(change_log)),
        change_log=change_log,
    )


@router.get("/{portfolio_id}/transactions/position-preview", response_model=TransactionPositionPreviewResponse)
def get_transaction_position_preview(
    portfolio_id: str,
    account_id: str,
    position_kind: str,
    position_reference_id: str,
    as_of_date: date = Query(...),
    trade_time: str | None = None,
    exclude_transaction_id: str | None = None,
) -> TransactionPositionPreviewResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if get_account(portfolio_id, account_id) is None:
        raise HTTPException(status_code=400, detail="Account not found")

    if position_kind == "instrument":
        _load_instrument_ref(position_reference_id)
    elif position_kind == "derivative_contract":
        derivative_contract = get_derivative_contract(
            portfolio_id,
            position_reference_id,
        )
        if derivative_contract is None:
            raise HTTPException(
                status_code=400,
                detail="Derivative contract not found in this portfolio.",
            )
        if str(derivative_contract.get("account_id") or "") != account_id:
            raise HTTPException(
                status_code=400,
                detail="Derivative contract belongs to another account.",
            )
    else:
        raise HTTPException(
            status_code=422,
            detail="position_kind must be instrument or derivative_contract.",
        )
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
        position_reference_id=position_reference_id,
        account_cost_methods=_account_cost_methods(portfolio_id),
        as_of_date=as_of_date,
    )
    return TransactionPositionPreviewResponse(
        portfolio_id=portfolio_id,
        account_id=account_id,
        position_kind=position_kind,
        position_reference_id=position_reference_id,
        as_of_date=as_of_date,
        trade_at=str(resolved_trade_timing["trade_at"]),
        quantity=quantity,
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
    """Resolve an unadjusted price for a transaction form on or before a date.

    Trading policy takes precedence over valuation policy. Performance/chart
    series such as adjusted_close and total_return_nav are never eligible.
    """

    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        quote = get_execution_quote_on_or_before(
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
    payload: TransactionCreateRequest,
    existing_transaction: dict[str, object] | None = None,
    expected_row_version: int | None = None,
    idempotency_key: str | None = None,
    idempotency_payload: dict[str, object] | None = None,
) -> TransactionRecord:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account = get_account(portfolio_id, payload.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="Account not found")
    settlement_date = payload.settlement_date or payload.trade_date
    resolved_position_effective_date = (
        (payload.position_effective_date or payload.trade_date)
        if payload.transaction_type in POSITION_EFFECTIVE_COMMAND_TYPES
        else None
    )
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
        event_dates=[
            payload.trade_date,
            settlement_date,
            *(
                [resolved_position_effective_date]
                if resolved_position_effective_date is not None
                else []
            ),
        ],
        role_label="Account",
    )
    account_cost_methods = _account_cost_methods(portfolio_id)

    transaction_type = payload.transaction_type
    lifecycle_event_type = payload.lifecycle_event_type
    account_type = str(account.get("account_type") or "")
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
        "lifecycle_event",
        "option_write",
        "option_buy_to_close",
    } and account_type != "securities_account":
        raise HTTPException(status_code=400, detail="Instrument income and trade transactions require securities_account.")
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
    if _requires_settlement_cash(payload=payload, account_type=account_type):
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

    derivative_contract_id = payload.derivative_contract_id
    derivative_contract_ref = None
    if derivative_contract_id:
        derivative_contract_ref = _load_derivative_contract_ref(
            portfolio_id=portfolio_id,
            account_id=payload.account_id,
            currency=payload.currency,
            derivative_contract_id=derivative_contract_id,
            inline_contract=payload.derivative_contract,
        )

    has_asset_reference = instrument_ref is not None or derivative_contract_ref is not None
    position_reference_id = derivative_contract_id or instrument_id
    option_action = resolve_option_action(
        transaction_type,
        derivative_contract=derivative_contract_ref,
    )

    asset_required_transaction_types = {
        "buy",
        "sell",
        "dividend",
        "dividend_reinvestment",
        "coupon",
        "return_of_capital",
        "maturity_redemption",
        "lifecycle_event",
        "option_write",
        "option_buy_to_close",
    }
    if transaction_type in asset_required_transaction_types and not has_asset_reference:
        raise HTTPException(
            status_code=400,
            detail="This transaction type requires an instrument or derivative contract.",
        )

    if transaction_type in {"deposit", "withdrawal", "fx_conversion", "interest"} and has_asset_reference:
        raise HTTPException(
            status_code=400,
            detail=f"{transaction_type.replace('_', ' ').title()} must not reference an asset.",
        )

    if (
        transaction_type == "opening_balance"
        and account_type == "deposit_account"
        and has_asset_reference
    ):
        raise HTTPException(
            status_code=400,
            detail="Cash opening balance must not reference an asset.",
        )

    if (
        transaction_type == "opening_balance"
        and account_type == "securities_account"
        and not has_asset_reference
    ):
        raise HTTPException(
            status_code=400,
            detail="Security opening balance requires an instrument or derivative contract.",
        )

    if (
        transaction_type in {"fee", "tax"}
        and account_type == "deposit_account"
        and has_asset_reference
    ):
        raise HTTPException(
            status_code=400,
            detail="Deposit-account fee and tax must not reference an asset.",
        )
    if (
        transaction_type in {"fee", "tax"}
        and account_type == "deposit_account"
        and settlement_cash_account_id is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Deposit-account fee and tax must not carry settlement cash account.",
        )

    _validate_asset_transaction_compatibility(
        transaction_type=transaction_type,
        lifecycle_event_type=lifecycle_event_type,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_account_asset_category(
        account=account,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_asset_amount_contract(
        payload=payload,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )
    _validate_derivative_contract_event_request(
        payload=payload,
        derivative_contract=derivative_contract_ref,
        position_effective_date=resolved_position_effective_date,
        settlement_cash_account_id=settlement_cash_account_id or None,
    )

    transactions_as_of_trade_date: list[dict[str, object]] | None = None
    if (
        transaction_type in {"sell", "maturity_redemption"}
        and position_reference_id
    ):
        transactions_as_of_trade_date = _list_transactions_as_of_trade_moment(
            portfolio_id,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=pending_created_at,
            settlement_date=settlement_date,
            exclude_transaction_ids=excluded_transaction_ids,
        )

    transactions_as_of_entitlement_date: list[dict[str, object]] | None = None
    position_linked_income_or_expense = (
        transaction_type in {"dividend", "dividend_reinvestment", "coupon"}
        or (transaction_type in {"fee", "tax"} and has_asset_reference)
    )
    if position_linked_income_or_expense and position_reference_id:
        transactions_as_of_entitlement_date = _list_transactions_as_of_entitlement_moment(
            portfolio_id,
            entitlement_date=entitlement_date,
            trade_date=payload.trade_date,
            trade_at=str(resolved_trade_timing["trade_at"]),
            created_at=pending_created_at,
            settlement_date=settlement_date,
            exclude_transaction_ids=excluded_transaction_ids,
        )

    if (
        transaction_type in {"sell", "maturity_redemption"}
        and position_reference_id
    ):
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_trade_date or [],
            account_id=payload.account_id,
            position_reference_id=position_reference_id,
            account_cost_methods=account_cost_methods,
            as_of_date=payload.trade_date,
        )
        requested_quantity = float(payload.quantity or 0.0)
        if requested_quantity > available_quantity + 1e-9:
            raise HTTPException(
                status_code=400,
                detail="Transaction quantity exceeds account position as of trade_date.",
            )

    if position_linked_income_or_expense and position_reference_id:
        available_quantity = estimate_position_quantity(
            portfolio_id,
            transactions_as_of_entitlement_date or [],
            account_id=payload.account_id,
            position_reference_id=position_reference_id,
            account_cost_methods=account_cost_methods,
        )
        if available_quantity <= 1e-9:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Asset-linked income and expense requires an account position "
                    "as of entitlement_date."
                ),
            )

    if transaction_type == "return_of_capital" and instrument_id:
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
            position_reference_id=instrument_id,
            account_cost_methods=account_cost_methods,
            as_of_date=payload.trade_date,
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
        derivative_contract=derivative_contract_ref,
    )
    _validate_securities_account_currency_alignment(
        account=account,
        settlement_cash_account=settlement_cash_account,
        instrument_ref=instrument_ref,
        derivative_contract=derivative_contract_ref,
    )


    transaction_values = {
        "transaction_type": transaction_type,
        "lifecycle_event_type": lifecycle_event_type,
        "trade_date": payload.trade_date,
        "trade_time": payload.trade_time,
        "settlement_date": settlement_date,
        "position_effective_date": resolved_position_effective_date,
        "entitlement_date": payload.entitlement_date,
        "acquisition_date": payload.acquisition_date,
        "account_id": payload.account_id,
        "settlement_cash_account_id": settlement_cash_account_id or None,
        "instrument_id": instrument_id,
        "instrument_ref": instrument_ref,
        "derivative_contract_id": derivative_contract_id,
        "derivative_contract": (
            payload.derivative_contract.model_dump(mode="json")
            if payload.derivative_contract is not None
            else None
        ),
        "quantity": payload.quantity,
        "price": payload.price,
        "gross_amount": payload.gross_amount,
        "counter_amount": payload.counter_amount,
        "fx_rate": payload.fx_rate,
        "fees": payload.fees,
        "fee_category": payload.fee_category,
        "taxes": payload.taxes,
        "currency": payload.currency,
        "transfer_scope": None,
        "transfer_object_type": None,
        "transfer_group_id": None,
        "counterparty_account_id": (
            str(fx_conversion_target_account.get("account_id") or "")
            if fx_conversion_target_account is not None
            else payload.counterparty_account_id
        ),
        "source_system": payload.source_system,
        "external_reference": payload.external_reference,
        "note": payload.note,
        "created_at": pending_created_at,
    }
    try:
        if existing_transaction is not None:
            persisted_record = update_transaction(
                portfolio_id,
                str(existing_transaction.get("transaction_id") or ""),
                expected_row_version=expected_row_version,
                **transaction_values,
            )
        else:
            persisted_record = create_transaction(
                portfolio_id=portfolio_id,
                idempotency_key=idempotency_key,
                idempotency_payload=idempotency_payload,
                idempotency_operation=TRANSACTION_CREATE_IDEMPOTENCY_OPERATION,
                **transaction_values,
            )
    except TransactionIdempotencyKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (
        TransactionIdempotencyConflictError,
        TransactionRowVersionConflictError,
    ) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail=(
                "A transaction with the same portfolio/source_system/"
                "external_reference already exists."
            ),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction history changed; reload and retry. {error}",
        ) from error
    if persisted_record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return serialize_transactions(portfolio_id, [persisted_record], account_lookup)[0]


@router.post("/{portfolio_id}/transactions", response_model=TransactionRecord)
def create_transaction_record(
    portfolio_id: str,
    payload: TransactionCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TransactionRecord:
    idempotency_payload = _request_payload_for_idempotency(payload)
    replayed = _idempotency_replay_or_error(
        portfolio_id,
        idempotency_key=idempotency_key,
        operation=TRANSACTION_CREATE_IDEMPOTENCY_OPERATION,
        request_payload=idempotency_payload,
    )
    if replayed is not None:
        account_lookup = {
            item["account_id"]: item for item in list_accounts(portfolio_id)
        }
        return serialize_transactions(portfolio_id, [replayed[0]], account_lookup)[0]
    return _persist_transaction_record(
        portfolio_id=portfolio_id,
        payload=payload,
        idempotency_key=idempotency_key,
        idempotency_payload=idempotency_payload,
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
    current_row_version = int(existing_transaction.get("row_version") or 1)
    if payload.expected_row_version != current_row_version:
        raise HTTPException(
            status_code=409,
            detail=(
                "Transaction row version is stale; reload and retry "
                f"(expected {payload.expected_row_version}, current {current_row_version})."
            ),
        )
    return _persist_transaction_record(
        portfolio_id=portfolio_id,
        payload=payload,
        existing_transaction=existing_transaction,
        expected_row_version=payload.expected_row_version,
    )


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
    try:
        deleted_records = delete_transactions(
            portfolio_id,
            transaction_ids=transaction_ids,
            expected_row_versions=payload.expected_row_versions,
        )
    except TransactionRowVersionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Deleting this fact would invalidate later position history. {error}",
        ) from error
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TransactionBatchResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    idempotency_payload = _request_payload_for_idempotency(payload)
    replayed = _idempotency_replay_or_error(
        portfolio_id,
        idempotency_key=idempotency_key,
        operation=INTERNAL_TRANSFER_IDEMPOTENCY_OPERATION,
        request_payload=idempotency_payload,
    )
    if replayed is not None:
        account_lookup = {
            item["account_id"]: item for item in list_accounts(portfolio_id)
        }
        transfer_group_id = (
            str(replayed[0].get("transfer_group_id") or "").strip() or None
        )
        return TransactionBatchResponse(
            portfolio_id=portfolio_id,
            created_count=len(replayed),
            transfer_group_id=transfer_group_id,
            transactions=serialize_transactions(
                portfolio_id,
                replayed,
                account_lookup,
            ),
        )

    pending_created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    transfer_group_id = (
        f"trf-{uuid5(NAMESPACE_URL, f'{portfolio_id}:{idempotency_key.strip()}').hex[:12]}"
        if idempotency_key
        else f"trf-{uuid4().hex[:12]}"
    )
    prepared_records = _prepare_internal_transfer_values(
        portfolio_id=portfolio_id,
        payload=payload,
        created_at=pending_created_at,
        transfer_group_id=transfer_group_id,
    )

    try:
        created_records = create_transactions(
            portfolio_id=portfolio_id,
            records=prepared_records,
            idempotency_key=idempotency_key,
            idempotency_payload=idempotency_payload,
            idempotency_operation=INTERNAL_TRANSFER_IDEMPOTENCY_OPERATION,
        )
    except TransactionIdempotencyKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TransactionIdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction history changed; reload and retry. {error}",
        ) from error
    account_lookup = {item["account_id"]: item for item in list_accounts(portfolio_id)}
    return TransactionBatchResponse(
        portfolio_id=portfolio_id,
        created_count=len(created_records),
        transfer_group_id=transfer_group_id,
        transactions=serialize_transactions(
            portfolio_id,
            created_records,
            account_lookup,
        ),
    )
