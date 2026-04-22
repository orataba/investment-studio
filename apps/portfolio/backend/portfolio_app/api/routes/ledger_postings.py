from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import LedgerPostingListResponse, LedgerPostingListSummary, LedgerPostingRecord
from portfolio_app.services.ledger import list_ledger_postings, summarize_ledger_postings
from portfolio_app.services.portfolio_store import get_portfolio, list_accounts, list_transactions


router = APIRouter()


def _build_response(portfolio_id: str, postings: list[dict[str, object]]) -> LedgerPostingListResponse:
    return LedgerPostingListResponse(
        portfolio_id=portfolio_id,
        summary=LedgerPostingListSummary.model_validate(summarize_ledger_postings(postings)),
        ledger_postings=[LedgerPostingRecord.model_validate(item) for item in postings],
    )


@router.get("/{portfolio_id}/ledger-postings", response_model=LedgerPostingListResponse)
def list_portfolio_ledger_postings(
    portfolio_id: str,
    account_id: str | None = None,
    transaction_id: str | None = None,
    asset_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> LedgerPostingListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in list_accounts(portfolio_id)
        if account.get("account_type") == "securities_account"
    }
    account_currency_map = {
        str(account.get("account_id") or ""): str(account.get("currency") or "")
        for account in list_accounts(portfolio_id)
    }
    postings = list_ledger_postings(
        portfolio_id,
        list_transactions(portfolio_id),
        account_cost_methods=account_cost_methods,
        account_currency_map=account_currency_map,
        account_id=account_id,
        transaction_id=transaction_id,
        asset_id=asset_id,
        start_date=start_date,
        end_date=end_date,
    )
    return _build_response(portfolio_id, postings)


@router.get(
    "/{portfolio_id}/transactions/{transaction_id}/ledger-postings",
    response_model=LedgerPostingListResponse,
)
def list_transaction_ledger_postings(
    portfolio_id: str,
    transaction_id: str,
) -> LedgerPostingListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    account_cost_methods = {
        str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
        for account in list_accounts(portfolio_id)
        if account.get("account_type") == "securities_account"
    }
    account_currency_map = {
        str(account.get("account_id") or ""): str(account.get("currency") or "")
        for account in list_accounts(portfolio_id)
    }
    postings = list_ledger_postings(
        portfolio_id,
        list_transactions(portfolio_id),
        account_cost_methods=account_cost_methods,
        account_currency_map=account_currency_map,
        transaction_id=transaction_id,
    )
    if not postings:
        raise HTTPException(status_code=404, detail="Ledger postings not found for transaction")
    return _build_response(portfolio_id, postings)
