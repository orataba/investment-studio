from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from studio_data.db.base import Base
from studio_data.services import fund_nav_actions
from studio_data.services.fund_nav_action_candidates import (
    FundNavActionCandidateRepository,
)


ACTION = {
    "event_type": "cash_distribution",
    "announcement_date": "2026-06-03",
    "record_date": "2026-06-04",
    "effective_date": "2026-06-05",
    "payable_date": "2026-06-06",
    "sequence_order": None,
    "cash_per_unit": "0.1",
    "unit_ratio": None,
    "evidence_kind": "manual_verified",
    "source": "administrator_review",
    "external_event_id": "notice-2026-06-05",
    "provenance": {"notice_sha256": "a" * 64},
}
EVIDENCE = {
    "reinvestment_nav": "0.9",
    "evidence_kind": "manual_verified",
    "source": "administrator_review",
    "external_evidence_id": "reinvestment-2026-06-05",
    "provenance": {"notice_sha256": "b" * 64},
}


class _NoopCandidateRepository:
    def __init__(self, _session_factory: object) -> None:
        pass

    def project_current(self, **_kwargs: object) -> list[dict[str, object]]:
        return []


def _base_instrument() -> dict[str, object]:
    return {
        "instrument_id": "fund-a",
        "instrument_type": "public_fund",
        "currency": "CNY",
        "market_data_updated_at": "2026-07-16T00:00:00Z",
        "fund_nav_events": [],
        "fund_nav_event_revisions": [],
        "fund_nav_reinvestment_evidence": [],
        "fund_nav_reinvestment_evidence_revisions": [],
    }


def _install_publish_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    instrument: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    builder_views: list[dict[str, object]] = []
    publish_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        fund_nav_actions,
        "get_instrument",
        lambda _instrument_id: deepcopy(instrument),
    )
    monkeypatch.setattr(
        fund_nav_actions,
        "_load_all_durable_nav_source_rows",
        lambda **_kwargs: [],
    )

    def build(**kwargs: object) -> SimpleNamespace:
        view = deepcopy(kwargs["instrument"])
        builder_views.append(view)
        current_events = list(view.get("fund_nav_events", []))
        current_evidence = list(view.get("fund_nav_reinvestment_evidence", []))
        return SimpleNamespace(
            rows=[],
            projection_run={"projection": "test"},
            current_fund_nav_event_ids=[
                item["fund_nav_event_id"] for item in current_events
            ],
            current_fund_nav_reinvestment_evidence_ids=[
                item["fund_nav_reinvestment_evidence_id"]
                for item in current_evidence
            ],
            adjustment_factors=[],
            action_candidates=[],
        )

    def publish(**kwargs: object) -> dict[str, object]:
        publish_calls.append(deepcopy(kwargs))
        record = deepcopy(instrument)
        record["fund_nav_event_revisions"] = [
            *list(record.get("fund_nav_event_revisions", [])),
            *list(kwargs["event_revisions"]),
        ]
        record["fund_nav_reinvestment_evidence_revisions"] = [
            *list(record.get("fund_nav_reinvestment_evidence_revisions", [])),
            *list(kwargs["reinvestment_evidence_revisions"]),
        ]
        return {
            "record": record,
            "published_projection_run_id": "projection-test",
            "market_data_updated_at": "2026-07-16T01:00:00Z",
            "dirty_from": "2026-06-05",
            "changed": True,
        }

    monkeypatch.setattr(fund_nav_actions, "_build_fund_nav_publication", build)
    monkeypatch.setattr(fund_nav_actions, "publish_fund_nav_history", publish)
    monkeypatch.setattr(
        fund_nav_actions,
        "FundNavActionCandidateRepository",
        _NoopCandidateRepository,
    )
    monkeypatch.setattr(fund_nav_actions, "get_session_factory", lambda: object())
    return builder_views, publish_calls


