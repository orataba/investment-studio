from __future__ import annotations

from portfolio_app.services import performance


def _trade(
    *,
    transaction_id: str,
    transaction_type: str,
    trade_date: str,
    quantity: float,
    transaction_sequence: int,
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "transaction_sequence": transaction_sequence,
        "portfolio_id": "portfolio-1",
        "transaction_type": transaction_type,
        "trade_date": trade_date,
        "trade_at": f"{trade_date}T09:30:00+08:00",
        "settlement_date": trade_date,
        "account_id": "account-1",
        "instrument_id": "equity-1",
        "instrument_ref": {
            "instrument_id": "equity-1",
            "instrument_type": "equity",
            "exchange_code": "XNYS",
            "currency": "CNY",
        },
        "quantity": quantity,
        "gross_amount": quantity * 10,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "CNY",
    }


def test_equity_without_detected_corporate_action_has_no_generic_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-06-01",
                "status": "confirmed",
            }
        ],
    )

    assert performance.corporate_action_quality_warnings(
        {"equity"},
        {"equity-1"},
        transactions=[
            _trade(
                transaction_id="buy-1",
                transaction_type="buy",
                trade_date="2026-05-01",
                quantity=100,
                transaction_sequence=1,
            )
        ],
    ) == []


def test_detected_corporate_action_warning_identifies_member_date_and_remedy(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-07-01",
                "status": "detected",
            }
        ],
    )

    warnings = performance.corporate_action_quality_warnings(
        {"equity"},
        {"equity-1"},
        transactions=[
            _trade(
                transaction_id="buy-1",
                transaction_type="buy",
                trade_date="2026-06-01",
                quantity=100,
                transaction_sequence=1,
            )
        ],
    )

    assert len(warnings) == 1
    assert "Corporate action review required" in warnings[0]
    assert "equity-1 effective 2026-07-01" in warnings[0]
    assert "Confirm the issuer, exchange, or depository ratio" in warnings[0]


def test_non_equity_instruments_do_not_query_corporate_actions(monkeypatch) -> None:
    def unexpected_query(_instrument_ids, **_kwargs):
        raise AssertionError("Corporate actions must not be queried for ineligible types.")

    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        unexpected_query,
    )

    assert (
        performance.corporate_action_quality_warnings(
            {"fund"},
            {"fund-1"},
            transactions=[
                _trade(
                    transaction_id="buy-1",
                    transaction_type="buy",
                    trade_date="2026-06-01",
                    quantity=100,
                    transaction_sequence=1,
                )
            ],
        )
        == []
    )


def test_detected_action_before_first_purchase_does_not_warn(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-07-01",
                "status": "detected",
            }
        ],
    )

    assert (
        performance.corporate_action_quality_warnings(
            {"equity"},
            {"equity-1"},
            transactions=[
                _trade(
                    transaction_id="buy-1",
                    transaction_type="buy",
                    trade_date="2026-07-02",
                    quantity=100,
                    transaction_sequence=1,
                )
            ],
        )
        == []
    )


def test_detected_action_after_position_is_closed_does_not_warn(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-07-10",
                "status": "detected",
            }
        ],
    )
    transactions = [
        _trade(
            transaction_id="buy-1",
            transaction_type="buy",
            trade_date="2026-07-01",
            quantity=100,
            transaction_sequence=1,
        ),
        _trade(
            transaction_id="sell-1",
            transaction_type="sell",
            trade_date="2026-07-05",
            quantity=100,
            transaction_sequence=2,
        ),
    ]

    assert (
        performance.corporate_action_quality_warnings(
            {"equity"},
            {"equity-1"},
            transactions=transactions,
        )
        == []
    )


def test_detected_action_uses_beginning_of_entitlement_day_position(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "record_date": "2026-07-10",
                "effective_date": "2026-07-11",
                "status": "detected",
            }
        ],
    )
    transactions = [
        _trade(
            transaction_id="buy-1",
            transaction_type="buy",
            trade_date="2026-07-01",
            quantity=100,
            transaction_sequence=1,
        ),
        _trade(
            transaction_id="sell-1",
            transaction_type="sell",
            trade_date="2026-07-10",
            quantity=100,
            transaction_sequence=2,
        ),
    ]

    warnings = performance.corporate_action_quality_warnings(
        {"equity"},
        {"equity-1"},
        transactions=transactions,
    )

    assert len(warnings) == 1
    assert "equity-1 effective 2026-07-11" in warnings[0]


def test_detected_action_ignores_same_day_purchase(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids, **_kwargs: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-07-10",
                "status": "detected",
            }
        ],
    )

    assert (
        performance.corporate_action_quality_warnings(
            {"equity"},
            {"equity-1"},
            transactions=[
                _trade(
                    transaction_id="buy-1",
                    transaction_type="buy",
                    trade_date="2026-07-10",
                    quantity=100,
                    transaction_sequence=1,
                )
            ],
        )
        == []
    )
