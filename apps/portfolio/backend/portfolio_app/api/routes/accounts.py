from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from portfolio_app.api.assemblers import serialize_transaction, summarize_transactions
from portfolio_app.api.contracts import (
    AccountCreateRequest,
    AccountListResponse,
    AccountRecord,
    AccountPositionRecord,
    AccountsWorkspaceResponse,
    AccountWorkspaceAccount,
    DerivationBoundaryStatus,
    LedgerPostingRecord,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.portfolio_store import create_account, get_account, get_portfolio, list_accounts, list_transactions


router = APIRouter()


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
        settlement_account = get_account(portfolio_id, settlement_account_id)
        if settlement_account is None:
            raise HTTPException(status_code=400, detail="Default settlement cash account not found.")
        if settlement_account.get("account_type") != "deposit_account":
            raise HTTPException(status_code=400, detail="Default settlement cash account must be deposit_account.")
        if str(settlement_account.get("currency") or "").upper() != payload.currency.upper():
            raise HTTPException(status_code=400, detail="Settlement cash mapping must use the same currency.")

    record = create_account(
        portfolio_id=portfolio_id,
        account_name=payload.account_name,
        account_type=payload.account_type,
        currency=payload.currency,
        institution=payload.institution,
        default_settlement_cash_account_id=settlement_account_id,
        cost_basis_method=payload.cost_basis_method,
        allowed_asset_types=payload.allowed_asset_types,
        opened_at=payload.opened_at,
        closed_at=payload.closed_at,
        status=payload.status,
    )
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
    try:
        workspace = build_account_workspace(
            portfolio_id,
            accounts,
            list_transactions(portfolio_id),
            selected_account_id=account_id,
            base_currency=str(portfolio.get("base_currency") or "USD"),
            as_of_date=resolved_as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    selected_account_id = str(workspace.get("selected_account_id") or "") or None
    linked_transactions_raw = (
        list_transactions(
            portfolio_id,
            account_id=selected_account_id,
            end_date=resolved_as_of_date,
        )
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