def test_create_action_and_evidence_are_one_atomic_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=_base_instrument(),
    )

    result = fund_nav_actions.create_fund_nav_action(
        instrument_id="fund-a",
        action_payload=ACTION,
        reinvestment_evidence_payload=EVIDENCE,
        client_mutation_id="ui-create-1",
        recorded_by="data-operator@example.com",
        revision_reason="Verified provider distribution notice.",
    )

    assert result is not None
    assert result["changed"] is True
    assert result["dirty_from"] == "2026-06-05"
    assert len(publish_calls) == 1
    call = publish_calls[0]
    assert len(call["event_revisions"]) == 1
    assert len(call["reinvestment_evidence_revisions"]) == 1
    assert call["projection_run"]["created_by"] == "data-operator@example.com"
    event = call["event_revisions"][0]
    evidence = call["reinvestment_evidence_revisions"][0]
    assert event["revision_kind"] == "original"
    assert event["recorded_by"] == "data-operator@example.com"
    assert evidence["fund_nav_event_id"] == event["fund_nav_event_id"]
    assert call["current_fund_nav_event_ids"] == [event["fund_nav_event_id"]]
    assert call["current_fund_nav_reinvestment_evidence_ids"] == [
        evidence["fund_nav_reinvestment_evidence_id"]
    ]
    assert builder_views[0]["fund_nav_reinvestment_evidence"] == [evidence]


def test_exact_create_replay_is_changed_false_and_appends_no_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_instrument()
    publish_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        fund_nav_actions,
        "get_instrument",
        lambda _instrument_id: deepcopy(state),
    )
    monkeypatch.setattr(
        fund_nav_actions,
        "_load_all_durable_nav_source_rows",
        lambda **_kwargs: [],
    )

    def build(**kwargs: object) -> SimpleNamespace:
        view = deepcopy(kwargs["instrument"])
        current_events = list(view.get("fund_nav_events", []))
        current_evidence = list(view.get("fund_nav_reinvestment_evidence", []))
        return SimpleNamespace(
            rows=[],
            projection_run={"projection": "stable"},
            current_fund_nav_event_ids=[
                item["fund_nav_event_id"] for item in current_events
            ],
            current_fund_nav_reinvestment_evidence_ids=[
                item["fund_nav_reinvestment_evidence_id"]
                for item in current_evidence
            ],
            adjustment_factors=[],
            action_candidates=[],
        )

    def publish(**kwargs: object) -> dict[str, object]:
        publish_calls.append(deepcopy(kwargs))
        known_event_ids = {
            item["fund_nav_event_id"]
            for item in state["fund_nav_event_revisions"]
        }
        known_evidence_ids = {
            item["fund_nav_reinvestment_evidence_id"]
            for item in state["fund_nav_reinvestment_evidence_revisions"]
        }
        new_events = [
            item
            for item in kwargs["event_revisions"]
            if item["fund_nav_event_id"] not in known_event_ids
        ]
        new_evidence = [
            item
            for item in kwargs["reinvestment_evidence_revisions"]
            if item["fund_nav_reinvestment_evidence_id"] not in known_evidence_ids
        ]
        changed = bool(new_events or new_evidence)
        state["fund_nav_event_revisions"].extend(deepcopy(new_events))
        state["fund_nav_reinvestment_evidence_revisions"].extend(
            deepcopy(new_evidence)
        )
        if changed:
            state["fund_nav_events"] = deepcopy(list(kwargs["event_revisions"]))
            state["fund_nav_reinvestment_evidence"] = deepcopy(
                list(kwargs["reinvestment_evidence_revisions"])
            )
            state["market_data_updated_at"] = "2026-07-16T01:00:00Z"
        return {
            "record": deepcopy(state),
            "published_projection_run_id": "projection-stable",
            "market_data_updated_at": state["market_data_updated_at"],
            "dirty_from": "2026-06-05" if changed else None,
            "changed": changed,
        }

    monkeypatch.setattr(fund_nav_actions, "_build_fund_nav_publication", build)
    monkeypatch.setattr(fund_nav_actions, "publish_fund_nav_history", publish)
    monkeypatch.setattr(
        fund_nav_actions,
        "FundNavActionCandidateRepository",
        _NoopCandidateRepository,
    )
    monkeypatch.setattr(fund_nav_actions, "get_session_factory", lambda: object())

    request = {
        "instrument_id": "fund-a",
        "action_payload": ACTION,
        "reinvestment_evidence_payload": EVIDENCE,
        "client_mutation_id": "stable-replay-1",
        "recorded_by": "data-operator@example.com",
        "revision_reason": "Verified provider distribution notice.",
    }
    first = fund_nav_actions.create_fund_nav_action(**request)
    second = fund_nav_actions.create_fund_nav_action(**request)

    assert first is not None and first["changed"] is True
    assert second is not None and second["changed"] is False
    assert second["dirty_from"] is None
    assert len(state["fund_nav_event_revisions"]) == 1
    assert len(state["fund_nav_reinvestment_evidence_revisions"]) == 1
    assert len(publish_calls) == 2
    assert publish_calls[0]["event_revisions"][0][
        "fund_nav_event_id"
    ] == publish_calls[1]["event_revisions"][0]["fund_nav_event_id"]
    assert publish_calls[0]["reinvestment_evidence_revisions"][0][
        "fund_nav_reinvestment_evidence_id"
    ] == publish_calls[1]["reinvestment_evidence_revisions"][0][
        "fund_nav_reinvestment_evidence_id"
    ]


