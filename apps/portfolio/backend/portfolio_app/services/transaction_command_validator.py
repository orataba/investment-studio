"""Exact pre-commit validation of the prospective current transaction book."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.capture_common import (
    ManifestCaptureError,
    capture_transaction_replay_dependencies,
)
from portfolio_app.calculations.numeric import CalculationNumericError
from portfolio_app.calculations.portfolio_daily.hashing import (
    CanonicalHashContractError,
)
from portfolio_app.calculations.portfolio_daily.ledger import (
    LedgerReasonCode,
    LedgerStatus,
    replay_daily_ledger,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer import (
    build_transaction_replay_events,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildError,
)
from portfolio_app.db.models import (
    TransactionCurrentModel,
    TransactionRevisionGroupRecordModel,
)


class TransactionCommandValidationCode(StrEnum):
    EVENT_BUILD_FAILED = "event_build_failed"
    LEDGER_REPLAY_FAILED = "ledger_replay_failed"


class TransactionCommandValidationError(ValueError):
    """Typed rejection that remains compatible with existing service callers."""

    def __init__(
        self,
        message: str,
        *,
        code: TransactionCommandValidationCode,
        reason_code: str,
        failed_event_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason_code = reason_code
        self.failed_event_id = failed_event_id


@dataclass(frozen=True, slots=True)
class TransactionReplayEvidence:
    portfolio_id: str
    effective_as_of: date | None
    event_count: int
    final_position_count: int


def _as_aware_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata in tests.  Revision-group timestamps are
    # normalized to UTC before persistence, so restoring UTC is exact here.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _prospective_boundaries(
    session: Session,
    *,
    portfolio_id: str,
) -> tuple[date | None, datetime]:
    latest_trade_date, latest_settlement_date = session.execute(
        select(
            func.max(TransactionCurrentModel.trade_date),
            func.max(TransactionCurrentModel.settlement_date),
        ).where(TransactionCurrentModel.portfolio_id == portfolio_id)
    ).one()
    boundaries = tuple(
        boundary
        for boundary in (latest_trade_date, latest_settlement_date)
        if boundary is not None
    )
    if any(type(boundary) is not date for boundary in boundaries):
        raise TransactionCommandValidationError(
            "prospective transaction history has an invalid activity-date boundary",
            code=TransactionCommandValidationCode.EVENT_BUILD_FAILED,
            reason_code="invalid_activity_date_boundary",
        )
    # Position transfers, FX conversions, and cash legs can become effective on
    # settlement rather than trade date.  Replaying only through the latest
    # trade would therefore admit a future-settling invalid current book.
    effective_as_of = max(boundaries, default=None)
    latest_recorded_at = session.scalar(
        select(func.max(TransactionRevisionGroupRecordModel.recorded_at)).where(
            TransactionRevisionGroupRecordModel.portfolio_id == portfolio_id
        )
    )
    now = datetime.now(UTC)
    cutoff = (
        now
        if latest_recorded_at is None
        else max(now, _as_aware_utc(latest_recorded_at))
    )
    return effective_as_of, cutoff


def _replay_failure_message(
    reason_code: LedgerReasonCode,
    *,
    diagnostic: str | None,
) -> str:
    if reason_code is LedgerReasonCode.OVERSELL:
        prefix = "transaction quantity exceeds account position"
    elif reason_code is LedgerReasonCode.POSITION_NOT_FOUND:
        prefix = "transaction requires an existing account position"
    else:
        prefix = "prospective current transaction facts are not replayable"
    return f"{prefix}: {diagnostic}" if diagnostic else prefix


def validate_prospective_transaction_history(
    session: Session,
    *,
    portfolio_id: str,
) -> TransactionReplayEvidence:
    """Replay all prospective current facts before their mutation commits.

    The caller must already hold the portfolio mutation lock and must have
    appended/refreshed the complete batch.  A rejection rolls back that caller
    transaction, so no partially valid current book can become visible.
    """

    normalized_portfolio_id = portfolio_id.strip()
    if not normalized_portfolio_id:
        raise TransactionCommandValidationError(
            "portfolio_id must not be blank",
            code=TransactionCommandValidationCode.EVENT_BUILD_FAILED,
            reason_code="blank_portfolio_id",
        )
    effective_as_of, cutoff = _prospective_boundaries(
        session,
        portfolio_id=normalized_portfolio_id,
    )
    if effective_as_of is None:
        return TransactionReplayEvidence(
            portfolio_id=normalized_portfolio_id,
            effective_as_of=None,
            event_count=0,
            final_position_count=0,
        )
    try:
        captured = capture_transaction_replay_dependencies(
            session,
            portfolio_id=normalized_portfolio_id,
            effective_as_of=effective_as_of,
            knowledge_cutoff_at=cutoff,
        )
        event_build = build_transaction_replay_events(captured.rows_by_table)
    except (
        ManifestCaptureError,
        LedgerEventBuildError,
        CalculationNumericError,
        CanonicalHashContractError,
    ) as exc:
        if isinstance(exc, LedgerEventBuildError):
            reason_code = exc.code.value
        elif isinstance(exc, CalculationNumericError):
            reason_code = "numeric_contract_violation"
        elif isinstance(exc, CanonicalHashContractError):
            reason_code = "canonical_hash_contract_violation"
        else:
            reason_code = "capture_contract_violation"
        failed_event_id = (
            exc.source_record_id
            if isinstance(exc, LedgerEventBuildError)
            else None
        )
        raise TransactionCommandValidationError(
            f"prospective transaction event mapping failed: {exc}",
            code=TransactionCommandValidationCode.EVENT_BUILD_FAILED,
            reason_code=reason_code,
            failed_event_id=failed_event_id,
        ) from exc

    replay = replay_daily_ledger(
        event_build.events,
        as_of_date=effective_as_of,
    )
    if replay.status is not LedgerStatus.SUCCEEDED or replay.state is None:
        reason = (
            replay.reason_codes[0]
            if replay.reason_codes
            else LedgerReasonCode.STATE_NOT_CLOSED
        )
        raise TransactionCommandValidationError(
            _replay_failure_message(reason, diagnostic=replay.diagnostic),
            code=TransactionCommandValidationCode.LEDGER_REPLAY_FAILED,
            reason_code=reason.value,
            failed_event_id=replay.failed_event_id,
        )
    return TransactionReplayEvidence(
        portfolio_id=normalized_portfolio_id,
        effective_as_of=effective_as_of,
        event_count=len(event_build.events),
        final_position_count=len(replay.state.positions),
    )


__all__ = [
    "TransactionCommandValidationCode",
    "TransactionCommandValidationError",
    "TransactionReplayEvidence",
    "validate_prospective_transaction_history",
]
