from __future__ import annotations

from copy import deepcopy

import pytest

from portfolio_app.services import execution_quotes


def _point(
    *,
    quote_basis: str,
    as_of_date: str,
    value: str,
    provider: str,
    status: str = "complete",
) -> dict[str, object]:
    return {
        "instrument_id": "159516-sz",
        "metric_family": "price",
        "quote_basis": quote_basis,
        "as_of_date": as_of_date,
        "value": value,
        "currency": "CNY",
        "provider": provider,
        "status": status,
    }


def _instrument_detail(
    *,
    policy: dict[str, list[str]],
    market_data: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "instrument_id": "159516-sz",
        "instrument_name": "半导体设备ETF国泰",
        "instrument_type": "etf",
        "currency": "CNY",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "159516.SZ",
                "is_primary": True,
            }
        ],
        "quote_selection_policy": policy,
        "market_data": market_data,
    }


def _install_detail(monkeypatch: pytest.MonkeyPatch, detail: dict[str, object]) -> None:
    monkeypatch.setattr(
        execution_quotes,
        "get_registry_instrument_detail",
        lambda instrument_id: deepcopy(detail) if instrument_id == "159516-sz" else None,
    )


def test_execution_quote_endpoint_prefers_raw_close_over_adjusted_close(client, monkeypatch):
    _install_detail(
        monkeypatch,
        _instrument_detail(
            policy={
                "trading": ["last", "adjusted_close", "close"],
                "valuation": ["close", "last"],
                "total_return": ["adjusted_close", "close"],
                "chart": ["adjusted_close", "close"],
            },
            market_data=[
                _point(
                    quote_basis="adjusted_close",
                    as_of_date="2026-03-27",
                    value="0.4152179894444583",
                    provider="tushare:fund_adj:qfq:latest_factor=3.9979",
                ),
                _point(
                    quote_basis="close",
                    as_of_date="2026-03-27",
                    value="1.66",
                    provider="tushare:fund_daily",
                ),
            ],
        ),
    )

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "159516-sz", "as_of_date": "2026-03-27"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "portfolio_id": "portfolio-ops",
        "instrument_id": "159516-sz",
        "requested_as_of_date": "2026-03-27",
        "selection_role": "trading",
        "value": pytest.approx(1.66),
        "quote_date": "2026-03-27",
        "quote_basis": "close",
        "metric_family": "price",
        "currency": "CNY",
        "provider": "tushare:fund_daily",
        "status": "complete",
        "stale": False,
    }


def test_execution_quote_endpoint_marks_prior_unadjusted_quote_stale(client, monkeypatch):
    _install_detail(
        monkeypatch,
        _instrument_detail(
            policy={"trading": ["close"], "valuation": ["close"]},
            market_data=[
                _point(
                    quote_basis="close",
                    as_of_date="2026-03-27",
                    value="1.66",
                    provider="tushare:fund_daily",
                )
            ],
        ),
    )

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "159516-sz", "as_of_date": "2026-03-30"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] == pytest.approx(1.66)
    assert payload["quote_date"] == "2026-03-27"
    assert payload["quote_basis"] == "close"
    assert payload["stale"] is True


def test_execution_quote_endpoint_returns_unavailable_instead_of_adjusted_fallback(client, monkeypatch):
    _install_detail(
        monkeypatch,
        _instrument_detail(
            policy={
                "trading": ["adjusted_close"],
                "valuation": ["total_return_nav", "cumulative_nav"],
            },
            market_data=[
                _point(
                    quote_basis="adjusted_close",
                    as_of_date="2026-03-27",
                    value="0.4152179894444583",
                    provider="tushare:fund_adj:qfq:latest_factor=3.9979",
                )
            ],
        ),
    )

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "159516-sz", "as_of_date": "2026-03-27"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] is None
    assert payload["quote_date"] is None
    assert payload["quote_basis"] is None
    assert payload["metric_family"] is None
    assert payload["provider"] is None
    assert payload["status"] == "unavailable"
    assert payload["stale"] is False


def test_execution_quote_endpoint_returns_not_found_for_unknown_instrument(client, monkeypatch):
    monkeypatch.setattr(execution_quotes, "get_registry_instrument_detail", lambda _instrument_id: None)

    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "missing", "as_of_date": "2026-03-27"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Instrument not found in shared registry."