def test_create_replay_cannot_remove_evidence_from_the_exact_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_instrument()
    monkeypatch.setattr(
        fund_nav_actions,
        "get_instrument",
        lambda _instrument_id: deepcopy(state),
    )
    monkeypatch.setattr(
        fund_nav_actions,
        "_load_all_durable_nav_source_rows",
        lambda **_kwargs: [],
    )

    def build(**kwargs: object) -> SimpleNamespace:
        view = deepcopy(kwargs["instrument"])
        return SimpleNamespace(
            rows=[],
            projection_run={"projection": "evidence-removal-replay"},
            current_fund_nav_event_ids=[
                item["fund_nav_event_id"] for item in view["fund_nav_events"]
            ],
            current_fund_nav_reinvestment_evidence_ids=[
                item["fund_nav_reinvestment_evidence_id"]
                for item in view["fund_nav_reinvestment_evidence"]
            ],
            adjustment_factors=[],
            action_candidates=[],
        )

    def publish(**kwargs: object) -> dict[str, object]:
        state["fund_nav_event_revisions"].extend(
            deepcopy(list(kwargs["event_revisions"]))
        )
        state["fund_nav_reinvestment_evidence_revisions"].extend(
            deepcopy(list(kwargs["reinvestment_evidence_revisions"]))
        )
        state["fund_nav_events"] = deepcopy(list(kwargs["event_revisions"]))
        state["fund_nav_reinvestment_evidence"] = deepcopy(
            list(kwargs["reinvestment_evidence_revisions"])
        )
        return {
            "record": deepcopy(state),
            "published_projection_run_id": "projection-evidence-removal",
            "market_data_updated_at": state["market_data_updated_at"],
            "dirty_from": "2026-06-05",
            "changed": True,
        }

    monkeypatch.setattr(fund_nav_actions, "_build_fund_nav_publication", build)
    monkeypatch.setattr(fund_nav_actions, "publish_fund_nav_history", publish)
    monkeypatch.setattr(
        fund_nav_actions,
        "FundNavActionCandidateRepository",
        _NoopCandidateRepository,
    )
    monkeypatch.setattr(fund_nav_actions, "get_session_factory", lambda: object())

    request = {
        "instrument_id": "fund-a",
        "action_payload": ACTION,
        "client_mutation_id": "evidence-shape-is-immutable",
        "recorded_by": "data-operator@example.com",
        "revision_reason": "Verified provider distribution notice.",
    }
    fund_nav_actions.create_fund_nav_action(
        **request,
        reinvestment_evidence_payload=EVIDENCE,
    )

    with pytest.raises(
        fund_nav_actions.FundNavAdminConflictError,
        match="already applied with reinvestment evidence",
    ):
        fund_nav_actions.create_fund_nav_action(
            **request,
            reinvestment_evidence_payload=None,
        )


