from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import LedgerPostingListResponse, LedgerPostingListSummary, LedgerPostingRecord
from portfolio_app.services.fact_currency import PortfolioFactCurrencyError
from portfolio_app.services.ledger import (
    LedgerDataIntegrityError,
    list_ledger_postings,
    summarize_ledger_postings,
)
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
    instrument_id: str | None = None,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> LedgerPostingListResponse:
    try:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        accounts = list_accounts(portfolio_id)
        account_cost_methods = {
            str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
            for account in accounts
            if account.get("account_type") == "securities_account"
        }
        account_currency_map = {
            str(account.get("account_id") or ""): str(account.get("currency") or "")
            for account in accounts
        }
        postings = list_ledger_postings(
            portfolio_id,
            list_transactions(portfolio_id),
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            account_id=account_id,
            transaction_id=transaction_id,
            instrument_id=instrument_id,
            start_date=start_date,
            end_date=end_date,
        )
    except (PortfolioFactCurrencyError, LedgerDataIntegrityError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _build_response(portfolio_id, postings)


@router.get(
    "/{portfolio_id}/transactions/{transaction_id}/ledger-postings",
    response_model=LedgerPostingListResponse,
)
def list_transaction_ledger_postings(
    portfolio_id: str,
    transaction_id: str,
) -> LedgerPostingListResponse:
    try:
        if get_portfolio(portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        accounts = list_accounts(portfolio_id)
        account_cost_methods = {
            str(account.get("account_id") or ""): str(account.get("cost_basis_method") or "fifo")
            for account in accounts
            if account.get("account_type") == "securities_account"
        }
        account_currency_map = {
            str(account.get("account_id") or ""): str(account.get("currency") or "")
            for account in accounts
        }
        postings = list_ledger_postings(
            portfolio_id,
            list_transactions(portfolio_id),
            account_cost_methods=account_cost_methods,
            account_currency_map=account_currency_map,
            transaction_id=transaction_id,
        )
    except (PortfolioFactCurrencyError, LedgerDataIntegrityError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not postings:
        raise HTTPException(status_code=404, detail="Ledger postings not found for transaction")
    return _build_response(portfolio_id, postings)
