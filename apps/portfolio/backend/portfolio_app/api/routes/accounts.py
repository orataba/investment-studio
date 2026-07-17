from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from portfolio_app.api.assemblers import serialize_transaction, summarize_transactions
from portfolio_app.api.contracts import (
    AccountCreateRequest,
    AccountListResponse,
    AccountRecord,
    AccountPositionRecord,
    AccountUpdateRequest,
    AccountsWorkspaceResponse,
    AccountWorkspaceAccount,
    DerivationBoundaryStatus,
    LedgerPostingRecord,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.portfolio_store import (
    create_account,
    get_account,
    get_portfolio,
    list_accounts,
    list_transactions,
    update_account,
)


router = APIRouter()


def _transactions_booked_to_account(
    transactions: list[dict[str, object]],
    account_id: str,
) -> list[dict[str, object]]:
    return [
        transaction
        for transaction in transactions
        if str(transaction.get("account_id") or "") == account_id
        or (
            str(transaction.get("transaction_type") or "") == "fx_conversion"
            and str(transaction.get("counterparty_account_id") or "") == account_id
        )
    ]


def _validate_default_settlement_account(
    *,
    portfolio_id: str,
    settlement_account_id: str | None,
    currency: str,
) -> None:
    if not settlement_account_id:
        return
    settlement_account = get_account(portfolio_id, settlement_account_id)
    if settlement_account is None:
        raise HTTPException(status_code=400, detail="Default settlement cash account not found.")
    if settlement_account.get("account_type") != "deposit_account":
        raise HTTPException(status_code=400, detail="Default settlement cash account must be deposit_account.")
    if str(settlement_account.get("currency") or "").upper() != currency.upper():
        raise HTTPException(status_code=400, detail="Settlement cash mapping must use the same currency.")


@router.get("/{portfolio_id}/accounts", response_model=AccountListResponse)
def list_account_records(portfolio_id: str) -> AccountListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    return AccountListResponse(
        portfolio_id=portfolio_id,
        accounts=[AccountRecord.model_validate(item) for item in list_accounts(portfolio_id)],
    )


@router.post("/{portfolio_id}/accounts", response_model=AccountRecord)
def create_account_record(
    portfolio_id: str,
    payload: AccountCreateRequest,
) -> AccountRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    settlement_account_id = payload.default_settlement_cash_account_id
    if payload.account_type == "securities_account" and settlement_account_id:
        _validate_default_settlement_account(
            portfolio_id=portfolio_id,
            settlement_account_id=settlement_account_id,
            currency=payload.currency,
        )

    record = create_account(
        portfolio_id=portfolio_id,
        account_name=payload.account_name,
        account_type=payload.account_type,
        currency=payload.currency,
        institution=payload.institution,
        default_settlement_cash_account_id=settlement_account_id,
        cost_basis_method=payload.cost_basis_method,
        allowed_instrument_types=payload.allowed_instrument_types,
        opened_at=payload.opened_at,
        closed_at=payload.closed_at,
        status=payload.status,
    )
    return AccountRecord.model_validate(record)


@router.patch("/{portfolio_id}/accounts/{account_id}", response_model=AccountRecord)
def update_account_record(
    portfolio_id: str,
    account_id: str,
    payload: AccountUpdateRequest,
) -> AccountRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    existing_account = get_account(portfolio_id, account_id)
    if existing_account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    account_type = str(existing_account.get("account_type") or "")
    account_name = (
        payload.account_name.strip()
        if payload.account_name is not None
        else str(existing_account.get("account_name") or "")
    )
    if not account_name:
        raise HTTPException(status_code=400, detail="Account name is required.")

    institution = payload.institution if payload.institution is not None else existing_account.get("institution")
    opened_at = payload.opened_at if "opened_at" in payload.model_fields_set else existing_account.get("opened_at")
    closed_at = payload.closed_at if "closed_at" in payload.model_fields_set else existing_account.get("closed_at")
    status = payload.status if payload.status is not None else str(existing_account.get("status") or "active")

    if account_type == "deposit_account":
        if payload.default_settlement_cash_account_id is not None:
            raise HTTPException(status_code=400, detail="deposit_account must not carry default_settlement_cash_account_id.")
        if payload.cost_basis_method is not None:
            raise HTTPException(status_code=400, detail="deposit_account must not carry cost_basis_method.")
        if payload.allowed_instrument_types is not None:
            raise HTTPException(status_code=400, detail="deposit_account must not carry allowed_instrument_types.")
        settlement_account_id = None
        cost_basis_method = None
        allowed_instrument_types = None
    else:
        settlement_account_id = (
            payload.default_settlement_cash_account_id
            if "default_settlement_cash_account_id" in payload.model_fields_set
            else existing_account.get("default_settlement_cash_account_id")
        )
        _validate_default_settlement_account(
            portfolio_id=portfolio_id,
            settlement_account_id=settlement_account_id,
            currency=str(existing_account.get("currency") or ""),
        )
        current_cost_basis_method = str(existing_account.get("cost_basis_method") or "fifo")
        cost_basis_method = payload.cost_basis_method or current_cost_basis_method
        allowed_instrument_types = (
            payload.allowed_instrument_types
            if "allowed_instrument_types" in payload.model_fields_set
            else existing_account.get("allowed_instrument_types")
        )

    record = update_account(
        portfolio_id=portfolio_id,
        account_id=account_id,
        account_name=account_name,
        institution=institution if isinstance(institution, str) else None,
        default_settlement_cash_account_id=settlement_account_id if isinstance(settlement_account_id, str) else None,
        cost_basis_method=cost_basis_method,
        allowed_instrument_types=allowed_instrument_types if isinstance(allowed_instrument_types, list) else None,
        opened_at=opened_at if isinstance(opened_at, date) else date.fromisoformat(str(opened_at)) if opened_at else None,
        closed_at=closed_at if isinstance(closed_at, date) else date.fromisoformat(str(closed_at)) if closed_at else None,
        status=str(status or "active"),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountRecord.model_validate(record)


@router.get("/{portfolio_id}/accounts/workspace", response_model=AccountsWorkspaceResponse)
def get_accounts_workspace(
    portfolio_id: str,
    account_id: str | None = None,
    as_of_date: date | None = None,
) -> AccountsWorkspaceResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    if account_id and not any(item["account_id"] == account_id for item in accounts):
        raise HTTPException(status_code=404, detail="Account not found")

    portfolio_as_of_date = (
        date.fromisoformat(str(portfolio.get("as_of_date")))
        if portfolio.get("as_of_date")
        else None
    )
    resolved_as_of_date = as_of_date or portfolio_as_of_date or date.today()
    transactions = list_transactions(portfolio_id, end_date=resolved_as_of_date)
    try:
        workspace = build_account_workspace(
            portfolio_id,
            accounts,
            transactions,
            selected_account_id=account_id,
            base_currency=str(portfolio.get("base_currency") or "USD"),
            as_of_date=resolved_as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    selected_account_id = str(workspace.get("selected_account_id") or "") or None
    linked_transactions_raw = (
        _transactions_booked_to_account(transactions, selected_account_id)
        if selected_account_id
        else []
    )
    account_lookup = {str(item["account_id"]): item for item in accounts}
    return AccountsWorkspaceResponse(
        portfolio_id=portfolio_id,
        base_currency=workspace["base_currency"],
        summary=workspace["summary"],
        derivation_boundary=DerivationBoundaryStatus.model_validate(workspace["derivation_boundary"]),
        selected_account_id=selected_account_id,
        accounts=[AccountWorkspaceAccount.model_validate(item) for item in workspace["accounts"]],
        ledger_postings=[LedgerPostingRecord.model_validate(item) for item in workspace["ledger_postings"]],
        positions=[AccountPositionRecord.model_validate(item) for item in workspace["positions"]],
        linked_transactions_summary=summarize_transactions(linked_transactions_raw) if selected_account_id else None,
        linked_transactions=[
            serialize_transaction(portfolio_id, item, account_lookup) for item in linked_transactions_raw
        ],
    )