def _instrument_with_action_and_evidence() -> dict[str, object]:
    instrument = _base_instrument()
    event = fund_nav_actions._event_model(
        instrument_id="fund-a",
        fund_nav_event_id="event-1",
        fund_nav_action_id="action-1",
        revision_number=1,
        revision_kind="original",
        supersedes_fund_nav_event_id=None,
        snapshot=ACTION,
        recorded_by="operator-a",
        revision_reason="Initial verification.",
    ).model_dump(mode="json")
    evidence = fund_nav_actions._evidence_model(
        instrument_id="fund-a",
        evidence_id="evidence-1",
        fund_nav_event_id="event-1",
        revision_number=1,
        revision_kind="original",
        supersedes_evidence_id=None,
        snapshot=EVIDENCE,
        recorded_by="operator-a",
        revision_reason="Initial verification.",
    ).model_dump(mode="json")
    instrument["fund_nav_events"] = [event]
    instrument["fund_nav_event_revisions"] = [event]
    instrument["fund_nav_reinvestment_evidence"] = [evidence]
    instrument["fund_nav_reinvestment_evidence_revisions"] = [evidence]
    return instrument


def test_action_correction_drops_old_revision_evidence_unless_explicitly_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=_instrument_with_action_and_evidence(),
    )
    corrected_action = {**ACTION, "cash_per_unit": "0.12"}

    result = fund_nav_actions.revise_fund_nav_action(
        instrument_id="fund-a",
        action_id="action-1",
        predecessor_fund_nav_event_id="event-1",
        revision_kind="correction",
        action_payload=corrected_action,
        reinvestment_evidence_payload=None,
        client_mutation_id="ui-correct-1",
        recorded_by="data-operator@example.com",
        revision_reason="Provider issued a corrected cash amount.",
    )

    assert result is not None
    assert builder_views[0]["fund_nav_reinvestment_evidence"] == []
    assert publish_calls[0]["reinvestment_evidence_revisions"] == ()
    revision = publish_calls[0]["event_revisions"][0]
    assert revision["revision_number"] == 2
    assert revision["supersedes_fund_nav_event_id"] == "event-1"
    assert revision["cash_per_unit"] == "0.12"


def test_action_correction_can_atomically_publish_new_revision_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=_instrument_with_action_and_evidence(),
    )

    result = fund_nav_actions.revise_fund_nav_action(
        instrument_id="fund-a",
        action_id="action-1",
        predecessor_fund_nav_event_id="event-1",
        revision_kind="correction",
        action_payload={**ACTION, "cash_per_unit": "0.12"},
        reinvestment_evidence_payload={**EVIDENCE, "reinvestment_nav": "0.88"},
        client_mutation_id="ui-correct-with-evidence-1",
        recorded_by="data-operator@example.com",
        revision_reason="Verified the corrected notice and reinvestment price.",
    )

    assert result is not None
    call = publish_calls[0]
    event = call["event_revisions"][0]
    evidence = call["reinvestment_evidence_revisions"][0]
    assert evidence["revision_number"] == 1
    assert evidence["fund_nav_event_id"] == event["fund_nav_event_id"]
    assert evidence["supersedes_fund_nav_reinvestment_evidence_id"] is None
    assert builder_views[0]["fund_nav_reinvestment_evidence"] == [evidence]


