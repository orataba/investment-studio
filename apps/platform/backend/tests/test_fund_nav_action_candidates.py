from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Callable

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from platform_app.core.settings import get_settings
from platform_app.db.base import Base
from platform_app.services import market_data_ops
from platform_app.services.fund_nav_action_candidates import (
    FundNavActionCandidateConflictError,
    FundNavActionCandidateRepository,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def candidate_repository(
    tmp_path: Path,
) -> FundNavActionCandidateRepository:
    engine = sa.create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'fund-nav-action-candidates.db'}"
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return FundNavActionCandidateRepository(factory)


def _candidate(
    *,
    identity_hex: str = "a",
    instrument_id: str = "fund-a",
    interval_start_date: str = "2026-06-01",
    interval_end_date: str = "2026-06-08",
    cash_before: str = "0.1",
    cash_after: str = "0.2",
    cash_delta: str = "0.1",
    candidate_type: str = "cash_distribution_signal",
) -> dict[str, object]:
    source_provider = "email:provider-a"
    source_evidence: dict[str, object] = {
        "interval_start_date": interval_start_date,
        "interval_end_date": interval_end_date,
        "cash_balance_before": cash_before,
        "cash_balance_after": cash_after,
        "expected_cash_balance": "0.1",
        "measurement_uncertainty": "0.00000001",
        "unit_nav": "1.1",
        "cash_cumulative_nav": "1.3",
        "unit_provider": source_provider,
        "cash_provider": source_provider,
        "unit_evidence": {"test_identity": identity_hex},
        "cash_evidence": {"test_identity": identity_hex},
        "confirmed_event_ids": [],
    }
    source_revision = hashlib.sha256(
        json.dumps(
            source_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = "\\0".join(
        (
            instrument_id,
            candidate_type,
            interval_start_date,
            interval_end_date,
            source_revision,
        )
    )
    candidate_id = "fund-nav-action-candidate-" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()
    return {
        "fund_nav_action_candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "interval_start_date": interval_start_date,
        "interval_end_date": interval_end_date,
        "observed_cash_balance_before": cash_before,
        "observed_cash_balance_after": cash_after,
        "observed_cash_delta": cash_delta,
        "expected_cash_balance": "0.1",
        "measurement_uncertainty": "0.00000001",
        "status": "open",
        "source_provider": source_provider,
        "source_revision": source_revision,
        "source_evidence": source_evidence,
    }


def test_current_projection_is_exactly_idempotent_and_supersedes_old_open(
    candidate_repository: FundNavActionCandidateRepository,
) -> None:
    first_candidate = _candidate(identity_hex="a")
    first_open = candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[first_candidate],
    )
    assert len(first_open) == 1
    assert first_open[0]["status"] == "open"

    history_before_replay = candidate_repository.list_history(
        instrument_id="fund-a"
    )
    replayed_open = candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[deepcopy(first_candidate)],
    )
    assert replayed_open == first_open
    assert candidate_repository.list_history(
        instrument_id="fund-a"
    ) == history_before_replay

    revised_candidate = _candidate(
        identity_hex="b",
        cash_after="0.3",
        cash_delta="0.2",
    )
    revised_open = candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[revised_candidate],
    )
    assert [row["fund_nav_action_candidate_id"] for row in revised_open] == [
        revised_candidate["fund_nav_action_candidate_id"]
    ]
    history_by_id = {
        row["fund_nav_action_candidate_id"]: row
        for row in candidate_repository.list_history(instrument_id="fund-a")
    }
    assert history_by_id[first_candidate["fund_nav_action_candidate_id"]][
        "status"
    ] == "superseded"
    assert history_by_id[revised_candidate["fund_nav_action_candidate_id"]][
        "status"
    ] == "open"

    assert candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[],
    ) == []
    history_before_stale_replay = candidate_repository.list_history(
        instrument_id="fund-a"
    )
    with pytest.raises(
        FundNavActionCandidateConflictError,
        match="cannot be reopened",
    ):
        candidate_repository.project_current(
            instrument_id="fund-a",
            candidates=[revised_candidate],
        )
    assert candidate_repository.list_history(
        instrument_id="fund-a"
    ) == history_before_stale_replay


