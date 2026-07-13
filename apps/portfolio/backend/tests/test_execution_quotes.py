from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.db.session import get_session_factory
from portfolio_ops_instrument_core import instrument_store as shared_store


INSTRUMENT_ID = "equity-us-abbv"


def _policy(*, trading: list[str]) -> dict[str, list[str]]:
    return {
        "trading": trading,
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    }


def _set_policy(*, trading: list[str]) -> None:
    updated = shared_store.upsert_quote_selection_policy(
        get_session_factory(),
        instrument_id=INSTRUMENT_ID,
        quote_selection_policy=_policy(trading=trading),
    )
    assert updated is not None


def _upsert_point(
    *,
    quote_basis: str,
    as_of_date: date,
    value: str,
    source_ref: str,
    status: str = "complete",
) -> None:
    changed_count = shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=INSTRUMENT_ID,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": quote_basis,
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "source_ref": source_ref,
                "status": status,
            }
        ],
    )
    assert changed_count == 1


def _get_quote(client, *, as_of_date: date):
    return client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={
            "instrument_id": INSTRUMENT_ID,
            "as_of_date": as_of_date.isoformat(),
        },
    )


def test_execution_quote_endpoint_uses_policy_selected_unadjusted_series(client):
    requested_date = date(2026, 3, 27)
    _set_policy(trading=["last", "close"])
    _upsert_point(
        quote_basis="adjusted_close",
        as_of_date=requested_date,
        value="204.25",
        source_ref="test:adjusted",
    )
    _upsert_point(
        quote_basis="close",
        as_of_date=requested_date,
        value="206.47",
        source_ref="test:close",
    )

    response = _get_quote(client, as_of_date=requested_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == "portfolio-ops"
    assert payload["instrument_id"] == INSTRUMENT_ID
    assert payload["selection_role"] == "trading"
    assert payload["value"] == pytest.approx(206.47)
    assert payload["quote_date"] == requested_date.isoformat()
    assert payload["quote_basis"] == "close"
    assert payload["currency"] == "USD"
    assert payload["source_ref"] == "test:close"
    assert payload["source_status"] == "complete"
    assert payload["status"] == "complete"
    assert payload["resolution_status"] == "resolved"
    assert payload["freshness_status"] == "current"
    assert payload["reliability_status"] == "reliable"
    assert payload["stale"] is False
    assert payload["carry_forward"] is False
    assert payload["quote_series_id"]
    assert payload["observation_id"]
    assert payload["revision_id"]
    assert payload["revision_number"] == 1
    assert payload["payload_hash"]
    assert payload["calculation_dependency"]["fingerprint"]


def test_execution_quote_endpoint_does_not_cross_policy_series_boundary(client):
    requested_date = date(2026, 3, 27)
    _set_policy(trading=["last", "close"])
    _upsert_point(
        quote_basis="last",
        as_of_date=date(2026, 3, 26),
        value="205.00",
        source_ref="test:last",
    )
    _upsert_point(
        quote_basis="close",
        as_of_date=requested_date,
        value="206.47",
        source_ref="test:close",
    )

    response = _get_quote(client, as_of_date=requested_date)

    assert response.status_code == 200
    payload = response.json()
    # The higher-priority existing `last` identity is locked.  Exact-only
    # trading freshness makes its prior-date observation unavailable; the
    # consumer must not splice in the same-day `close` series.
    assert payload["value"] is None
    assert payload["quote_basis"] == "last"
    assert payload["quote_date"] == "2026-03-26"
    assert payload["source_ref"] == "test:last"
    assert payload["source_status"] == "complete"
    assert payload["resolution_status"] == "unavailable"
    assert payload["freshness_status"] == "late"
    assert payload["reliability_status"] == "unavailable"
    assert payload["stale"] is True
    assert payload["carry_forward"] is False
    assert payload["age_days"] == 1
    assert payload["reason_codes"] == [
        "late_observation",
        "freshness_limit_exceeded",
    ]


def test_execution_quote_endpoint_does_not_propose_prior_close(client):
    quote_date = date(2026, 3, 27)
    requested_date = date(2026, 3, 30)
    _set_policy(trading=["close"])
    _upsert_point(
        quote_basis="close",
        as_of_date=quote_date,
        value="206.47",
        source_ref="test:close",
    )

    response = _get_quote(client, as_of_date=requested_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] is None
    assert payload["quote_date"] == quote_date.isoformat()
    assert payload["quote_basis"] == "close"
    assert payload["source_status"] == "complete"
    assert payload["resolution_status"] == "unavailable"
    assert payload["freshness_status"] == "late"
    assert payload["stale"] is True
    assert payload["age_days"] == 3


@pytest.mark.parametrize("bad_status", ["partial", "rejected"])
def test_execution_quote_endpoint_preserves_current_bad_state_lineage(
    client,
    bad_status: str,
):
    requested_date = date(2026, 3, 27)
    _set_policy(trading=["close"])
    _upsert_point(
        quote_basis="close",
        as_of_date=requested_date,
        value="206.47",
        source_ref=f"test:{bad_status}",
        status=bad_status,
    )

    response = _get_quote(client, as_of_date=requested_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] is None
    assert payload["quote_date"] == requested_date.isoformat()
    assert payload["source_status"] == bad_status
    assert payload["status"] == ("partial" if bad_status == "partial" else "unavailable")
    assert payload["resolution_status"] == "unavailable"
    assert payload["freshness_status"] == "current"
    assert payload["reliability_status"] == "unavailable"
    assert payload["revision_id"]
    assert payload["payload_hash"]
    assert payload["reason_codes"] == [
        {
            "partial": "partial_series",
            "rejected": "rejected_observation",
            "withdrawn": "withdrawn_observation",
        }[bad_status]
    ]


def test_execution_quote_endpoint_returns_unavailable_for_missing_trading_series(client):
    requested_date = date(2026, 3, 27)
    _set_policy(trading=["last"])

    response = _get_quote(client, as_of_date=requested_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] is None
    assert payload["quote_date"] is None
    assert payload["quote_basis"] is None
    assert payload["metric_family"] is None
    assert payload["source_ref"] is None
    assert payload["source_status"] is None
    assert payload["status"] == "unavailable"
    assert payload["resolution_status"] == "unavailable"
    assert payload["freshness_status"] == "missing"
    assert payload["stale"] is False
    assert payload["reason_codes"] == ["missing_quote_series"]


def test_execution_quote_endpoint_returns_not_found_for_unknown_instrument(client):
    response = client.get(
        "/api/portfolios/portfolio-ops/transactions/execution-quote",
        params={"instrument_id": "missing", "as_of_date": "2026-03-27"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Instrument not found in shared registry."
