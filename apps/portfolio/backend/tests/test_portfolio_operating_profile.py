from __future__ import annotations

from copy import deepcopy

import pytest

from portfolio_app.db.models import (
    AllocationResearchSettingsRecordModel,
    PortfolioRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import portfolio_store
from tests.store_fixture import TEST_PORTFOLIO_STORE


def test_portfolio_store_requires_explicit_operating_profile() -> None:
    payload = deepcopy(TEST_PORTFOLIO_STORE)
    payload["portfolios"][0].pop("operating_profile")

    with pytest.raises(
        ValueError,
        match="operating_profile must be one of",
    ):
        portfolio_store._normalize_store(payload)


def test_portfolio_model_has_no_operating_profile_default() -> None:
    column = PortfolioRecordModel.__table__.c.operating_profile

    assert column.nullable is False
    assert column.default is None
    assert column.server_default is None


def test_store_create_update_read_and_copy_round_trip_operating_profile() -> None:
    created = portfolio_store.create_portfolio(
        "Operating Profile Store",
        base_currency="USD",
        operating_profile="external_etf_rotation",
    )
    portfolio_id = str(created["portfolio_id"])
    assert created["operating_profile"] == "external_etf_rotation"
    assert portfolio_store.get_portfolio(portfolio_id) == created

    updated = portfolio_store.update_portfolio_operating_profile(
        portfolio_id,
        operating_profile="standard_taxonomy",
    )
    assert updated is not None
    assert updated["operating_profile"] == "standard_taxonomy"

    copied = portfolio_store.copy_portfolio(portfolio_id)
    assert copied is not None
    assert copied["operating_profile"] == "standard_taxonomy"


def test_create_requires_and_round_trips_explicit_operating_profile(client) -> None:
    missing = client.post(
        "/api/portfolios",
        json={"name": "Missing Profile", "base_currency": "USD"},
    )
    invalid = client.post(
        "/api/portfolios",
        json={
            "name": "Invalid Profile",
            "base_currency": "USD",
            "operating_profile": "allocation_managed",
        },
    )
    created = client.post(
        "/api/portfolios",
        json={
            "name": "External ETF Live",
            "base_currency": "CNY",
            "operating_profile": "external_etf_rotation",
        },
    )

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert created.status_code == 200
    created_record = created.json()
    assert created_record["operating_profile"] == "external_etf_rotation"

    listing = client.get("/api/portfolios")
    assert listing.status_code == 200
    listed_record = next(
        row
        for row in listing.json()
        if row["portfolio_id"] == created_record["portfolio_id"]
    )
    assert listed_record["operating_profile"] == "external_etf_rotation"


def test_update_and_copy_preserve_operating_profile_and_other_portfolio_facts(
    client,
) -> None:
    created = client.post(
        "/api/portfolios",
        json={
            "name": "Profile Update Guard",
            "base_currency": "HKD",
            "operating_profile": "standard_taxonomy",
        },
    )
    assert created.status_code == 200
    before = created.json()

    missing = client.patch(f"/api/portfolios/{before['portfolio_id']}", json={})
    invalid = client.patch(
        f"/api/portfolios/{before['portfolio_id']}",
        json={"operating_profile": "taxonomy_optional"},
    )
    updated = client.patch(
        f"/api/portfolios/{before['portfolio_id']}",
        json={"operating_profile": "external_etf_rotation"},
    )

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert updated.status_code == 200
    after = updated.json()
    assert after["operating_profile"] == "external_etf_rotation"
    for field_name in (
        "portfolio_id",
        "portfolio_name",
        "base_currency",
        "valuation_timezone",
        "valuation_cutoff_policy",
        "sort_order",
        "lifecycle_status",
    ):
        assert after[field_name] == before[field_name]

    copied = client.post(f"/api/portfolios/{before['portfolio_id']}/copy")
    assert copied.status_code == 200
    assert copied.json()["operating_profile"] == "external_etf_rotation"


def test_update_operating_profile_returns_not_found(client) -> None:
    response = client.patch(
        "/api/portfolios/not-a-portfolio",
        json={"operating_profile": "standard_taxonomy"},
    )

    assert response.status_code == 404


def test_external_etf_rotation_rejects_allocation_research_without_side_effects(
    client,
) -> None:
    created = client.post(
        "/api/portfolios",
        json={
            "name": "External ETF Capability Boundary",
            "base_currency": "CNY",
            "operating_profile": "external_etf_rotation",
        },
    )
    assert created.status_code == 200
    portfolio_id = created.json()["portfolio_id"]

    workbench = client.get(
        f"/api/portfolios/{portfolio_id}/allocation-research/workbench"
    )
    run = client.post(
        f"/api/portfolios/{portfolio_id}/allocation-research/runs",
        json={"requested_by": "pytest"},
    )

    for response in (workbench, run):
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "operating_profile_capability_not_applicable",
            "capability": "allocation_research",
            "operating_profile": "external_etf_rotation",
        }

    session_factory = get_session_factory()
    with session_factory() as session:
        assert (
            session.get(AllocationResearchSettingsRecordModel, portfolio_id)
            is None
        )
