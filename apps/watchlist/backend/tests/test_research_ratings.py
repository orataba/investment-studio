from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


def _add_research_instrument(client: TestClient) -> str:
    watchlist_response = client.post(
        "/api/watchlists",
        json={"name": "Research Rating", "description": None},
    )
    assert watchlist_response.status_code == 200
    watchlist_id = watchlist_response.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200
    return watchlist_id


def _rating_request(
    *,
    rating: int | None,
    rationale: str,
    expected_revision_id: str | None,
    confidence: str = "high",
) -> dict[str, object]:
    return {
        "rating": rating,
        "confidence": confidence,
        "rationale": rationale,
        "as_of_date": date.today().isoformat(),
        "next_review_date": (date.today() + timedelta(days=90)).isoformat(),
        "author": "test-analyst",
        "expected_current_revision_id": expected_revision_id,
    }


def test_market_recalculation_never_creates_a_research_rating(client: TestClient) -> None:
    _add_research_instrument(client)

    initial = client.get("/api/instruments/sxv264/research-rating")
    assert initial.status_code == 200
    assert initial.json()["rating"] is None
    assert initial.json()["rating_revision_id"] is None

    recalc = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={"job_type": "all"},
    )
    assert recalc.status_code == 200

    current = client.get("/api/instruments/sxv264/research-rating")
    assert current.status_code == 200
    assert current.json()["rating"] is None
    assert current.json()["rating_revision_id"] is None

    summary = client.get("/api/instruments/sxv264/summary")
    assert summary.status_code == 200
    assert summary.json()["research_rating"] is None


@pytest.mark.parametrize(
    "deprecated_payload",
    [
        {"manual_rating": 5},
        {"overview": {"current_view": "Constructive"}},
    ],
)
def test_research_profile_rejects_removed_rating_fields(
    client: TestClient,
    deprecated_payload: dict[str, object],
) -> None:
    _add_research_instrument(client)

    response = client.put(
        "/api/instruments/sxv264/research",
        json={"payload": deprecated_payload, "updated_by": "test-analyst"},
    )

    assert response.status_code == 422


def test_research_rating_revisions_are_append_only_and_materialized_consistently(
    client: TestClient,
) -> None:
    watchlist_id = _add_research_instrument(client)

    first = client.put(
        "/api/instruments/sxv264/research-rating",
        json=_rating_request(
            rating=4,
            rationale="Repeatable process with a capacity question still under review.",
            expected_revision_id=None,
        ),
    )
    assert first.status_code == 200
    first_payload = first.json()
    first_revision_id = first_payload["rating_revision_id"]
    assert first_payload["rating"] == 4
    assert first_payload["confidence"] == "high"
    assert first_payload["previous_rating_revision_id"] is None
    assert first_payload["is_current"] is True

    summary = client.get("/api/instruments/sxv264/summary")
    assert summary.status_code == 200
    assert summary.json()["research_rating"]["rating_revision_id"] == first_revision_id
    assert summary.json()["research_rating"]["rating"] == 4

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "research_rating"],
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert screener.json()["rows"][0]["research_rating"] == 4

    second = client.put(
        "/api/instruments/sxv264/research-rating",
        json=_rating_request(
            rating=3,
            confidence="medium",
            rationale="Capacity evidence weakened the original conviction.",
            expected_revision_id=first_revision_id,
        ),
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["previous_rating_revision_id"] == first_revision_id
    assert second_payload["rating"] == 3

    history = client.get("/api/instruments/sxv264/research-ratings")
    assert history.status_code == 200
    items = history.json()["items"]
    assert [item["rating"] for item in items] == [3, 4]
    assert [item["is_current"] for item in items] == [True, False]
    assert items[1]["superseded_at"] is not None

    cleared = client.put(
        "/api/instruments/sxv264/research-rating",
        json=_rating_request(
            rating=None,
            confidence="low",
            rationale="Rating cleared pending a manager-change review.",
            expected_revision_id=second_payload["rating_revision_id"],
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["rating"] is None
    assert cleared.json()["previous_rating_revision_id"] == second_payload["rating_revision_id"]


@pytest.mark.parametrize("invalid_rating", [0, 6, 9, True, "5"])
def test_research_rating_rejects_invalid_values(
    client: TestClient,
    invalid_rating: object,
) -> None:
    _add_research_instrument(client)
    payload = _rating_request(
        rating=4,
        rationale="Valid rationale.",
        expected_revision_id=None,
    )
    payload["rating"] = invalid_rating

    response = client.put("/api/instruments/sxv264/research-rating", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rationale", ""),
        ("author", ""),
        ("confidence", "unassessed"),
        ("confidence", "unknown"),
    ],
)
def test_research_rating_requires_governance_fields(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    _add_research_instrument(client)
    payload = _rating_request(
        rating=4,
        rationale="Valid rationale.",
        expected_revision_id=None,
    )
    payload[field] = value

    response = client.put("/api/instruments/sxv264/research-rating", json=payload)

    assert response.status_code == 422


def test_research_rating_uses_compare_and_swap_for_concurrent_edits(
    client: TestClient,
) -> None:
    _add_research_instrument(client)
    first = client.put(
        "/api/instruments/sxv264/research-rating",
        json=_rating_request(
            rating=4,
            rationale="Initial assessment.",
            expected_revision_id=None,
        ),
    )
    assert first.status_code == 200

    stale_update = client.put(
        "/api/instruments/sxv264/research-rating",
        json=_rating_request(
            rating=5,
            rationale="This editor did not load the current revision.",
            expected_revision_id=None,
        ),
    )

    assert stale_update.status_code == 409
    detail = stale_update.json()["detail"]
    assert detail["code"] == "research_rating_revision_conflict"
    assert detail["actual_current_revision_id"] == first.json()["rating_revision_id"]
