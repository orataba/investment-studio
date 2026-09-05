from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from studio_data.db.email_models import FundNavActionCandidate
from studio_data.services.fund_nav_action_candidate_contract import (
    NormalizedFundNavActionCandidate,
    expected_fund_nav_action_candidate_id,
    normalize_fund_nav_action_candidate_id,
    normalize_fund_nav_action_candidate_projection,
    normalize_fund_nav_instrument_id,
)


_MAX_EVENT_ID_LENGTH = 1024
_MAX_REJECTION_REASON_LENGTH = 4096
_MAX_DECISION_BY_LENGTH = 512
_MAX_CLIENT_MUTATION_ID_LENGTH = 200
_ADVISORY_LOCK_SEED = 2026071601


class FundNavActionCandidateConflictError(ValueError):
    """The requested mutation conflicts with immutable candidate history."""


class FundNavActionCandidateNotFoundError(LookupError):
    """The requested Platform-private candidate does not exist."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_bounded_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long.")
    return normalized


def _normalize_sha256(value: object, *, field_name: str) -> str:
    normalized = _normalize_bounded_text(
        value,
        field_name=field_name,
        max_length=64,
    ).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest.")
    return normalized


def _normalize_confirmation_request(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("A confirmation request snapshot is required.")
    return deepcopy(dict(value))


def _lock_write_scope(session: Session, *, lock_key: str) -> None:
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    elif dialect_name == "postgresql":
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:lock_key, :lock_seed))"
            ),
            {"lock_key": lock_key, "lock_seed": _ADVISORY_LOCK_SEED},
        )


def _records_for_instrument(
    session: Session,
    *,
    instrument_id: str,
) -> list[FundNavActionCandidate]:
    statement = select(FundNavActionCandidate).where(
        FundNavActionCandidate.instrument_id == instrument_id
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    return list(session.scalars(statement))


def _candidate_for_transition(
    session: Session,
    *,
    candidate_id: str,
) -> FundNavActionCandidate:
    _lock_write_scope(
        session,
        lock_key=f"fund-nav-action-candidate:{candidate_id}",
    )
    statement = select(FundNavActionCandidate).where(
        FundNavActionCandidate.fund_nav_action_candidate_id == candidate_id
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    record = session.scalar(statement)
    if record is None:
        raise FundNavActionCandidateNotFoundError(candidate_id)
    return record


def _record_source_identity(
    record: FundNavActionCandidate,
) -> tuple[str, date, date, str]:
    return (
        record.candidate_type,
        record.interval_start_date,
        record.interval_end_date,
        record.source_revision,
    )


def _record_matches_candidate(
    record: FundNavActionCandidate,
    candidate: NormalizedFundNavActionCandidate,
) -> bool:
    return (
        record.fund_nav_action_candidate_id == candidate.candidate_id
        and record.candidate_type == candidate.candidate_type
        and record.interval_start_date == candidate.interval_start_date
        and record.interval_end_date == candidate.interval_end_date
        and record.observed_cash_balance_before
        == candidate.observed_cash_balance_before
        and record.observed_cash_balance_after
        == candidate.observed_cash_balance_after
        and record.observed_cash_delta == candidate.observed_cash_delta
        and record.expected_cash_balance == candidate.expected_cash_balance
        and record.measurement_uncertainty == candidate.measurement_uncertainty
        and record.source_provider == candidate.source_provider
        and record.source_revision == candidate.source_revision
        and dict(record.source_evidence_json or {}) == candidate.source_evidence
    )


def _candidate_record(
    *,
    instrument_id: str,
    candidate: NormalizedFundNavActionCandidate,
) -> FundNavActionCandidate:
    return FundNavActionCandidate(
        fund_nav_action_candidate_id=candidate.candidate_id,
        instrument_id=instrument_id,
        candidate_type=candidate.candidate_type,
        interval_start_date=candidate.interval_start_date,
        interval_end_date=candidate.interval_end_date,
        observed_cash_balance_before=candidate.observed_cash_balance_before,
        observed_cash_balance_after=candidate.observed_cash_balance_after,
        observed_cash_delta=candidate.observed_cash_delta,
        expected_cash_balance=candidate.expected_cash_balance,
        measurement_uncertainty=candidate.measurement_uncertainty,
        status="open",
        source_provider=candidate.source_provider,
        source_revision=candidate.source_revision,
        source_evidence_json=deepcopy(candidate.source_evidence),
        resolved_fund_nav_event_id=None,
        rejection_reason=None,
        decision_by=None,
        confirmation_client_mutation_id=None,
        confirmation_request_fingerprint=None,
        confirmation_request_json=None,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _record_as_dict(record: FundNavActionCandidate) -> dict[str, object]:
    return {
        "fund_nav_action_candidate_id": record.fund_nav_action_candidate_id,
        "instrument_id": record.instrument_id,
        "candidate_type": record.candidate_type,
        "interval_start_date": record.interval_start_date.isoformat(),
        "interval_end_date": record.interval_end_date.isoformat(),
        "observed_cash_balance_before": _decimal_text(
            record.observed_cash_balance_before
        ),
        "observed_cash_balance_after": _decimal_text(
            record.observed_cash_balance_after
        ),
        "observed_cash_delta": _decimal_text(record.observed_cash_delta),
        "expected_cash_balance": _decimal_text(record.expected_cash_balance),
        "measurement_uncertainty": _decimal_text(
            record.measurement_uncertainty
        ),
        "status": record.status,
        "source_provider": record.source_provider,
        "source_revision": record.source_revision,
        "source_evidence": deepcopy(record.source_evidence_json),
        "resolved_fund_nav_event_id": record.resolved_fund_nav_event_id,
        "rejection_reason": record.rejection_reason,
        "decision_by": record.decision_by,
        "confirmation_client_mutation_id": (
            record.confirmation_client_mutation_id
        ),
        "confirmation_request_fingerprint": (
            record.confirmation_request_fingerprint
        ),
        "created_at": _timestamp_text(record.created_at),
        "updated_at": _timestamp_text(record.updated_at),
    }


def _candidate_statement(
    *,
    instrument_id: str,
    status: str | None = None,
):
    statement = select(FundNavActionCandidate).where(
        FundNavActionCandidate.instrument_id == instrument_id
    )
    if status is not None:
        statement = statement.where(FundNavActionCandidate.status == status)
    return statement.order_by(
        FundNavActionCandidate.interval_end_date,
        FundNavActionCandidate.interval_start_date,
        FundNavActionCandidate.candidate_type,
        FundNavActionCandidate.fund_nav_action_candidate_id,
    )


class FundNavActionCandidateRepository:
    """Persist the builder's Platform-private current-candidate projection.

    The repository owns candidate review state only. It never creates or
    modifies Registry fund events or adjustment factors. ``project_current``
    returns the instrument's current ``open`` rows after the atomic projection.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def project_current(
        self,
        *,
        instrument_id: str,
        candidates: Iterable[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Atomically replace one instrument's complete current projection.

        Passing an empty iterable supersedes every open candidate. Callers must
        therefore pass the builder's complete projection, never a delta page.
        """
        normalized_instrument_id = normalize_fund_nav_instrument_id(instrument_id)
        normalized_candidates = normalize_fund_nav_action_candidate_projection(
            candidates
        )
        for candidate in normalized_candidates:
            if candidate.candidate_id != expected_fund_nav_action_candidate_id(
                instrument_id=normalized_instrument_id,
                candidate=candidate,
            ):
                raise ValueError(
                    f'Fund NAV action candidate "{candidate.candidate_id}" does not '
                    "match its instrument and source identity."
                )
        incoming_ids = {
            candidate.candidate_id for candidate in normalized_candidates
        }

        with self.session_factory() as session:
            _lock_write_scope(
                session,
                lock_key=f"fund-nav-action-candidates:{normalized_instrument_id}",
            )
            records = _records_for_instrument(
                session,
                instrument_id=normalized_instrument_id,
            )
            records_by_id = {
                record.fund_nav_action_candidate_id: record for record in records
            }
            records_by_source_identity = {
                _record_source_identity(record): record for record in records
            }

            missing_ids = incoming_ids - set(records_by_id)
            if missing_ids:
                statement = select(FundNavActionCandidate).where(
                    FundNavActionCandidate.fund_nav_action_candidate_id.in_(
                        sorted(missing_ids)
                    )
                )
                if session.get_bind().dialect.name == "postgresql":
                    statement = statement.with_for_update()
                if session.scalar(statement) is not None:
                    raise FundNavActionCandidateConflictError(
                        "A fund NAV action candidate id already belongs to another "
                        "instrument."
                    )

            for candidate in normalized_candidates:
                existing = records_by_id.get(candidate.candidate_id)
                source_match = records_by_source_identity.get(
                    candidate.source_identity
                )
                if source_match is not None and source_match is not existing:
                    raise FundNavActionCandidateConflictError(
                        "A fund NAV action source revision is already bound to a "
                        "different candidate id."
                    )
                if existing is None:
                    continue
                if not _record_matches_candidate(existing, candidate):
                    raise FundNavActionCandidateConflictError(
                        f'Fund NAV action candidate "{candidate.candidate_id}" '
                        "has immutable payload history."
                    )
                if existing.status == "superseded":
                    raise FundNavActionCandidateConflictError(
                        f'Superseded fund NAV action candidate "{candidate.candidate_id}" '
                        "cannot be reopened."
                    )
                if existing.status not in {
                    "open",
                    "confirming",
                    "resolved",
                    "rejected",
                }:
                    raise FundNavActionCandidateConflictError(
                        f'Fund NAV action candidate "{candidate.candidate_id}" '
                        "has an invalid persisted status."
                    )

            now = _utcnow()
            for record in records:
                if (
                    record.status == "open"
                    and record.fund_nav_action_candidate_id not in incoming_ids
                ):
                    record.status = "superseded"
                    record.updated_at = now
            # Release logical slots before inserting their new source revision.
            session.flush()
            session.add_all(
                _candidate_record(
                    instrument_id=normalized_instrument_id,
                    candidate=candidate,
                )
                for candidate in normalized_candidates
                if candidate.candidate_id not in records_by_id
            )
            session.flush()
            current_open = [
                _record_as_dict(record)
                for record in session.scalars(
                    _candidate_statement(
                        instrument_id=normalized_instrument_id,
                        status="open",
                    )
                )
            ]
            session.commit()
            return current_open

    def list_reviewable(self, *, instrument_id: str) -> list[dict[str, object]]:
        normalized_instrument_id = normalize_fund_nav_instrument_id(instrument_id)
        with self.session_factory() as session:
            return [
                _record_as_dict(record)
                for record in session.scalars(
                    _candidate_statement(
                        instrument_id=normalized_instrument_id,
                    ).where(
                        FundNavActionCandidate.status.in_(("open", "confirming"))
                    )
                )
            ]

    def list_history(self, *, instrument_id: str) -> list[dict[str, object]]:
        normalized_instrument_id = normalize_fund_nav_instrument_id(instrument_id)
        with self.session_factory() as session:
            return [
                _record_as_dict(record)
                for record in session.scalars(
                    _candidate_statement(instrument_id=normalized_instrument_id)
                )
            ]

    def reserve_confirmation(
        self,
        *,
        fund_nav_action_candidate_id: str,
        confirmed_fund_nav_event_id: str,
        decision_by: str,
        client_mutation_id: str,
        request_fingerprint: str,
        confirmation_request: Mapping[str, object],
    ) -> dict[str, object]:
        candidate_id = normalize_fund_nav_action_candidate_id(
            fund_nav_action_candidate_id
        )
        event_id = _normalize_bounded_text(
            confirmed_fund_nav_event_id,
            field_name="A confirmed fund NAV event id",
            max_length=_MAX_EVENT_ID_LENGTH,
        )
        normalized_decision_by = _normalize_bounded_text(
            decision_by,
            field_name="A candidate decision actor",
            max_length=_MAX_DECISION_BY_LENGTH,
        )
        normalized_mutation_id = _normalize_bounded_text(
            client_mutation_id,
            field_name="A confirmation client mutation id",
            max_length=_MAX_CLIENT_MUTATION_ID_LENGTH,
        )
        normalized_fingerprint = _normalize_sha256(
            request_fingerprint,
            field_name="A confirmation request fingerprint",
        )
        normalized_request = _normalize_confirmation_request(
            confirmation_request
        )
        with self.session_factory() as session:
            record = _candidate_for_transition(
                session,
                candidate_id=candidate_id,
            )
            if record.status in {"confirming", "resolved"}:
                if (
                    record.resolved_fund_nav_event_id != event_id
                    or record.decision_by != normalized_decision_by
                    or record.confirmation_client_mutation_id
                    != normalized_mutation_id
                    or record.confirmation_request_fingerprint
                    != normalized_fingerprint
                    or dict(record.confirmation_request_json or {})
                    != normalized_request
                ):
                    raise FundNavActionCandidateConflictError(
                        "A reserved fund NAV action confirmation cannot change its "
                        "event, operator, mutation id, or exact request payload."
                    )
                session.commit()
                return _record_as_dict(record)
            if record.status != "open":
                raise FundNavActionCandidateConflictError(
                    f'Fund NAV action candidate "{candidate_id}" cannot transition '
                    f'from {record.status} to confirming.'
                )
            record.status = "confirming"
            record.resolved_fund_nav_event_id = event_id
            record.rejection_reason = None
            record.decision_by = normalized_decision_by
            record.confirmation_client_mutation_id = normalized_mutation_id
            record.confirmation_request_fingerprint = normalized_fingerprint
            record.confirmation_request_json = normalized_request
            record.updated_at = _utcnow()
            session.commit()
            return _record_as_dict(record)

    def get_confirmation_request(
        self,
        *,
        fund_nav_action_candidate_id: str,
    ) -> dict[str, object]:
        candidate_id = normalize_fund_nav_action_candidate_id(
            fund_nav_action_candidate_id
        )
        with self.session_factory() as session:
            record = session.get(FundNavActionCandidate, candidate_id)
            if record is None:
                raise FundNavActionCandidateNotFoundError(candidate_id)
            if record.status not in {"confirming", "resolved"}:
                raise FundNavActionCandidateConflictError(
                    f'Fund NAV action candidate "{candidate_id}" has no reserved '
                    "confirmation to resume."
                )
            request = dict(record.confirmation_request_json or {})
            if not request:
                raise FundNavActionCandidateConflictError(
                    "The reserved candidate confirmation has no durable request snapshot."
                )
            return deepcopy(request)

    def resolve(
        self,
        *,
        fund_nav_action_candidate_id: str,
        confirmed_fund_nav_event_id: str,
        decision_by: str,
        client_mutation_id: str,
        request_fingerprint: str,
    ) -> dict[str, object]:
        candidate_id = normalize_fund_nav_action_candidate_id(
            fund_nav_action_candidate_id
        )
        event_id = _normalize_bounded_text(
            confirmed_fund_nav_event_id,
            field_name="A confirmed fund NAV event id",
            max_length=_MAX_EVENT_ID_LENGTH,
        )
        normalized_decision_by = _normalize_bounded_text(
            decision_by,
            field_name="A candidate decision actor",
            max_length=_MAX_DECISION_BY_LENGTH,
        )
        normalized_mutation_id = _normalize_bounded_text(
            client_mutation_id,
            field_name="A confirmation client mutation id",
            max_length=_MAX_CLIENT_MUTATION_ID_LENGTH,
        )
        normalized_fingerprint = _normalize_sha256(
            request_fingerprint,
            field_name="A confirmation request fingerprint",
        )
        with self.session_factory() as session:
            record = _candidate_for_transition(
                session,
                candidate_id=candidate_id,
            )
            if record.status == "resolved":
                if (
                    record.resolved_fund_nav_event_id != event_id
                    or record.decision_by != normalized_decision_by
                    or record.confirmation_client_mutation_id
                    != normalized_mutation_id
                    or record.confirmation_request_fingerprint
                    != normalized_fingerprint
                ):
                    raise FundNavActionCandidateConflictError(
                        "A resolved fund NAV action candidate cannot change its "
                        "reserved confirmation identity."
                    )
                session.commit()
                return _record_as_dict(record)
            if record.status != "confirming":
                raise FundNavActionCandidateConflictError(
                    f'Fund NAV action candidate "{candidate_id}" cannot transition '
                    f'from {record.status} to resolved.'
                )
            if (
                record.resolved_fund_nav_event_id != event_id
                or record.decision_by != normalized_decision_by
                or record.confirmation_client_mutation_id
                != normalized_mutation_id
                or record.confirmation_request_fingerprint
                != normalized_fingerprint
            ):
                raise FundNavActionCandidateConflictError(
                    "The completion does not match the reserved candidate confirmation."
                )
            record.status = "resolved"
            record.updated_at = _utcnow()
            session.commit()
            return _record_as_dict(record)

    def reject(
        self,
        *,
        fund_nav_action_candidate_id: str,
        reason: str,
        decision_by: str,
    ) -> dict[str, object]:
        candidate_id = normalize_fund_nav_action_candidate_id(
            fund_nav_action_candidate_id
        )
        normalized_reason = _normalize_bounded_text(
            reason,
            field_name="A rejection reason",
            max_length=_MAX_REJECTION_REASON_LENGTH,
        )
        normalized_decision_by = _normalize_bounded_text(
            decision_by,
            field_name="A candidate decision actor",
            max_length=_MAX_DECISION_BY_LENGTH,
        )
        with self.session_factory() as session:
            record = _candidate_for_transition(
                session,
                candidate_id=candidate_id,
            )
            if record.status == "rejected":
                if (
                    record.rejection_reason != normalized_reason
                    or record.decision_by != normalized_decision_by
                ):
                    raise FundNavActionCandidateConflictError(
                        "A rejected fund NAV action candidate cannot change its "
                        "reason or decision actor."
                    )
                session.commit()
                return _record_as_dict(record)
            if record.status != "open":
                raise FundNavActionCandidateConflictError(
                    f'Fund NAV action candidate "{candidate_id}" cannot transition '
                    f'from {record.status} to rejected.'
                )
            record.status = "rejected"
            record.resolved_fund_nav_event_id = None
            record.rejection_reason = normalized_reason
            record.decision_by = normalized_decision_by
            record.updated_at = _utcnow()
            session.commit()
            return _record_as_dict(record)