def test_current_market_data_builder_payload_round_trips_without_drift(
    candidate_repository: FundNavActionCandidateRepository,
) -> None:
    rows = market_data_ops._stamp_nav_value_statuses(
        [
            {
                "as_of_date": "2026-05-27",
                "currency": "CNY",
                "nav": "1.0000",
                "cash_cumulative_nav": "1.0000",
            },
            {
                "as_of_date": "2026-05-28",
                "currency": "CNY",
                "nav": "1.0000",
                "cash_cumulative_nav": "2.0000",
            },
            {
                "as_of_date": "2026-05-29",
                "currency": "CNY",
                "nav": "1.0100",
                "cash_cumulative_nav": "2.0100",
            },
        ],
        status="complete",
    )
    publication = market_data_ops._build_fund_nav_publication(
        instrument_id="private-fund",
        instrument={
            "currency": "CNY",
            "fund_nav_events": [],
            "fund_nav_reinvestment_evidence": [],
        },
        rows=rows,
    )

    current = candidate_repository.project_current(
        instrument_id="private-fund",
        candidates=publication.action_candidates,
    )
    assert len(current) == 1
    assert current[0]["observed_cash_delta"] == "1"
    assert candidate_repository.project_current(
        instrument_id="private-fund",
        candidates=publication.action_candidates,
    ) == current


