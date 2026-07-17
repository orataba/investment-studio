from __future__ import annotations

from datetime import date

from fastapi import BackgroundTasks

from platform_app.api.routes import instruments as routes


def test_fund_nav_admin_routes_expose_only_the_current_contract() -> None:
    paths = {
        str(getattr(route, "path"))
        for route in routes.router.routes
        if getattr(route, "path", None)
    }

    assert {
        "/{instrument_id}/fund-nav-actions",
        "/{instrument_id}/fund-nav-actions/{action_id}/revisions",
        "/{instrument_id}/fund-nav-events/{fund_nav_event_id}/reinvestment-evidence",
        "/{instrument_id}/fund-nav-reinvestment-evidence/{evidence_id}/revisions",
        "/{instrument_id}/nav-action-candidates/{candidate_id}/confirm",
        "/{instrument_id}/nav-action-candidates/{candidate_id}/resume-confirmation",
    } <= paths
    assert "/{instrument_id}/fund-nav-events" not in paths
    assert (
        "/{instrument_id}/nav-action-candidates/{candidate_id}/confirm-event"
        not in paths
    )


def test_changed_nav_mutation_queues_the_exact_dirty_boundary(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    class ResponseContract:
        @classmethod
        def model_validate(cls, payload: dict[str, object]) -> dict[str, object]:
            return payload

    monkeypatch.setattr(routes, "PlatformFundNavMutationResponse", ResponseContract)
    monkeypatch.setattr(
        routes,
        "queue_market_data_downstream_refresh",
        lambda _tasks, **kwargs: captured.append(kwargs),
    )

    result = {
        "changed": True,
        "dirty_from": "2026-06-30",
    }
    response = routes._fund_nav_mutation_response(
        instrument_id="fund-1",
        result=result,
        background_tasks=BackgroundTasks(),
    )

    assert response == result
    assert captured == [
        {
            "instrument_ids": ["fund-1"],
            "dirty_from": date(2026, 6, 30),
        }
    ]


def test_idempotent_nav_mutation_does_not_queue_duplicate_recalculation(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    class ResponseContract:
        @classmethod
        def model_validate(cls, payload: dict[str, object]) -> dict[str, object]:
            return payload

    monkeypatch.setattr(routes, "PlatformFundNavMutationResponse", ResponseContract)
    monkeypatch.setattr(
        routes,
        "queue_market_data_downstream_refresh",
        lambda _tasks, **kwargs: captured.append(kwargs),
    )

    result = {"changed": False, "dirty_from": None}
    routes._fund_nav_mutation_response(
        instrument_id="fund-1",
        result=result,
        background_tasks=BackgroundTasks(),
    )

    assert captured == []