def test_action_revision_rejects_stale_optimistic_predecessor_before_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = _instrument_with_action_and_evidence()
    instrument["fund_nav_events"] = []
    _builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=instrument,
    )

    with pytest.raises(
        fund_nav_actions.FundNavAdminConflictError,
        match="predecessor is stale",
    ):
        fund_nav_actions.revise_fund_nav_action(
            instrument_id="fund-a",
            action_id="action-1",
            predecessor_fund_nav_event_id="event-1",
            revision_kind="correction",
            action_payload={**ACTION, "cash_per_unit": "0.12"},
            reinvestment_evidence_payload=None,
            client_mutation_id="ui-stale-1",
            recorded_by="data-operator@example.com",
            revision_reason="Attempted stale revision.",
        )
    assert publish_calls == []


def test_client_mutation_id_cannot_be_reused_for_a_later_action_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = _instrument_with_action_and_evidence()
    _builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=instrument,
    )
    first = fund_nav_actions.revise_fund_nav_action(
        instrument_id="fund-a",
        action_id="action-1",
        predecessor_fund_nav_event_id="event-1",
        revision_kind="correction",
        action_payload={**ACTION, "cash_per_unit": "0.12"},
        reinvestment_evidence_payload=None,
        client_mutation_id="one-token-one-mutation",
        recorded_by="data-operator@example.com",
        revision_reason="First exact correction.",
    )
    assert first is not None
    first_revision = deepcopy(publish_calls[0]["event_revisions"][0])
    instrument["fund_nav_event_revisions"].append(first_revision)
    instrument["fund_nav_events"] = [first_revision]
    instrument["fund_nav_reinvestment_evidence"] = []

    with pytest.raises(
        fund_nav_actions.FundNavAdminConflictError,
        match="different fund NAV action revision",
    ):
        fund_nav_actions.revise_fund_nav_action(
            instrument_id="fund-a",
            action_id="action-1",
            predecessor_fund_nav_event_id=str(
                first_revision["fund_nav_event_id"]
            ),
            revision_kind="correction",
            action_payload={**ACTION, "cash_per_unit": "0.13"},
            reinvestment_evidence_payload=None,
            client_mutation_id="one-token-one-mutation",
            recorded_by="data-operator@example.com",
            revision_reason="Illegitimate second correction.",
        )

    assert len(publish_calls) == 1


def test_evidence_cancellation_is_immutable_revision_and_removes_current_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_views, publish_calls = _install_publish_harness(
        monkeypatch,
        instrument=_instrument_with_action_and_evidence(),
    )

    result = fund_nav_actions.revise_fund_nav_reinvestment_evidence(
        instrument_id="fund-a",
        predecessor_fund_nav_reinvestment_evidence_id="evidence-1",
        revision_kind="cancellation",
        evidence_payload=None,
        client_mutation_id="ui-cancel-evidence-1",
        recorded_by="data-operator@example.com",
        revision_reason="Provider withdrew the reinvestment price evidence.",
    )

    assert result is not None
    revision = publish_calls[0]["reinvestment_evidence_revisions"][0]
    assert revision["revision_kind"] == "cancellation"
    assert revision["reinvestment_nav"] == "0.9"
    assert builder_views[0]["fund_nav_reinvestment_evidence"] == []
    assert publish_calls[0]["current_fund_nav_reinvestment_evidence_ids"] == []


def _review_candidate() -> dict[str, object]:
    instrument_id = "fund-a"
    candidate_type = "cash_distribution_signal"
    interval_start = "2026-06-01"
    interval_end = "2026-06-08"
    source_provider = "email:provider-a"
    evidence = {
        "interval_start_date": interval_start,
        "interval_end_date": interval_end,
        "cash_balance_before": "0",
        "cash_balance_after": "0.1",
        "expected_cash_balance": "0",
        "measurement_uncertainty": "0.00000001",
        "unit_nav": "0.9",
        "cash_cumulative_nav": "1.0",
        "unit_provider": source_provider,
        "cash_provider": source_provider,
        "unit_evidence": {"message_sha256": "c" * 64},
        "cash_evidence": {"message_sha256": "c" * 64},
        "confirmed_event_ids": [],
    }
    source_revision = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = "\\0".join(
        (
            instrument_id,
            candidate_type,
            interval_start,
            interval_end,
            source_revision,
        )
    )
    return {
        "fund_nav_action_candidate_id": "fund-nav-action-candidate-"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "candidate_type": candidate_type,
        "interval_start_date": interval_start,
        "interval_end_date": interval_end,
        "observed_cash_balance_before": "0",
        "observed_cash_balance_after": "0.1",
        "observed_cash_delta": "0.1",
        "expected_cash_balance": "0",
        "measurement_uncertainty": "0.00000001",
        "status": "open",
        "source_provider": source_provider,
        "source_revision": source_revision,
        "source_evidence": evidence,
    }