def test_resolved_and_rejected_candidates_are_terminal_and_replay_safe(
    candidate_repository: FundNavActionCandidateRepository,
) -> None:
    resolved_candidate = _candidate(identity_hex="c")
    candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[resolved_candidate],
    )
    confirmation_identity = {
        "client_mutation_id": "confirm-candidate-c",
        "request_fingerprint": "c" * 64,
    }
    reserved = candidate_repository.reserve_confirmation(
        fund_nav_action_candidate_id=str(
            resolved_candidate["fund_nav_action_candidate_id"]
        ),
        confirmed_fund_nav_event_id="confirmed-provider-event-2026-06-05",
        decision_by="reviewer@example.com",
        confirmation_request={"action": {"effective_date": "2026-06-05"}},
        **confirmation_identity,
    )
    assert reserved["status"] == "confirming"
    assert candidate_repository.list_reviewable(instrument_id="fund-a") == [reserved]
    resolved = candidate_repository.resolve(
        fund_nav_action_candidate_id=str(
            resolved_candidate["fund_nav_action_candidate_id"]
        ),
        confirmed_fund_nav_event_id="confirmed-provider-event-2026-06-05",
        decision_by="reviewer@example.com",
        **confirmation_identity,
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_fund_nav_event_id"] == (
        "confirmed-provider-event-2026-06-05"
    )
    assert resolved["rejection_reason"] is None
    assert resolved["decision_by"] == "reviewer@example.com"

    resolved_replay = candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[resolved_candidate],
    )
    assert resolved_replay == []
    assert candidate_repository.resolve(
        fund_nav_action_candidate_id=str(
            resolved_candidate["fund_nav_action_candidate_id"]
        ),
        confirmed_fund_nav_event_id="confirmed-provider-event-2026-06-05",
        decision_by="reviewer@example.com",
        **confirmation_identity,
    ) == resolved
    with pytest.raises(FundNavActionCandidateConflictError):
        candidate_repository.resolve(
            fund_nav_action_candidate_id=str(
                resolved_candidate["fund_nav_action_candidate_id"]
            ),
            confirmed_fund_nav_event_id="different-confirmed-event",
            decision_by="reviewer@example.com",
            **confirmation_identity,
        )
    with pytest.raises(
        FundNavActionCandidateConflictError,
        match="reserved confirmation identity",
    ):
        candidate_repository.resolve(
            fund_nav_action_candidate_id=str(
                resolved_candidate["fund_nav_action_candidate_id"]
            ),
            confirmed_fund_nav_event_id="confirmed-provider-event-2026-06-05",
            decision_by="different-reviewer@example.com",
            **confirmation_identity,
        )
    with pytest.raises(FundNavActionCandidateConflictError):
        candidate_repository.reject(
            fund_nav_action_candidate_id=str(
                resolved_candidate["fund_nav_action_candidate_id"]
            ),
            reason="not an event",
            decision_by="reviewer@example.com",
        )
    rejected_candidate = _candidate(
        identity_hex="d",
        interval_start_date="2026-06-08",
        interval_end_date="2026-06-15",
    )
    candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[rejected_candidate],
    )
    with pytest.raises(ValueError, match="reason is required"):
        candidate_repository.reject(
            fund_nav_action_candidate_id=str(
                rejected_candidate["fund_nav_action_candidate_id"]
            ),
            reason="  ",
            decision_by="reviewer@example.com",
        )
    rejected = candidate_repository.reject(
        fund_nav_action_candidate_id=str(
            rejected_candidate["fund_nav_action_candidate_id"]
        ),
        reason="provider confirmed this was a reporting correction",
        decision_by="reviewer@example.com",
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == (
        "provider confirmed this was a reporting correction"
    )
    assert rejected["source_evidence"] == rejected_candidate["source_evidence"]
    assert rejected["decision_by"] == "reviewer@example.com"
    assert candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[rejected_candidate],
    ) == []
    assert candidate_repository.reject(
        fund_nav_action_candidate_id=str(
            rejected_candidate["fund_nav_action_candidate_id"]
        ),
        reason="provider confirmed this was a reporting correction",
        decision_by="reviewer@example.com",
    ) == rejected
    with pytest.raises(
        FundNavActionCandidateConflictError,
        match="decision actor",
    ):
        candidate_repository.reject(
            fund_nav_action_candidate_id=str(
                rejected_candidate["fund_nav_action_candidate_id"]
            ),
            reason="provider confirmed this was a reporting correction",
            decision_by="different-reviewer@example.com",
        )
    with pytest.raises(FundNavActionCandidateConflictError):
        candidate_repository.reject(
            fund_nav_action_candidate_id=str(
                rejected_candidate["fund_nav_action_candidate_id"]
            ),
            reason="a different reason",
            decision_by="reviewer@example.com",
        )
    with pytest.raises(FundNavActionCandidateConflictError):
        candidate_repository.resolve(
            fund_nav_action_candidate_id=str(
                rejected_candidate["fund_nav_action_candidate_id"]
            ),
            confirmed_fund_nav_event_id="confirmed-event-after-rejection",
            decision_by="reviewer@example.com",
            client_mutation_id="rejected-candidate-confirm",
            request_fingerprint="d" * 64,
        )


def _add_effective_date(candidate: dict[str, object]) -> None:
    candidate["effective_date"] = "2026-06-05"


def _add_reinvestment_nav(candidate: dict[str, object]) -> None:
    candidate["reinvestment_nav"] = "1.2345"


def _remove_source_evidence(candidate: dict[str, object]) -> None:
    candidate.pop("source_evidence")


def _empty_source_evidence(candidate: dict[str, object]) -> None:
    candidate["source_evidence"] = {}


def _change_status(candidate: dict[str, object]) -> None:
    candidate["status"] = "resolved"


def _reverse_interval(candidate: dict[str, object]) -> None:
    candidate["interval_end_date"] = candidate["interval_start_date"]


def _break_cash_delta(candidate: dict[str, object]) -> None:
    candidate["observed_cash_delta"] = "0.2"


def _invalidate_revision(candidate: dict[str, object]) -> None:
    candidate["source_revision"] = "G" * 64


def _mismatch_candidate_type(candidate: dict[str, object]) -> None:
    candidate["candidate_type"] = "cash_balance_discontinuity"


