from __future__ import annotations

from datetime import date

from portfolio_app.db.session import get_session_factory
from portfolio_app.services.transaction_command_validator import (
    validate_prospective_transaction_history,
)


def _account(client, account_id: str) -> dict[str, object]:
    response = client.get("/api/portfolios/portfolio-ops/accounts")
    assert response.status_code == 200
    return next(
        item
        for item in response.json()["accounts"]
        if item["account_id"] == account_id
    )


def test_account_dates_cannot_invalidate_current_transaction_history(client) -> None:
    account_id = "broker-us-core"
    original = _account(client, account_id)

    response = client.patch(
        f"/api/portfolios/portfolio-ops/accounts/{account_id}",
        json={"opened_at": "2026-04-16"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "transaction_command_validation_failed"
    assert detail["validation_code"] == "event_build_failed"
    assert detail["reason_code"] == "account_contract_violation"
    assert _account(client, account_id)["opened_at"] == original["opened_at"]


def test_prospective_replay_boundary_includes_later_settlement_date(client) -> None:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-15",
            "settlement_date": "2026-05-02",
            "account_id": "cash-usd-main",
            "gross_amount": "100.00",
            "currency": "USD",
            "actor": {
                "actor_type": "user",
                "actor_id": "pm:history-guard-test",
                "display_name": "History Guard Test",
                "actor_source": "client_asserted",
            },
        },
    )
    assert response.status_code == 200, response.text

    session_factory = get_session_factory()
    with session_factory() as session:
        evidence = validate_prospective_transaction_history(
            session,
            portfolio_id="portfolio-ops",
        )

    assert evidence.effective_as_of == date(2026, 5, 2)