@dataclass
class _CandidateConfirmationHarness:
    repository: FundNavActionCandidateRepository
    state: dict[str, object]
    candidate_id: str
    publish_calls: list[dict[str, object]]
    publish: Callable[..., dict[str, object]]


def _install_candidate_confirmation_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database_path: Path,
) -> _CandidateConfirmationHarness:
    engine = sa.create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    repository = FundNavActionCandidateRepository(factory)
    candidate = _review_candidate()
    candidate_id = str(candidate["fund_nav_action_candidate_id"])
    repository.project_current(instrument_id="fund-a", candidates=[candidate])
    state = _base_instrument()
    publish_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        fund_nav_actions,
        "get_instrument",
        lambda _instrument_id: deepcopy(state),
    )
    monkeypatch.setattr(fund_nav_actions, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        fund_nav_actions,
        "_load_all_durable_nav_source_rows",
        lambda **_kwargs: [],
    )

    def build(**kwargs: object) -> SimpleNamespace:
        view = deepcopy(kwargs["instrument"])
        return SimpleNamespace(
            rows=[],
            projection_run={"projection": "candidate-confirm"},
            current_fund_nav_event_ids=[
                item["fund_nav_event_id"] for item in view["fund_nav_events"]
            ],
            current_fund_nav_reinvestment_evidence_ids=[
                item["fund_nav_reinvestment_evidence_id"]
                for item in view["fund_nav_reinvestment_evidence"]
            ],
            adjustment_factors=[],
            action_candidates=[],
        )

    def publish(**kwargs: object) -> dict[str, object]:
        publish_calls.append(deepcopy(kwargs))
        known_event_ids = {
            item["fund_nav_event_id"] for item in state["fund_nav_event_revisions"]
        }
        known_evidence_ids = {
            item["fund_nav_reinvestment_evidence_id"]
            for item in state["fund_nav_reinvestment_evidence_revisions"]
        }
        new_events = [
            deepcopy(item)
            for item in kwargs["event_revisions"]
            if item["fund_nav_event_id"] not in known_event_ids
        ]
        new_evidence = [
            deepcopy(item)
            for item in kwargs["reinvestment_evidence_revisions"]
            if item["fund_nav_reinvestment_evidence_id"] not in known_evidence_ids
        ]
        changed = bool(new_events or new_evidence)
        state["fund_nav_event_revisions"].extend(new_events)
        state["fund_nav_reinvestment_evidence_revisions"].extend(new_evidence)
        if changed:
            state["fund_nav_events"] = deepcopy(new_events)
            state["fund_nav_reinvestment_evidence"] = deepcopy(new_evidence)
            state["market_data_updated_at"] = "2026-07-16T02:00:00Z"
        return {
            "record": deepcopy(state),
            "published_projection_run_id": "projection-candidate-confirm",
            "market_data_updated_at": state["market_data_updated_at"],
            "dirty_from": "2026-06-05" if changed else None,
            "changed": changed,
        }

    monkeypatch.setattr(fund_nav_actions, "_build_fund_nav_publication", build)
    monkeypatch.setattr(fund_nav_actions, "publish_fund_nav_history", publish)
    return _CandidateConfirmationHarness(
        repository=repository,
        state=state,
        candidate_id=candidate_id,
        publish_calls=publish_calls,
        publish=publish,
    )


