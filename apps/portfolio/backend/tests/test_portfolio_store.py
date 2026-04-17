from __future__ import annotations

from app.services import portfolio_store


def test_reset_store_without_payload_leaves_store_empty() -> None:
    portfolio_store.reset_store()

    assert portfolio_store.list_portfolios() == []
    assert portfolio_store.list_accounts("yungu") == []
    assert portfolio_store.list_transactions("yungu") == []
