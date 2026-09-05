from __future__ import annotations

from datetime import date


from studio_data.commands import instruments as routes
from studio_data.cli import COMMANDS


def test_data_maintenance_is_cli_only() -> None:
    assert {("nav", "action-create"), ("nav", "action-revise"),
            ("nav", "evidence-create"), ("nav", "evidence-revise"),
            ("nav", "candidate-confirm"), ("nav", "candidate-resume")} <= COMMANDS.keys()

def test_changed_nav_mutation_queues_the_exact_dirty_boundary(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    class ResponseContract:
        @classmethod
        def model_validate(cls, payload: dict[str, object]) -> dict[str, object]:
            return payload

    monkeypatch.setattr(routes, "StudioFundNavMutationResponse", ResponseContract)
    monkeypatch.setattr(
        routes,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: captured.append(kwargs),
    )

    result = {
        "changed": True,
        "dirty_from": "2026-06-30",
    }
    response = routes._fund_nav_mutation_response(
        instrument_id="fund-1",
        result=result,
    )

    assert response == result
    assert captured == [
        {
            "instrument_ids": ["fund-1"],
            "dirty_from": date(2026, 6, 30),
            "raise_on_error": True,
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

    monkeypatch.setattr(routes, "StudioFundNavMutationResponse", ResponseContract)
    monkeypatch.setattr(
        routes,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: captured.append(kwargs),
    )

    result = {"changed": False, "dirty_from": None}
    routes._fund_nav_mutation_response(
        instrument_id="fund-1",
        result=result,
    )

    assert captured == []