def test_candidate_confirm_resolves_before_disappeared_signal_is_projected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_candidate_confirmation_harness(
        monkeypatch,
        database_path=tmp_path / "candidate-confirm-order.db",
    )

    request = {
        "instrument_id": "fund-a",
        "candidate_id": harness.candidate_id,
        "action_payload": ACTION,
        "reinvestment_evidence_payload": None,
        "client_mutation_id": "candidate-confirm-1",
        "recorded_by": "candidate-reviewer@example.com",
        "revision_reason": "Matched the provider distribution notice.",
    }
    first = fund_nav_actions.confirm_fund_nav_action_candidate(**request)
    second = fund_nav_actions.confirm_fund_nav_action_candidate(**request)

    assert first is not None and first["changed"] is True
    assert second is not None and second["changed"] is False
    expected_action_id = fund_nav_actions._deterministic_id(
        "fund-nav-action", "fund-a", "candidate", harness.candidate_id
    )
    expected_event_id = fund_nav_actions._deterministic_id(
        "fund-nav-event",
        "fund-a",
        expected_action_id,
        1,
        harness.candidate_id,
    )
    assert first["fund_nav_event"]["fund_nav_action_id"] == expected_action_id
    assert first["fund_nav_event"]["fund_nav_event_id"] == expected_event_id
    history = harness.repository.list_history(instrument_id="fund-a")
    assert len(history) == 1
    assert history[0]["status"] == "resolved"
    assert history[0]["decision_by"] == "candidate-reviewer@example.com"
    assert history[0]["resolved_fund_nav_event_id"] == first["fund_nav_event"][
        "fund_nav_event_id"
    ]


def test_candidate_confirmation_resumes_after_crash_before_registry_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_candidate_confirmation_harness(
        monkeypatch,
        database_path=tmp_path / "candidate-confirm-before-publish.db",
    )
    request = {
        "instrument_id": "fund-a",
        "candidate_id": harness.candidate_id,
        "action_payload": ACTION,
        "reinvestment_evidence_payload": EVIDENCE,
        "client_mutation_id": "candidate-confirm-before-publish",
        "recorded_by": "candidate-reviewer@example.com",
        "revision_reason": "Matched the exact provider distribution notice.",
    }

    monkeypatch.setattr(
        fund_nav_actions,
        "publish_fund_nav_history",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated crash before Registry commit")
        ),
    )
    with pytest.raises(RuntimeError, match="before Registry commit"):
        fund_nav_actions.confirm_fund_nav_action_candidate(**request)

    reserved = harness.repository.list_history(instrument_id="fund-a")
    assert len(reserved) == 1
    assert reserved[0]["status"] == "confirming"
    assert harness.state["fund_nav_event_revisions"] == []

    monkeypatch.setattr(
        fund_nav_actions,
        "publish_fund_nav_history",
        harness.publish,
    )
    resumed = fund_nav_actions.resume_fund_nav_action_candidate_confirmation(
        instrument_id="fund-a",
        candidate_id=harness.candidate_id,
    )

    assert resumed is not None
    assert resumed["changed"] is True
    assert resumed["candidate_confirmation_status"] == "resolved"
    assert harness.repository.list_history(instrument_id="fund-a")[0][
        "status"
    ] == "resolved"


def test_invalid_projection_never_reserves_a_candidate_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_candidate_confirmation_harness(
        monkeypatch,
        database_path=tmp_path / "candidate-confirm-invalid-projection.db",
    )
    monkeypatch.setattr(
        fund_nav_actions,
        "_build_fund_nav_publication",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("same-day action order is ambiguous")
        ),
    )

    with pytest.raises(ValueError, match="ambiguous"):
        fund_nav_actions.confirm_fund_nav_action_candidate(
            instrument_id="fund-a",
            candidate_id=harness.candidate_id,
            action_payload=ACTION,
            reinvestment_evidence_payload=None,
            client_mutation_id="candidate-invalid-projection",
            recorded_by="candidate-reviewer@example.com",
            revision_reason="Attempt exact confirmation.",
        )

    history = harness.repository.list_history(instrument_id="fund-a")
    assert len(history) == 1
    assert history[0]["status"] == "open"
    assert harness.state["fund_nav_event_revisions"] == []