def _zero_uncertainty(candidate: dict[str, object]) -> None:
    candidate["measurement_uncertainty"] = "0"


def _exceed_decimal_scale(candidate: dict[str, object]) -> None:
    candidate["measurement_uncertainty"] = "0.0000000000000000001"


@pytest.mark.parametrize(
    "mutate",
    [
        _add_effective_date,
        _add_reinvestment_nav,
        _remove_source_evidence,
        _empty_source_evidence,
        _change_status,
        _reverse_interval,
        _break_cash_delta,
        _invalidate_revision,
        _mismatch_candidate_type,
        _zero_uncertainty,
        _exceed_decimal_scale,
    ],
    ids=[
        "no-effective-date-field",
        "no-reinvestment-nav-field",
        "source-evidence-required",
        "source-evidence-nonempty",
        "incoming-status-open",
        "ordered-interval",
        "cash-delta-consistent",
        "revision-is-sha256",
        "type-matches-delta",
        "uncertainty-positive",
        "decimal-scale-bounded",
    ],
)
def test_projection_rejects_non_builder_or_inconsistent_payloads(
    candidate_repository: FundNavActionCandidateRepository,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    candidate = _candidate()
    mutate(candidate)
    with pytest.raises(ValueError):
        candidate_repository.project_current(
            instrument_id="fund-a",
            candidates=[candidate],
        )
    assert candidate_repository.list_history(instrument_id="fund-a") == []


def test_projection_is_isolated_per_instrument_and_candidate_id_is_global(
    candidate_repository: FundNavActionCandidateRepository,
) -> None:
    candidate_a = _candidate(identity_hex="e")
    candidate_b = _candidate(identity_hex="f", instrument_id="fund-b")
    candidate_repository.project_current(
        instrument_id="fund-a",
        candidates=[candidate_a],
    )
    candidate_repository.project_current(
        instrument_id="fund-b",
        candidates=[candidate_b],
    )
    candidate_repository.project_current(instrument_id="fund-a", candidates=[])
    assert candidate_repository.list_reviewable(instrument_id="fund-a") == []
    assert len(candidate_repository.list_reviewable(instrument_id="fund-b")) == 1

    with pytest.raises(ValueError, match="does not match its instrument"):
        candidate_repository.project_current(
            instrument_id="fund-c",
            candidates=[candidate_b],
        )
    assert len(candidate_repository.list_reviewable(instrument_id="fund-b")) == 1
    assert candidate_repository.list_history(instrument_id="fund-c") == []


def _platform_alembic_config(
    *,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Config:
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.delenv(
        "PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL",
        raising=False,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA", "")
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def _database_candidate_values(
    *,
    candidate_id: str,
    status: str = "open",
    resolved_event_id: str | None = None,
    rejection_reason: str | None = None,
    decision_by: str | None = None,
    confirmation_client_mutation_id: str | None = None,
    confirmation_request_fingerprint: str | None = None,
    confirmation_request_json: dict[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "fund_nav_action_candidate_id": candidate_id,
        "instrument_id": "fund-migration",
        "candidate_type": "cash_distribution_signal",
        "interval_start_date": date(2026, 6, 1),
        "interval_end_date": date(2026, 6, 8),
        "observed_cash_balance_before": "0.1",
        "observed_cash_balance_after": "0.2",
        "observed_cash_delta": "0.1",
        "expected_cash_balance": "0.1",
        "measurement_uncertainty": "0.00000001",
        "status": status,
        "source_provider": "migration-test",
        "source_revision": candidate_id[-1] * 64,
        "source_evidence_json": {"source": "migration-test"},
        "resolved_fund_nav_event_id": resolved_event_id,
        "rejection_reason": rejection_reason,
        "decision_by": decision_by,
        "confirmation_client_mutation_id": confirmation_client_mutation_id,
        "confirmation_request_fingerprint": confirmation_request_fingerprint,
    }
    if confirmation_request_json is not None:
        values["confirmation_request_json"] = confirmation_request_json
    return values


def test_platform_migration_enforces_terminal_and_rejection_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = (
        f"sqlite+pysqlite:///{tmp_path / 'fund-nav-action-candidate-migration.db'}"
    )
    config = _platform_alembic_config(
        database_url=database_url,
        monkeypatch=monkeypatch,
    )
    try:
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()

    engine = sa.create_engine(database_url)
    metadata = sa.MetaData()
    table = sa.Table(
        "fund_nav_action_candidate",
        metadata,
        autoload_with=engine,
    )
    resolved_id = "fund-nav-action-candidate-" + "8" * 64
    with engine.begin() as connection:
        connection.execute(
            sa.insert(table).values(
                **_database_candidate_values(candidate_id=resolved_id)
            )
        )
        connection.execute(
            sa.update(table)
            .where(table.c.fund_nav_action_candidate_id == resolved_id)
            .values(
                status="confirming",
                resolved_fund_nav_event_id="confirmed-event",
                decision_by="migration-reviewer",
                confirmation_client_mutation_id="migration-confirmation",
                confirmation_request_fingerprint="8" * 64,
                confirmation_request_json={"action": "snapshot"},
            )
        )
        connection.execute(
            sa.update(table)
            .where(table.c.fund_nav_action_candidate_id == resolved_id)
            .values(status="resolved")
        )

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.update(table)
                .where(table.c.fund_nav_action_candidate_id == resolved_id)
                .values(
                    status="open",
                    resolved_fund_nav_event_id=None,
                )
            )

    confirming_id = "fund-nav-action-candidate-" + "3" * 64
    with engine.begin() as connection:
        connection.execute(
            sa.insert(table).values(
                **_database_candidate_values(candidate_id=confirming_id)
            )
        )
        connection.execute(
            sa.update(table)
            .where(table.c.fund_nav_action_candidate_id == confirming_id)
            .values(
                status="confirming",
                resolved_fund_nav_event_id="reserved-event",
                decision_by="migration-reviewer",
                confirmation_client_mutation_id="reserved-mutation",
                confirmation_request_fingerprint="3" * 64,
                confirmation_request_json={"action": "reserved"},
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.update(table)
                .where(table.c.fund_nav_action_candidate_id == confirming_id)
                .values(
                    status="rejected",
                    resolved_fund_nav_event_id=None,
                    rejection_reason="racing rejection",
                    confirmation_client_mutation_id=None,
                    confirmation_request_fingerprint=None,
                    confirmation_request_json=None,
                )
            )

    rejected_id = "fund-nav-action-candidate-" + "7" * 64
    with engine.begin() as connection:
        connection.execute(
            sa.insert(table).values(
                **_database_candidate_values(
                    candidate_id=rejected_id,
                    status="rejected",
                    rejection_reason="confirmed reporting correction",
                    decision_by="migration-reviewer",
                )
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.update(table)
                .where(table.c.fund_nav_action_candidate_id == rejected_id)
                .values(
                    status="open",
                    rejection_reason=None,
                )
            )

    first_open_id = "fund-nav-action-candidate-" + "6" * 64
    with engine.begin() as connection:
        connection.execute(
            sa.insert(table).values(
                **_database_candidate_values(candidate_id=first_open_id)
            )
        )
    second_open_id = "fund-nav-action-candidate-" + "5" * 64
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.insert(table).values(
                    **_database_candidate_values(candidate_id=second_open_id)
                )
            )

    rejected_without_reason_id = "fund-nav-action-candidate-" + "9" * 64
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.insert(table).values(
                    **_database_candidate_values(
                        candidate_id=rejected_without_reason_id,
                        status="rejected",
                    )
                )
            )

    resolved_without_actor_id = "fund-nav-action-candidate-" + "4" * 64
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.insert(table).values(
                    **_database_candidate_values(
                        candidate_id=resolved_without_actor_id,
                        status="resolved",
                        resolved_event_id="confirmed-without-actor",
                    )
                )
            )