def test_registry_commit_returns_pending_and_resumes_after_candidate_db_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_candidate_confirmation_harness(
        monkeypatch,
        database_path=tmp_path / "candidate-confirm-after-publish.db",
    )
    original_resolve = FundNavActionCandidateRepository.resolve
    resolve_attempts = 0

    def fail_first_resolve(
        self: FundNavActionCandidateRepository,
        **kwargs: object,
    ) -> dict[str, object]:
        nonlocal resolve_attempts
        resolve_attempts += 1
        if resolve_attempts == 1:
            raise RuntimeError("simulated candidate database outage")
        return original_resolve(self, **kwargs)

    monkeypatch.setattr(
        FundNavActionCandidateRepository,
        "resolve",
        fail_first_resolve,
    )
    first = fund_nav_actions.confirm_fund_nav_action_candidate(
        instrument_id="fund-a",
        candidate_id=harness.candidate_id,
        action_payload=ACTION,
        reinvestment_evidence_payload=None,
        client_mutation_id="candidate-confirm-after-publish",
        recorded_by="candidate-reviewer@example.com",
        revision_reason="Matched the exact provider distribution notice.",
    )

    assert first is not None
    assert first["changed"] is True
    assert first["candidate_confirmation_status"] == "confirming"
    assert first["operational_warnings"]
    assert len(harness.state["fund_nav_event_revisions"]) == 1

    resumed = fund_nav_actions.resume_fund_nav_action_candidate_confirmation(
        instrument_id="fund-a",
        candidate_id=harness.candidate_id,
    )
    assert resumed is not None
    assert resumed["changed"] is False
    assert resumed["candidate_confirmation_status"] == "resolved"
    assert len(harness.state["fund_nav_event_revisions"]) == 1


def test_concurrent_candidate_reject_and_confirm_have_one_durable_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _install_candidate_confirmation_harness(
        monkeypatch,
        database_path=tmp_path / "candidate-confirm-reject-race.db",
    )
    start = Barrier(2)

    def confirm() -> tuple[str, object]:
        start.wait()
        try:
            result = fund_nav_actions.confirm_fund_nav_action_candidate(
                instrument_id="fund-a",
                candidate_id=harness.candidate_id,
                action_payload=ACTION,
                reinvestment_evidence_payload=None,
                client_mutation_id="candidate-confirm-race",
                recorded_by="confirm-reviewer@example.com",
                revision_reason="Confirmed exact provider notice.",
            )
            return "confirmed", result
        except fund_nav_actions.FundNavActionCandidateConflictError as error:
            return "conflict", error

    def reject() -> tuple[str, object]:
        start.wait()
        try:
            result = fund_nav_actions.reject_fund_nav_action_candidate(
                instrument_id="fund-a",
                candidate_id=harness.candidate_id,
                reason="Provider verified this was not a distribution.",
                decision_by="reject-reviewer@example.com",
            )
            return "rejected", result
        except fund_nav_actions.FundNavActionCandidateConflictError as error:
            return "conflict", error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(confirm), executor.submit(reject)]
        outcomes = [future.result() for future in futures]

    statuses = [status for status, _result in outcomes]
    assert statuses.count("conflict") == 1
    history = harness.repository.list_history(instrument_id="fund-a")
    assert len(history) == 1
    if "confirmed" in statuses:
        assert history[0]["status"] == "resolved"
        assert len(harness.state["fund_nav_event_revisions"]) == 1
    else:
        assert history[0]["status"] == "rejected"
        assert harness.state["fund_nav_event_revisions"] == []
