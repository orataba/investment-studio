from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from uuid import uuid4

from portfolio_ops_instrument_core.db_models import Instrument
from sqlalchemy import and_, case, delete, func, or_, select, update

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioAnalyticsPolicyStateModel,
    PortfolioCalculationStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import (
    attribution,
    holdings_market_profile,
    performance,
    return_chain,
)
from portfolio_app.services.instrument_charts import HOLDINGS_PRICE_CHART_RANGE_KEYS
from portfolio_app.services.portfolio_store import (
    _resolve_live_portfolio_as_of_date,
    _serialize_account_row,
    _serialize_portfolio_row,
    _serialize_transaction_row,
)
from portfolio_app.services.snapshot_selection import (
    default_portfolio_snapshot,
)
from portfolio_app.services.transaction_dates import (
    transaction_performance_effective_date,
)

_LOCAL_REFRESH_LOCKS: dict[str, Lock] = {}
_LOCAL_REFRESH_LOCKS_GUARD = Lock()
_RUNNING_REFRESH_LEASE_SECONDS = 900.0
_DAILY_SNAPSHOT_QUEUE_SCAN_LIMIT = 64
_SOURCE_GENERATION_MAX_DISCARDS = 3
_SOURCE_GENERATION_CHANGED_REASON = "source_generation_changed_during_calculation"
_SOURCE_GENERATION_CHANGED_BEFORE_PUBLISH_REASON = "source_generation_changed_before_publish"
DAILY_SNAPSHOT_CALCULATION_VERSION = (
    "portfolio-daily-v20260806-derivative-liability-event-valuation-basis-transfer"
    "-holding-kind-identity-split-coverage-return-chain-quote-identity-market-history"
    "-source-generation-fence-pending-settlement-fx-recorded-attached-charges"
    "-portfolio-instrument-total-return-windows-v2-gips-funded-segment-boundaries-v4"
    "-position-effective-recognition-bridge-v1"
    "-pending-monetary-holdings-v1"
    "-effective-analytics-scope-policy-v1"
    "-option-cash-settlement-v1"
    "-market-risk-zero-return-cash-derivatives-v2"
)


@dataclass(frozen=True)
class _SnapshotSourceGeneration:
    refresh_request_id: str | None
    market_data_updated_at: str | None
    analytics_policy_version: int

    def as_payload(self) -> dict[str, object]:
        return {
            "refresh_request_id": self.refresh_request_id,
            "market_data_updated_at": self.market_data_updated_at,
            "analytics_policy_version": self.analytics_policy_version,
        }


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _running_refresh_lease_expired(
    state: PortfolioCalculationStateModel,
    *,
    now: datetime | None = None,
) -> bool:
    started_at = _parse_utc_timestamp(state.refresh_started_at)
    if started_at is None:
        return True
    return (now or datetime.now(UTC)) - started_at >= timedelta(seconds=_RUNNING_REFRESH_LEASE_SECONDS)


def _new_refresh_request_id() -> str:
    return uuid4().hex


def _refresh_lock_for_portfolio(portfolio_id: str) -> Lock:
    with _LOCAL_REFRESH_LOCKS_GUARD:
        lock = _LOCAL_REFRESH_LOCKS.get(portfolio_id)
        if lock is None:
            lock = Lock()
            _LOCAL_REFRESH_LOCKS[portfolio_id] = lock
        return lock


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _json_safe(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _restore_snapshot(value: dict[str, object]) -> dict[str, object]:
    snapshot = dict(value)
    snapshot_date = _parse_date(snapshot.get("as_of_date"))
    if snapshot_date is not None:
        snapshot["as_of_date"] = snapshot_date
    return snapshot


def _restore_dated_payload(value: dict[str, object]) -> dict[str, object]:
    payload = dict(value)
    payload_date = _parse_date(payload.get("as_of_date"))
    if payload_date is not None:
        payload["as_of_date"] = payload_date
    return payload


def _public_snapshot_payload(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"_holding_rows", "_contribution_slices"}
    }


def _state_for_portfolio(session, portfolio_id: str) -> PortfolioCalculationStateModel:
    state = session.get(PortfolioCalculationStateModel, portfolio_id)
    if state is None:
        state = PortfolioCalculationStateModel(
            portfolio_id=portfolio_id,
            daily_snapshot_status="stale",
        )
        session.add(state)
        session.flush()
    return state


def _source_market_data_watermark(session, portfolio_id: str) -> str | None:
    portfolio_instrument_ids = select(TransactionRecordModel.instrument_id).where(
        TransactionRecordModel.portfolio_id == portfolio_id,
        TransactionRecordModel.instrument_id.is_not(None),
    )
    value = session.scalar(
        select(func.max(Instrument.market_data_updated_at)).where(
            Instrument.market_data_updated_at.is_not(None),
            or_(
                Instrument.instrument_id.in_(portfolio_instrument_ids),
                Instrument.instrument_type == "fx",
            ),
        )
    )
    return str(value) if value not in (None, "") else None


def _source_market_data_is_current(
    session,
    portfolio_id: str,
    state: PortfolioCalculationStateModel,
) -> bool:
    return state.source_market_data_updated_at == _source_market_data_watermark(session, portfolio_id)


def _snapshot_source_generation(
    session,
    portfolio_id: str,
) -> _SnapshotSourceGeneration | None:
    state = session.get(PortfolioCalculationStateModel, portfolio_id)
    if state is None:
        return None
    analytics_state = session.get(PortfolioAnalyticsPolicyStateModel, portfolio_id)
    return _SnapshotSourceGeneration(
        refresh_request_id=state.refresh_request_id,
        market_data_updated_at=_source_market_data_watermark(session, portfolio_id),
        analytics_policy_version=(
            int(analytics_state.current_version)
            if analytics_state is not None
            else 0
        ),
    )


def _read_snapshot_source_generation(portfolio_id: str) -> _SnapshotSourceGeneration | None:
    """Read the generation from a fresh session after an out-of-session calculation.

    The snapshot builder resolves market data through independent registry reads. Reusing
    the calculation session here could therefore compare against a transaction-local view
    instead of the generation that is currently committed.
    """

    session_factory = get_session_factory()
    with session_factory() as session:
        return _snapshot_source_generation(session, portfolio_id)


def _load_accounts(session, portfolio_id: str) -> list[AccountRecordModel]:
    return list(
        session.scalars(
            select(AccountRecordModel)
            .where(AccountRecordModel.portfolio_id == portfolio_id)
            .order_by(
                AccountRecordModel.account_type,
                AccountRecordModel.account_name,
                AccountRecordModel.account_id,
            )
        ).all()
    )


def _load_transactions(session, portfolio_id: str) -> list[TransactionRecordModel]:
    return list(
        session.scalars(
            select(TransactionRecordModel)
            .where(TransactionRecordModel.portfolio_id == portfolio_id)
            .order_by(
                TransactionRecordModel.trade_date,
                TransactionRecordModel.trade_at,
                TransactionRecordModel.created_at,
                TransactionRecordModel.transaction_sequence,
                TransactionRecordModel.settlement_date,
            )
        ).all()
    )


def _snapshot_count(session, portfolio_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(PortfolioDailySnapshotModel)
            .where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
        )
        or 0
    )


def _latest_snapshot_calculation_version(session, portfolio_id: str) -> str | None:
    payload = session.scalar(
        select(PortfolioDailySnapshotModel.snapshot_json)
        .where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
        .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
        .limit(1)
    )
    if not isinstance(payload, dict):
        return None
    version = payload.get("calculation_version")
    return str(version) if version not in (None, "") else None


def _incremental_snapshot_seed(
    session,
    portfolio_id: str,
    *,
    dirty_from: date | None,
) -> dict[str, object] | None:
    """Return a reliable prefix seed for an exact dirty-suffix refresh."""

    if dirty_from is None:
        return None
    if _latest_snapshot_calculation_version(session, portfolio_id) != DAILY_SNAPSHOT_CALCULATION_VERSION:
        return None
    prior_row = session.scalar(
        select(PortfolioDailySnapshotModel)
        .where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.as_of_date < dirty_from,
            PortfolioDailySnapshotModel.valuation_coverage_state == "complete",
            PortfolioDailySnapshotModel.nav.is_not(None),
        )
        .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
        .limit(1)
    )
    if prior_row is None:
        return None
    prior_payload = _restore_snapshot(dict(prior_row.snapshot_json))
    prefix_rows = session.scalars(
        select(PortfolioDailySnapshotModel)
        .where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.as_of_date <= prior_row.as_of_date,
        )
        .order_by(PortfolioDailySnapshotModel.as_of_date)
    ).all()
    prefix_growth_values = [
        1.0 + cumulative
        for row in prefix_rows
        if (cumulative := _safe_float(row.cumulative_twr)) is not None
    ]
    prefix_market_risk_growth_values = [
        1.0 + cumulative
        for row in prefix_rows
        if isinstance(row.snapshot_json, dict)
        and (
            cumulative := _safe_float(
                row.snapshot_json.get("market_risk_cumulative_return")
            )
        )
        is not None
    ]
    return {
        "seed_date": prior_row.as_of_date,
        "persist_from": dirty_from,
        "cumulative_twr": _safe_float(prior_payload.get("cumulative_twr")),
        "peak_growth": max([1.0, *prefix_growth_values]),
        "return_chain_continuous": bool(
            prior_payload.get("return_chain_continuous", prior_row.cumulative_twr is not None)
        ),
        "market_risk_cumulative_return": _safe_float(
            prior_payload.get("market_risk_cumulative_return")
        ),
        "market_risk_peak_growth": max(
            [1.0, *prefix_market_risk_growth_values]
        ),
        "market_risk_return_chain_continuous": bool(
            prior_payload.get("market_risk_return_chain_continuous", True)
        ),
        "cash_currency_gains": _safe_float(prior_payload.get("cash_currency_gains")),
        "pending_settlement_currency_gains": _safe_float(
            prior_payload.get("pending_settlement_currency_gains")
        ),
        "instrument_currency_gains": _safe_float(prior_payload.get("instrument_currency_gains")),
        "total_pnl": _safe_float(prior_payload.get("total_pnl")),
    }


def _rebase_incremental_snapshots(
    snapshots: list[dict[str, object]],
    *,
    seed: dict[str, object],
) -> None:
    seed_date = _parse_date(seed.get("seed_date"))
    if seed_date is None:
        return
    cumulative_twr = _safe_float(seed.get("cumulative_twr"))
    growth_index = 1.0 + cumulative_twr if cumulative_twr is not None else 1.0
    peak_growth_index = max(_safe_float(seed.get("peak_growth")) or 1.0, growth_index)
    has_return_history = cumulative_twr is not None
    return_chain_continuous = bool(seed.get("return_chain_continuous", True))
    market_risk_cumulative_return = _safe_float(
        seed.get("market_risk_cumulative_return")
    )
    market_risk_growth_index = (
        1.0 + market_risk_cumulative_return
        if market_risk_cumulative_return is not None
        else 1.0
    )
    market_risk_peak_growth_index = max(
        _safe_float(seed.get("market_risk_peak_growth")) or 1.0,
        market_risk_growth_index,
    )
    has_market_risk_return_history = market_risk_cumulative_return is not None
    market_risk_return_chain_continuous = bool(
        seed.get("market_risk_return_chain_continuous", True)
    )
    prefix_cash_fx = _safe_float(seed.get("cash_currency_gains"))
    prefix_pending_settlement_fx = _safe_float(
        seed.get("pending_settlement_currency_gains")
    )
    prefix_instrument_fx = _safe_float(seed.get("instrument_currency_gains"))
    prefix_total_pnl = _safe_float(seed.get("total_pnl"))

    for snapshot in snapshots:
        snapshot_date = _parse_date(snapshot.get("as_of_date"))
        if snapshot_date is None or snapshot_date <= seed_date:
            continue
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        point_return_complete = (
            str(snapshot.get("return_coverage_state") or "unavailable") == "complete"
            and daily_twr is not None
        )
        if not point_return_complete:
            return_chain_continuous = False
        elif return_chain_continuous:
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)
        snapshot["return_chain_continuous"] = return_chain_continuous
        snapshot["cumulative_twr"] = (
            growth_index - 1.0
            if has_return_history and return_chain_continuous
            else None
        )
        snapshot["drawdown"] = (
            growth_index / peak_growth_index - 1.0
            if has_return_history and return_chain_continuous and peak_growth_index > 1e-12
            else None
        )

        market_risk_daily_return = _safe_float(
            snapshot.get("market_risk_daily_return")
        )
        if snapshot.get("market_risk_return_coverage_state") == "partial":
            market_risk_return_chain_continuous = False
        elif (
            bool(snapshot.get("market_risk_return_observation_eligible"))
            and market_risk_daily_return is not None
            and market_risk_return_chain_continuous
        ):
            has_market_risk_return_history = True
            market_risk_growth_index *= 1.0 + market_risk_daily_return
            market_risk_peak_growth_index = max(
                market_risk_peak_growth_index,
                market_risk_growth_index,
            )
        snapshot["market_risk_return_chain_continuous"] = (
            market_risk_return_chain_continuous
        )
        snapshot["market_risk_cumulative_return"] = (
            market_risk_growth_index - 1.0
            if has_market_risk_return_history
            and market_risk_return_chain_continuous
            else None
        )
        snapshot["market_risk_drawdown"] = (
            market_risk_growth_index / market_risk_peak_growth_index - 1.0
            if has_market_risk_return_history
            and market_risk_return_chain_continuous
            and market_risk_peak_growth_index > 1e-12
            else None
        )

        local_cash_fx = _safe_float(snapshot.get("cash_currency_gains"))
        local_pending_settlement_fx = _safe_float(
            snapshot.get("pending_settlement_currency_gains")
        )
        local_instrument_fx = _safe_float(snapshot.get("instrument_currency_gains"))
        snapshot["cash_currency_gains"] = (
            prefix_cash_fx + local_cash_fx
            if prefix_cash_fx is not None and local_cash_fx is not None
            else None
        )
        snapshot["pending_settlement_currency_gains"] = (
            prefix_pending_settlement_fx + local_pending_settlement_fx
            if (
                prefix_pending_settlement_fx is not None
                and local_pending_settlement_fx is not None
            )
            else None
        )
        snapshot["instrument_currency_gains"] = (
            prefix_instrument_fx + local_instrument_fx
            if prefix_instrument_fx is not None and local_instrument_fx is not None
            else None
        )
        total_pnl = _safe_float(snapshot.get("total_pnl"))
        snapshot["total_pnl"] = (
            prefix_total_pnl + total_pnl
            if prefix_total_pnl is not None and total_pnl is not None
            else None
        )


def _current_refresh_result(session, portfolio_id: str) -> dict[str, object] | None:
    state = session.get(PortfolioCalculationStateModel, portfolio_id)
    if state is None:
        return None
    return {
        "portfolio_id": portfolio_id,
        "snapshot_count": _snapshot_count(session, portfolio_id),
        "refreshed_from": state.refreshed_from,
        "refreshed_to": state.refreshed_to,
        "refreshed_at": state.refreshed_at,
        "source_market_data_updated_at": state.source_market_data_updated_at,
        "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
        "source_generation_status": "stable",
        "source_generation_reason": None,
        "discarded_attempt_count": 0,
        "source_generation_before": None,
        "source_generation_after": None,
    }


def _discard_source_generation_attempt(
    portfolio_id: str,
    *,
    request_id: str,
    claimed_started_at: str | None,
    source_generation_before: _SnapshotSourceGeneration | None,
    source_generation_after: _SnapshotSourceGeneration | None,
    reason: str,
) -> dict[str, object]:
    """Leave published rows untouched and make the interrupted generation retryable."""

    completed_at = _current_utc_timestamp()
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if (
            state is not None
            and state.daily_snapshot_status == "running"
            and state.refresh_started_at == claimed_started_at
        ):
            # An unannounced registry update does not advance refresh_request_id.
            # Give the retry a distinct fact-generation identity in that case.
            if state.refresh_request_id == request_id:
                state.refresh_request_id = _new_refresh_request_id()
            state.daily_snapshot_status = "stale"
            state.refresh_completed_at = completed_at
            state.error_message = reason

        result = {
            "portfolio_id": portfolio_id,
            "snapshot_count": _snapshot_count(session, portfolio_id),
            "refreshed_from": state.refreshed_from if state is not None else None,
            "refreshed_to": state.refreshed_to if state is not None else None,
            "refreshed_at": state.refreshed_at if state is not None else None,
            "source_market_data_updated_at": (
                state.source_market_data_updated_at if state is not None else None
            ),
            "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
            "source_generation_status": "discarded",
            "source_generation_reason": reason,
            "discarded_attempt_count": 1,
            "source_generation_before": (
                source_generation_before.as_payload()
                if source_generation_before is not None
                else None
            ),
            "source_generation_after": (
                source_generation_after.as_payload()
                if source_generation_after is not None
                else None
            ),
        }
        session.commit()
        return result


def _mark_portfolio_daily_snapshots_stale_in_session(
    session,
    portfolio_id: str,
    *,
    dirty_from: date | None,
) -> dict[str, object] | None:
    portfolio = session.scalar(
        select(PortfolioRecordModel)
        .where(PortfolioRecordModel.portfolio_id == portfolio_id)
        .with_for_update()
    )
    if portfolio is None:
        return None
    state = _state_for_portfolio(session, portfolio_id)

    state_model = PortfolioCalculationStateModel
    values: dict[str, object] = {
        "refresh_request_id": _new_refresh_request_id(),
        "daily_snapshot_status": case(
            (state_model.daily_snapshot_status == "running", "running"),
            else_="stale",
        ),
        "error_message": None,
    }
    if dirty_from is None:
        # ``stale/running + NULL`` is the established representation of a
        # full rebuild.  A full request must override any queued partial one.
        values["dirty_from"] = None
    else:
        # Compute the minimum in the UPDATE itself.  Concurrent enqueue calls
        # therefore cannot overwrite an earlier invalidation with a later date.
        # Preserve NULL when it already denotes a queued/running full rebuild;
        # NULL on a current row still means clean and accepts the partial date.
        values["dirty_from"] = case(
            (
                and_(
                    state_model.daily_snapshot_status.in_(("stale", "running")),
                    state_model.dirty_from.is_(None),
                ),
                None,
            ),
            (state_model.dirty_from.is_(None), dirty_from),
            (state_model.dirty_from > dirty_from, dirty_from),
            else_=state_model.dirty_from,
        )
    session.execute(
        update(state_model)
        .where(state_model.portfolio_id == portfolio_id)
        .values(**values)
    )
    session.flush()
    session.expire(state)
    return {
        "portfolio_id": portfolio_id,
        "status": "accepted",
        "daily_snapshot_status": state.daily_snapshot_status,
        "refresh_request_id": state.refresh_request_id,
        "dirty_from": state.dirty_from,
    }


def mark_portfolio_daily_snapshots_stale(
    portfolio_id: str,
    dirty_from: date | None = None,
    *,
    session=None,
) -> None:
    if session is not None:
        _mark_portfolio_daily_snapshots_stale_in_session(
            session,
            portfolio_id,
            dirty_from=dirty_from,
        )
        return

    session_factory = get_session_factory()
    with session_factory() as owned_session:
        _mark_portfolio_daily_snapshots_stale_in_session(
            owned_session,
            portfolio_id,
            dirty_from=dirty_from,
        )
        owned_session.commit()


def _wake_daily_snapshot_worker() -> None:
    # Imported lazily to keep the calculation service independent from the
    # worker lifecycle module while still avoiding poll latency for API jobs.
    from portfolio_app.services.daily_snapshot_worker import (
        wake_daily_snapshot_recalculation_worker,
    )

    wake_daily_snapshot_recalculation_worker()


def enqueue_selected_portfolio_daily_snapshot_recalculations(
    portfolio_ids: list[str],
    *,
    dirty_from: date | None = None,
) -> list[dict[str, object]]:
    normalized_portfolio_ids = list(
        dict.fromkeys(
            str(portfolio_id).strip()
            for portfolio_id in portfolio_ids
            if str(portfolio_id).strip()
        )
    )
    accepted: list[dict[str, object]] = []
    session_factory = get_session_factory()
    with session_factory() as session:
        for portfolio_id in normalized_portfolio_ids:
            result = _mark_portfolio_daily_snapshots_stale_in_session(
                session,
                portfolio_id,
                dirty_from=dirty_from,
            )
            if result is not None:
                accepted.append(result)
        session.commit()
    if accepted:
        _wake_daily_snapshot_worker()
    return accepted


def _claim_daily_snapshot_refresh(portfolio_id: str) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return {"status": "missing"}
        state = _state_for_portfolio(session, portfolio_id)

        snapshot_count = _snapshot_count(session, portfolio_id)
        if state.daily_snapshot_status == "current" and snapshot_count > 0:
            if (
                _latest_snapshot_calculation_version(session, portfolio_id)
                == DAILY_SNAPSHOT_CALCULATION_VERSION
                and _source_market_data_is_current(session, portfolio_id, state)
            ):
                return {
                    "status": "current",
                    "result": _current_refresh_result(session, portfolio_id),
                }
            state.daily_snapshot_status = "stale"
            state.refresh_request_id = _new_refresh_request_id()
            state.error_message = None
            session.flush()
        if state.daily_snapshot_status == "running":
            if not _running_refresh_lease_expired(state):
                return {"status": "running"}
            previous_request_id = state.refresh_request_id
            previous_started_at = state.refresh_started_at
            replacement_request_id = _new_refresh_request_id()
            takeover_result = session.execute(
                update(PortfolioCalculationStateModel)
                .where(PortfolioCalculationStateModel.portfolio_id == portfolio_id)
                .where(PortfolioCalculationStateModel.daily_snapshot_status == "running")
                .where(PortfolioCalculationStateModel.refresh_request_id == previous_request_id)
                .where(PortfolioCalculationStateModel.refresh_started_at == previous_started_at)
                .values(
                    refresh_request_id=replacement_request_id,
                    refresh_started_at=_current_utc_timestamp(),
                    refresh_completed_at=None,
                    error_message=None,
                )
            )
            session.commit()
            if int(takeover_result.rowcount or 0) == 0:
                return {"status": "running"}
            return {"status": "claimed", "request_id": replacement_request_id}

        request_id = state.refresh_request_id or _new_refresh_request_id()
        state.refresh_request_id = request_id
        session.flush()
        claim_result = session.execute(
            update(PortfolioCalculationStateModel)
            .where(PortfolioCalculationStateModel.portfolio_id == portfolio_id)
            .where(PortfolioCalculationStateModel.daily_snapshot_status != "running")
            .where(PortfolioCalculationStateModel.refresh_request_id == request_id)
            .values(
                daily_snapshot_status="running",
                refresh_started_at=_current_utc_timestamp(),
                refresh_completed_at=None,
                error_message=None,
            )
        )
        session.commit()
        if int(claim_result.rowcount or 0) == 0:
            return {"status": "running"}
        return {"status": "claimed", "request_id": request_id}


def _recalculate_portfolio_daily_snapshots_once(
    portfolio_id: str,
    *,
    request_id: str,
    end_date: date | None = None,
) -> tuple[dict[str, object] | None, bool]:
    session_factory = get_session_factory()
    claimed_started_at: str | None = None

    try:
        with session_factory() as session:
            portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
            if portfolio_record is None:
                return None, False
            claimed_state = session.get(PortfolioCalculationStateModel, portfolio_id)
            if claimed_state is None or claimed_state.refresh_request_id != request_id:
                return None, True
            claimed_started_at = claimed_state.refresh_started_at
            claimed_dirty_from = claimed_state.dirty_from
            account_records = _load_accounts(session, portfolio_id)
            transaction_records = _load_transactions(session, portfolio_id)
            resolved_end_date = end_date or _resolve_live_portfolio_as_of_date(
                session,
                portfolio_record,
                accounts=account_records,
                transactions=transaction_records,
            )
            portfolio = _serialize_portfolio_row(portfolio_record)
            portfolio["as_of_date"] = resolved_end_date.isoformat()
            accounts = [_serialize_account_row(item) for item in account_records]
            transactions = [_serialize_transaction_row(item) for item in transaction_records]
            source_market_data_updated_at = _source_market_data_watermark(session, portfolio_id)
            analytics_state = session.get(
                PortfolioAnalyticsPolicyStateModel,
                portfolio_id,
            )
            source_generation_before = _SnapshotSourceGeneration(
                refresh_request_id=request_id,
                market_data_updated_at=source_market_data_updated_at,
                analytics_policy_version=(
                    int(analytics_state.current_version)
                    if analytics_state is not None
                    else 0
                ),
            )
            incremental_seed = (
                _incremental_snapshot_seed(
                    session,
                    portfolio_id,
                    dirty_from=claimed_dirty_from,
                )
                if claimed_dirty_from is not None and claimed_dirty_from <= resolved_end_date
                else None
            )
            calculation_start_date = (
                _parse_date(incremental_seed.get("seed_date"))
                if incremental_seed is not None
                else None
            )

            snapshots = performance.build_daily_portfolio_snapshots(
                portfolio,
                accounts,
                transactions,
                start_date=calculation_start_date,
                end_date=resolved_end_date,
                include_materialized_rows=True,
            )
            if incremental_seed is not None:
                _rebase_incremental_snapshots(snapshots, seed=incremental_seed)
            source_generation_after = _read_snapshot_source_generation(portfolio_id)
            if source_generation_after != source_generation_before:
                session.rollback()
                return (
                    _discard_source_generation_attempt(
                        portfolio_id,
                        request_id=request_id,
                        claimed_started_at=claimed_started_at,
                        source_generation_before=source_generation_before,
                        source_generation_after=source_generation_after,
                        reason=_SOURCE_GENERATION_CHANGED_REASON,
                    ),
                    True,
                )
            calculated_at = _current_utc_timestamp()

            persist_from = (
                _parse_date(incremental_seed.get("persist_from"))
                if incremental_seed is not None
                else None
            )
            snapshots_to_persist = [
                snapshot
                for snapshot in snapshots
                if persist_from is None
                or (_parse_date(snapshot.get("as_of_date")) or date.min) >= persist_from
            ]

            contribution_delete = delete(PortfolioDailyContributionSliceModel).where(
                PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id
            )
            holding_delete = delete(PortfolioDailyHoldingSnapshotModel).where(
                PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id
            )
            snapshot_delete = delete(PortfolioDailySnapshotModel).where(
                PortfolioDailySnapshotModel.portfolio_id == portfolio_id
            )
            if persist_from is not None:
                contribution_delete = contribution_delete.where(
                    PortfolioDailyContributionSliceModel.as_of_date >= persist_from
                )
                holding_delete = holding_delete.where(
                    PortfolioDailyHoldingSnapshotModel.as_of_date >= persist_from
                )
                snapshot_delete = snapshot_delete.where(
                    PortfolioDailySnapshotModel.as_of_date >= persist_from
                )
            session.execute(contribution_delete)
            session.execute(holding_delete)
            session.execute(snapshot_delete)
            daily_snapshot_rows: list[dict[str, object]] = []
            for snapshot in snapshots_to_persist:
                snapshot_date = _parse_date(snapshot.get("as_of_date"))
                if snapshot_date is None:
                    continue
                holding_rows = [
                    item for item in list(snapshot.get("_holding_rows") or []) if isinstance(item, dict)
                ]
                contribution_slices = [
                    item for item in list(snapshot.get("_contribution_slices") or []) if isinstance(item, dict)
                ]
                public_snapshot = _public_snapshot_payload(snapshot)
                public_snapshot["calculation_version"] = DAILY_SNAPSHOT_CALCULATION_VERSION
                public_snapshot["source_generation"] = source_generation_before.as_payload()
                daily_snapshot_rows.append(
                    {
                        "portfolio_id": portfolio_id,
                        "as_of_date": snapshot_date,
                        "coverage_state": str(snapshot.get("coverage_state") or "unavailable"),
                        "valuation_coverage_state": str(
                            snapshot.get("valuation_coverage_state") or "unavailable"
                        ),
                        "return_coverage_state": str(
                            snapshot.get("return_coverage_state") or "unavailable"
                        ),
                        "book_pnl_coverage_state": str(
                            snapshot.get("book_pnl_coverage_state") or "unavailable"
                        ),
                        "attribution_coverage_state": str(
                            snapshot.get("attribution_coverage_state") or "unavailable"
                        ),
                        "nav": _safe_float(snapshot.get("nav")),
                        "beginning_nav": _safe_float(snapshot.get("beginning_nav")),
                        "ending_nav": _safe_float(snapshot.get("ending_nav")),
                        "daily_twr": _safe_float(snapshot.get("daily_twr")),
                        "cumulative_twr": _safe_float(snapshot.get("cumulative_twr")),
                        "drawdown": _safe_float(snapshot.get("drawdown")),
                        "snapshot_json": _json_safe(public_snapshot),
                        "calculated_at": calculated_at,
                    }
                )
                for holding in holding_rows:
                    account_id = str(holding.get("account_id") or "")
                    instrument_id = (
                        str(holding.get("instrument_id") or "") or None
                    )
                    derivative_contract_id = (
                        str(holding.get("derivative_contract_id") or "")
                        or None
                    )
                    position_reference_id = str(
                        holding.get("position_reference_id")
                        or derivative_contract_id
                        or instrument_id
                        or ""
                    )
                    if not account_id or not position_reference_id:
                        continue
                    holding_kind = (
                        str(holding.get("holding_kind") or "position").strip()
                        or "position"
                    )
                    session.add(
                        PortfolioDailyHoldingSnapshotModel(
                            portfolio_id=portfolio_id,
                            as_of_date=snapshot_date,
                            account_id=account_id,
                            position_reference_id=position_reference_id,
                            instrument_id=instrument_id,
                            derivative_contract_id=derivative_contract_id,
                            holding_kind=holding_kind,
                            currency=str(holding.get("currency") or portfolio.get("base_currency") or "USD"),
                            quantity=_safe_float(holding.get("quantity")) or 0.0,
                            cost_basis=_safe_float(holding.get("cost_basis")),
                            cost_basis_base=_safe_float(holding.get("cost_basis_base")),
                            last_price=_safe_float(holding.get("last_price")),
                            market_value=_safe_float(holding.get("market_value")),
                            market_value_base=_safe_float(holding.get("market_value_base")),
                            portfolio_weight=_safe_float(holding.get("portfolio_weight")),
                            holding_json=_json_safe(holding),  # type: ignore[arg-type]
                            calculated_at=calculated_at,
                        )
                    )
                for contribution_slice in contribution_slices:
                    axis = str(contribution_slice.get("axis") or "")
                    group_key = str(contribution_slice.get("group_key") or "")
                    if axis not in attribution.MATERIALIZED_CONTRIBUTION_AXES or not group_key:
                        continue
                    session.add(
                        PortfolioDailyContributionSliceModel(
                            portfolio_id=portfolio_id,
                            as_of_date=snapshot_date,
                            axis=axis,
                            group_key=group_key,
                            group_label=str(contribution_slice.get("group_label") or group_key),
                            coverage_state=str(contribution_slice.get("coverage_state") or "unavailable"),
                            total_pnl=_safe_float(contribution_slice.get("total_pnl")),
                            daily_contribution=_safe_float(contribution_slice.get("daily_contribution")),
                            slice_json=_json_safe(contribution_slice),  # type: ignore[arg-type]
                            calculated_at=calculated_at,
                        )
                    )

            session.add_all(
                PortfolioDailySnapshotModel(**row)
                for row in daily_snapshot_rows
            )
            session.flush()
            latest_snapshot = snapshots[-1] if snapshots else None
            portfolio_record.as_of_date = resolved_end_date
            if latest_snapshot is not None:
                portfolio_record.nav = _safe_float(latest_snapshot.get("nav"))
                # Portfolio day change is investment P&L, not the raw NAV
                # movement.  Using absolute_change would report subscriptions
                # and inception funding as investment gains even though the
                # paired daily TWR is cash-flow neutral.
                portfolio_record.day_change_value = _safe_float(latest_snapshot.get("delta"))
                portfolio_record.day_change_pct = _safe_float(latest_snapshot.get("daily_twr"))
                portfolio_record.securities_count = int(latest_snapshot.get("total_position_count") or 0)
            else:
                portfolio_record.nav = None
                portfolio_record.day_change_value = None
                portfolio_record.day_change_pct = None
                portfolio_record.securities_count = 0

            state = _state_for_portfolio(session, portfolio_id)
            refreshed_from = session.scalar(
                select(func.min(PortfolioDailySnapshotModel.as_of_date)).where(
                    PortfolioDailySnapshotModel.portfolio_id == portfolio_id
                )
            )
            refreshed_to = session.scalar(
                select(func.max(PortfolioDailySnapshotModel.as_of_date)).where(
                    PortfolioDailySnapshotModel.portfolio_id == portfolio_id
                )
            )
            snapshot_count = _snapshot_count(session, portfolio_id)
            update_result = session.execute(
                update(PortfolioCalculationStateModel)
                .where(PortfolioCalculationStateModel.portfolio_id == portfolio_id)
                .where(PortfolioCalculationStateModel.refresh_request_id == request_id)
                .where(PortfolioCalculationStateModel.daily_snapshot_status == "running")
                .values(
                    daily_snapshot_status="current",
                    dirty_from=None,
                    refreshed_from=refreshed_from,
                    refreshed_to=refreshed_to,
                    refreshed_at=calculated_at,
                    source_market_data_updated_at=source_market_data_updated_at,
                    refresh_request_id=None,
                    refresh_completed_at=calculated_at,
                    error_message=None,
                )
            )
            request_superseded = int(update_result.rowcount or 0) == 0
            if request_superseded:
                session.rollback()
                source_generation_before_publish = _read_snapshot_source_generation(portfolio_id)
                return (
                    _discard_source_generation_attempt(
                        portfolio_id,
                        request_id=request_id,
                        claimed_started_at=claimed_started_at,
                        source_generation_before=source_generation_before,
                        source_generation_after=source_generation_before_publish,
                        reason=_SOURCE_GENERATION_CHANGED_BEFORE_PUBLISH_REASON,
                    ),
                    True,
                )
            session.commit()

            return {
                "portfolio_id": portfolio_id,
                "snapshot_count": snapshot_count,
                "refreshed_from": refreshed_from,
                "refreshed_to": refreshed_to,
                "refreshed_at": calculated_at,
                "source_market_data_updated_at": source_market_data_updated_at,
                "recalculated_from": persist_from or refreshed_from,
                "source_generation_status": "stable",
                "source_generation_reason": None,
                "discarded_attempt_count": 0,
                "source_generation_before": source_generation_before.as_payload(),
                "source_generation_after": source_generation_after.as_payload(),
            }, False
    except Exception as error:
        with session_factory() as session:
            if session.get(PortfolioRecordModel, portfolio_id) is not None:
                completed_at = _current_utc_timestamp()
                update_result = session.execute(
                    update(PortfolioCalculationStateModel)
                    .where(PortfolioCalculationStateModel.portfolio_id == portfolio_id)
                    .where(PortfolioCalculationStateModel.refresh_request_id == request_id)
                    .values(
                        daily_snapshot_status="failed",
                        refresh_completed_at=completed_at,
                        error_message=str(error),
                    )
                )
                if int(update_result.rowcount or 0) == 0:
                    state = _state_for_portfolio(session, portfolio_id)
                    if (
                        state.daily_snapshot_status == "running"
                        and state.refresh_started_at == claimed_started_at
                    ):
                        state.daily_snapshot_status = "stale"
                        state.refresh_completed_at = completed_at
                        state.error_message = str(error)
                session.commit()
        raise


def _run_portfolio_daily_snapshot_recalculation_synchronously(
    portfolio_id: str,
    end_date: date | None = None,
) -> dict[str, object] | None:
    """Run one claimed recalculation job for the queue worker.

    This is deliberately internal.  Request handlers enqueue generations and
    return; only the worker and read-through materialization invoke the
    synchronous calculation kernel.
    """

    lock = _refresh_lock_for_portfolio(portfolio_id)
    with lock:
        discarded_attempt_count = 0
        last_discarded_result: dict[str, object] | None = None
        while True:
            claim = _claim_daily_snapshot_refresh(portfolio_id)
            claim_status = str(claim.get("status") or "")
            if claim_status == "missing":
                return None
            if claim_status == "current":
                result = claim.get("result")
                if isinstance(result, dict) and last_discarded_result is not None:
                    result["source_generation_status"] = "stable_after_retry"
                    result["source_generation_reason"] = last_discarded_result.get(
                        "source_generation_reason"
                    )
                    result["discarded_attempt_count"] = discarded_attempt_count
                    result["source_generation_before"] = last_discarded_result.get(
                        "source_generation_before"
                    )
                    result["source_generation_after"] = last_discarded_result.get(
                        "source_generation_after"
                    )
                return result if isinstance(result, dict) else None
            if claim_status == "running":
                # Another process owns a live database claim.  Do not make the
                # single queue worker wait behind it and starve other jobs.
                return None

            request_id = str(claim.get("request_id") or "")
            if not request_id:
                continue
            result, request_superseded = _recalculate_portfolio_daily_snapshots_once(
                portfolio_id,
                request_id=request_id,
                end_date=end_date,
            )
            if request_superseded:
                if (
                    isinstance(result, dict)
                    and result.get("source_generation_status") == "discarded"
                ):
                    discarded_attempt_count += 1
                    result["discarded_attempt_count"] = discarded_attempt_count
                    last_discarded_result = result
                    if discarded_attempt_count >= _SOURCE_GENERATION_MAX_DISCARDS:
                        return result
                continue
            if isinstance(result, dict) and last_discarded_result is not None:
                result["source_generation_status"] = "stable_after_retry"
                result["source_generation_reason"] = last_discarded_result.get(
                    "source_generation_reason"
                )
                result["discarded_attempt_count"] = discarded_attempt_count
                result["source_generation_before"] = last_discarded_result.get(
                    "source_generation_before"
                )
                result["source_generation_after"] = last_discarded_result.get(
                    "source_generation_after"
                )
            return result


def _portfolio_ids_for_instrument_change(
    session,
    *,
    instrument_ids: list[str],
    refresh_all: bool,
) -> list[str]:
    if refresh_all:
        return list(session.scalars(select(PortfolioRecordModel.portfolio_id)).all())
    normalized_instrument_ids = list(dict.fromkeys(instrument_id.strip() for instrument_id in instrument_ids if instrument_id.strip()))
    if not normalized_instrument_ids:
        return []
    return list(
        session.scalars(
            select(TransactionRecordModel.portfolio_id)
            .where(TransactionRecordModel.instrument_id.in_(normalized_instrument_ids))
            .distinct()
            .order_by(TransactionRecordModel.portfolio_id)
        ).all()
    )


def enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change(
    *,
    instrument_ids: list[str],
    dirty_from: date | None = None,
    refresh_all: bool = False,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_ids = _portfolio_ids_for_instrument_change(
            session,
            instrument_ids=instrument_ids,
            refresh_all=refresh_all,
        )
    return enqueue_selected_portfolio_daily_snapshot_recalculations(
        portfolio_ids,
        dirty_from=dirty_from,
    )


def _next_daily_snapshot_recalculation_candidate() -> str | None:
    """Return one stale or abandoned portfolio for a database-claim attempt."""

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            select(PortfolioRecordModel.portfolio_id, PortfolioCalculationStateModel)
            .outerjoin(
                PortfolioCalculationStateModel,
                PortfolioCalculationStateModel.portfolio_id
                == PortfolioRecordModel.portfolio_id,
            )
            .where(
                or_(
                    PortfolioCalculationStateModel.portfolio_id.is_(None),
                    PortfolioCalculationStateModel.daily_snapshot_status.in_(
                        ("stale", "running")
                    ),
                )
            )
            .order_by(
                case(
                    (
                        PortfolioCalculationStateModel.daily_snapshot_status
                        == "stale",
                        0,
                    ),
                    (PortfolioCalculationStateModel.portfolio_id.is_(None), 1),
                    else_=2,
                ),
                PortfolioCalculationStateModel.dirty_from,
                PortfolioCalculationStateModel.refresh_started_at,
                PortfolioRecordModel.portfolio_id,
            )
            .limit(_DAILY_SNAPSHOT_QUEUE_SCAN_LIMIT)
        ).all()
        for portfolio_id, state in rows:
            if state is None or state.daily_snapshot_status == "stale":
                return str(portfolio_id)
            if (
                state.daily_snapshot_status == "running"
                and _running_refresh_lease_expired(state)
            ):
                return str(portfolio_id)
    return None


def _reconcile_materialized_source_generation_batch(
    *,
    after_portfolio_id: str | None,
    batch_size: int,
) -> tuple[str | None, str | None]:
    """Boundedly discover current snapshots whose source facts changed silently.

    The queue notification is an optimization, not the source of truth.  This
    pass lets a restarted worker recover a missed Platform callback by comparing
    the persisted materialization generation with the Registry watermark.
    """

    if batch_size <= 0:
        raise ValueError("Daily snapshot reconciliation batch_size must be positive.")
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_ids_with_instrument_history = select(
            TransactionRecordModel.portfolio_id
        ).where(TransactionRecordModel.instrument_id.is_not(None))
        portfolio_ids_with_snapshots = select(
            PortfolioDailySnapshotModel.portfolio_id
        )
        statement = (
            select(PortfolioCalculationStateModel.portfolio_id)
            .where(
                PortfolioCalculationStateModel.daily_snapshot_status == "current",
                PortfolioCalculationStateModel.portfolio_id.in_(
                    portfolio_ids_with_instrument_history
                ),
                PortfolioCalculationStateModel.portfolio_id.in_(
                    portfolio_ids_with_snapshots
                ),
            )
            .order_by(PortfolioCalculationStateModel.portfolio_id)
            .limit(batch_size)
        )
        if after_portfolio_id is not None:
            statement = statement.where(
                PortfolioCalculationStateModel.portfolio_id > after_portfolio_id
            )
        portfolio_ids = list(session.scalars(statement).all())
        next_cursor = (
            str(portfolio_ids[-1])
            if len(portfolio_ids) == batch_size
            else None
        )
        for portfolio_id in portfolio_ids:
            if not _state_requires_refresh(session, str(portfolio_id)):
                continue
            accepted = _mark_portfolio_daily_snapshots_stale_in_session(
                session,
                str(portfolio_id),
                dirty_from=None,
            )
            session.commit()
            return (
                str(portfolio_id) if accepted is not None else None,
                str(portfolio_id),
            )
        return None, next_cursor


def _state_requires_refresh(session, portfolio_id: str) -> bool:
    state = session.get(PortfolioCalculationStateModel, portfolio_id)
    if state is None or state.daily_snapshot_status != "current":
        return True
    if _snapshot_count(session, portfolio_id) == 0:
        return True
    if _latest_snapshot_calculation_version(session, portfolio_id) != DAILY_SNAPSHOT_CALCULATION_VERSION:
        return True
    return not _source_market_data_is_current(session, portfolio_id, state)


def ensure_portfolio_daily_snapshots(portfolio_id: str) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get(PortfolioRecordModel, portfolio_id) is None:
            return
        should_refresh = _state_requires_refresh(session, portfolio_id)
    if should_refresh:
        _run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)


def list_materialized_daily_snapshots(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    ensure_current: bool = True,
) -> list[dict[str, object]]:
    if ensure_current:
        ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(PortfolioDailySnapshotModel).where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id
        )
        if start_date is not None:
            statement = statement.where(PortfolioDailySnapshotModel.as_of_date >= start_date)
        if end_date is not None:
            statement = statement.where(PortfolioDailySnapshotModel.as_of_date <= end_date)
        rows = session.scalars(
            statement.order_by(PortfolioDailySnapshotModel.as_of_date)
        ).all()
        return [_restore_snapshot(dict(row.snapshot_json)) for row in rows]


def get_materialized_daily_snapshot(portfolio_id: str, as_of_date: date) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        if _state_requires_refresh(session, portfolio_id):
            return None
        row = session.scalar(
            select(PortfolioDailySnapshotModel).where(
                PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
                PortfolioDailySnapshotModel.as_of_date == as_of_date,
            )
        )
        return _restore_snapshot(dict(row.snapshot_json)) if row is not None else None


def build_materialized_performance_report(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object] | None:
    ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return None
        portfolio = _serialize_portfolio_row(portfolio_record)
        transactions = [_serialize_transaction_row(item) for item in _load_transactions(session, portfolio_id)]

    snapshot_context_start = (
        start_date - timedelta(days=1)
        if start_date is not None
        else None
    )
    snapshots = list_materialized_daily_snapshots(
        portfolio_id,
        start_date=snapshot_context_start,
        end_date=end_date,
        ensure_current=False,
    )
    return performance.build_portfolio_performance_report_from_snapshots(
        portfolio,
        snapshots,
        transactions=transactions,
        start_date=start_date,
        end_date=end_date,
    )


def _sum_complete(values: list[object]) -> float | None:
    total = 0.0
    for value in values:
        numeric_value = _safe_float(value)
        if numeric_value is None:
            return None
        total += numeric_value
    return total


def _first_present(rows: list[dict[str, object]], key: str) -> object | None:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _earliest_holding_profile_row(
    rows: list[dict[str, object]],
) -> dict[str, object]:
    dated_rows = [
        (holding_start_date, index, row)
        for index, row in enumerate(rows)
        if (
            holding_start_date := _parse_date(
                row.get("instrument_holding_start_date")
            )
        )
        is not None
    ]
    if dated_rows:
        return min(dated_rows, key=lambda item: (item[0], item[1]))[2]
    return rows[0] if rows else {}


def _is_cash_holding_payload(row: dict[str, object]) -> bool:
    instrument_core = row.get("instrument_ref") if isinstance(row.get("instrument_ref"), dict) else None
    instrument_type = str((instrument_core or {}).get("instrument_type") or "").strip().lower()
    return instrument_type == "cash" or holdings_market_profile.is_cash_holding_instrument_id(
        row.get("instrument_id") or row.get("line_id")
    )


def _is_pending_monetary_holding_payload(row: dict[str, object]) -> bool:
    return holdings_market_profile.is_pending_monetary_holding(row)


def _aggregate_holding_rows(
    rows: list[PortfolioDailyHoldingSnapshotModel],
    *,
    total_nav_base: float | None,
) -> list[dict[str, object]]:
    rows_by_reference: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        payload = dict(row.holding_json) if isinstance(row.holding_json, dict) else {}
        payload["_snapshot_as_of_date"] = row.as_of_date
        position_reference_id = row.position_reference_id
        instrument_id = row.instrument_id
        derivative_contract_id = row.derivative_contract_id
        holding_kind = row.holding_kind
        payload["position_reference_id"] = position_reference_id
        payload["instrument_id"] = instrument_id
        payload["derivative_contract_id"] = derivative_contract_id
        payload["holding_kind"] = holding_kind
        rows_by_reference.setdefault(
            (position_reference_id, holding_kind),
            [],
        ).append(
            payload
        )

    aggregated_rows: list[dict[str, object]] = []
    for (
        position_reference_id,
        holding_kind,
    ), instrument_rows in rows_by_reference.items():
        account_ids = sorted(
            {
                str(account_id)
                for row in instrument_rows
                for account_id in list(row.get("account_ids") or [row.get("account_id")])
                if str(account_id or "")
            }
        )
        market_value_base = _sum_complete([row.get("market_value_base") for row in instrument_rows])
        cost_basis_base = _sum_complete([row.get("cost_basis_base") for row in instrument_rows])
        first_row = instrument_rows[0] if instrument_rows else {}
        holding_profile_row = _earliest_holding_profile_row(instrument_rows)
        cost_basis_methods = sorted(
            {
                str(row.get("cost_basis_method") or "")
                for row in instrument_rows
                if str(row.get("cost_basis_method") or "")
            }
        )
        price_charts = {
            f"price_chart_{range_key}": next(
                (
                    row.get(f"price_chart_{range_key}")
                    for row in instrument_rows
                    if isinstance(row.get(f"price_chart_{range_key}"), list)
                    and row.get(f"price_chart_{range_key}")
                ),
                [],
            )
            for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
        }
        trend_metrics = {
            "instrument_return_1w": _first_present(instrument_rows, "instrument_return_1w"),
            "instrument_return_1m": _first_present(instrument_rows, "instrument_return_1m"),
            "instrument_return_3m": _first_present(instrument_rows, "instrument_return_3m"),
            "instrument_return_6m": _first_present(instrument_rows, "instrument_return_6m"),
            "instrument_return_mtd": _first_present(instrument_rows, "instrument_return_mtd"),
            "instrument_return_ytd": _first_present(instrument_rows, "instrument_return_ytd"),
            "instrument_return_1y": _first_present(instrument_rows, "instrument_return_1y"),
            "instrument_volatility_1m": _first_present(instrument_rows, "instrument_volatility_1m"),
            "instrument_volatility_3m": _first_present(instrument_rows, "instrument_volatility_3m"),
            "instrument_volatility_6m": _first_present(instrument_rows, "instrument_volatility_6m"),
            "instrument_volatility_1y": _first_present(instrument_rows, "instrument_volatility_1y"),
            "instrument_return_series_1m": _first_present(instrument_rows, "instrument_return_series_1m"),
            "instrument_return_series_3m": _first_present(instrument_rows, "instrument_return_series_3m"),
            "instrument_return_series_6m": _first_present(instrument_rows, "instrument_return_series_6m"),
            "instrument_return_series_1y": _first_present(instrument_rows, "instrument_return_series_1y"),
            "instrument_return_series_all": _first_present(instrument_rows, "instrument_return_series_all"),
            "instrument_holding_return_series": holding_profile_row.get(
                "instrument_holding_return_series"
            ),
            "instrument_current_drawdown": _first_present(instrument_rows, "instrument_current_drawdown"),
            "instrument_max_drawdown": _first_present(instrument_rows, "instrument_max_drawdown"),
            "instrument_holding_max_drawdown": holding_profile_row.get(
                "instrument_holding_max_drawdown"
            ),
            "instrument_holding_start_date": holding_profile_row.get(
                "instrument_holding_start_date"
            ),
            "instrument_trend_as_of_date": _first_present(instrument_rows, "instrument_trend_as_of_date"),
            "instrument_trend_basis": _first_present(instrument_rows, "instrument_trend_basis"),
            "instrument_trend_coverage": _first_present(instrument_rows, "instrument_trend_coverage"),
            "instrument_trend_reason": _first_present(instrument_rows, "instrument_trend_reason"),
            "instrument_trend_split_adjusted": _first_present(
                instrument_rows,
                "instrument_trend_split_adjusted",
            ),
            "instrument_risk_frequency": _first_present(instrument_rows, "instrument_risk_frequency"),
        }
        market_value = _sum_complete([row.get("market_value") for row in instrument_rows])
        day_change_value = _sum_complete([row.get("day_change_value") for row in instrument_rows])
        day_change_value_base = _sum_complete([row.get("day_change_value_base") for row in instrument_rows])
        is_cash_row = _is_cash_holding_payload(first_row)
        is_pending_row = _is_pending_monetary_holding_payload(first_row)
        required_underlying_quantity = _sum_complete(
            [row.get("required_underlying_quantity") for row in instrument_rows]
        )
        aggregated_rows.append(
            {
                "line_id": (
                    f"{position_reference_id}:obligation"
                    if holding_kind == "option_obligation"
                    else position_reference_id
                ),
                "position_reference_id": position_reference_id,
                "instrument_id": first_row.get("instrument_id"),
                "derivative_contract_id": first_row.get(
                    "derivative_contract_id"
                ),
                "derivative_contract": deepcopy(
                    first_row.get("derivative_contract")
                ),
                "holding_kind": holding_kind
                or ("settled_cash" if is_cash_row else "position"),
                "available_for_trading": bool(
                    first_row.get(
                        "available_for_trading",
                        not is_pending_row,
                    )
                ),
                "economic_instrument_id": first_row.get(
                    "economic_instrument_id"
                ),
                "economic_instrument_ref": deepcopy(
                    first_row.get("economic_instrument_ref")
                ),
                "transaction_ids": sorted(
                    {
                        str(transaction_id)
                        for row in instrument_rows
                        for transaction_id in list(
                            row.get("transaction_ids") or []
                        )
                        if str(transaction_id or "")
                    }
                ),
                "instrument_core": (
                    holdings_market_profile.normalize_instrument_core(
                        str(first_row.get("instrument_id") or ""),
                        first_row.get("instrument_ref")
                        if isinstance(first_row.get("instrument_ref"), dict)
                        else None,
                    )
                    if first_row.get("instrument_id")
                    else None
                ),
                "quantity": _sum_complete([row.get("quantity") for row in instrument_rows]),
                "last_price": _first_present(instrument_rows, "last_price"),
                "quote_as_of_date": _first_present(instrument_rows, "quote_as_of_date"),
                "quote_metric_family": _first_present(instrument_rows, "quote_metric_family"),
                "quote_basis": _first_present(instrument_rows, "quote_basis"),
                "quote_provider": _first_present(instrument_rows, "quote_provider"),
                "quote_status": _first_present(instrument_rows, "quote_status"),
                "market_value": market_value,
                "market_value_base": market_value_base,
                "day_change_pct": _first_present(instrument_rows, "day_change_pct"),
                "day_change_value": day_change_value,
                "day_change_value_base": day_change_value_base,
                "cost_basis_method": (
                    None
                    if is_cash_row or is_pending_row
                    else cost_basis_methods[0]
                    if len(cost_basis_methods) == 1
                    else "mixed"
                    if cost_basis_methods
                    else "fifo"
                ),
                "cost_basis": _sum_complete([row.get("cost_basis") for row in instrument_rows]),
                "cost_basis_base": cost_basis_base,
                "allocation": (
                    market_value_base / total_nav_base
                    if market_value_base is not None and total_nav_base is not None and total_nav_base > 1e-9
                    else None
                ),
                **price_charts,
                **trend_metrics,
                "coverage_status": (
                    _first_present(instrument_rows, "coverage_status")
                    if is_cash_row
                    or is_pending_row
                    or holding_kind == "option_obligation"
                    or _first_present(instrument_rows, "coverage_status")
                    in {"event-cost", "event-liability"}
                    else "price-nav-fx"
                    if market_value_base is not None
                    else "unpriced"
                ),
                "account_ids": account_ids,
                "account_count": len(account_ids),
                "open_position_lot_count": sum(int(row.get("open_position_lot_count") or 0) for row in instrument_rows),
                "is_liability": any(bool(row.get("is_liability")) for row in instrument_rows),
                "performance_eligible": all(
                    bool(row.get("performance_eligible", True)) for row in instrument_rows
                ),
                "risk_eligible": all(
                    bool(row.get("risk_eligible", True)) for row in instrument_rows
                ),
                "open_contract_quantity": _sum_complete(
                    [row.get("open_contract_quantity") for row in instrument_rows]
                ),
                "required_underlying_quantity": required_underlying_quantity,
                "obligation_status": _first_present(
                    instrument_rows, "obligation_status"
                ),
                "related_underlying_id": _first_present(
                    instrument_rows, "related_underlying_id"
                ),
                "expiry_date": _first_present(instrument_rows, "expiry_date"),
                "days_to_expiry": _first_present(
                    instrument_rows, "days_to_expiry"
                ),
                "strike": _first_present(instrument_rows, "strike"),
                "option_type": _first_present(instrument_rows, "option_type"),
                "contract_multiplier": _first_present(
                    instrument_rows, "contract_multiplier"
                ),
                "strike_notional": _sum_complete(
                    [row.get("strike_notional") for row in instrument_rows]
                ),
                "strike_notional_base": _sum_complete(
                    [row.get("strike_notional_base") for row in instrument_rows]
                ),
                "premium_received_gross": _sum_complete(
                    [row.get("premium_received_gross") for row in instrument_rows]
                ),
                "premium_basis_remaining": _sum_complete(
                    [row.get("premium_basis_remaining") for row in instrument_rows]
                ),
                "liability_value": _sum_complete(
                    [row.get("liability_value") for row in instrument_rows]
                ),
                "liability_value_base": _sum_complete(
                    [row.get("liability_value_base") for row in instrument_rows]
                ),
                "carrying_value": _sum_complete(
                    [row.get("carrying_value") for row in instrument_rows]
                ),
                "carrying_value_base": _sum_complete(
                    [row.get("carrying_value_base") for row in instrument_rows]
                ),
                "fair_value": _sum_complete([row.get("fair_value") for row in instrument_rows]),
                "fair_value_coverage_status": _first_present(
                    instrument_rows, "fair_value_coverage_status"
                ),
                "valuation_basis": _first_present(instrument_rows, "valuation_basis"),
                "settlement_date": _first_present(
                    instrument_rows, "settlement_date"
                ),
                "pending_until_date": _first_present(
                    instrument_rows, "pending_until_date"
                ),
                "pending_status": _first_present(
                    instrument_rows, "pending_status"
                ),
                "settlement_amount": _sum_complete(
                    [row.get("settlement_amount") for row in instrument_rows]
                ),
                "settlement_amount_base": _sum_complete(
                    [row.get("settlement_amount_base") for row in instrument_rows]
                ),
            }
        )

    aggregated_rows.sort(
        key=lambda item: (
            -(_safe_float(item.get("market_value_base")) or 0.0),
            str(item.get("line_id") or ""),
        )
    )
    return aggregated_rows


def build_materialized_holdings_workspace(
    portfolio_id: str,
    *,
    as_of_date: date | None = None,
) -> dict[str, object] | None:
    ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return None
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return None

        if as_of_date is None:
            snapshot = default_portfolio_snapshot(session, portfolio_id)
        else:
            snapshot_statement = select(PortfolioDailySnapshotModel).where(
                PortfolioDailySnapshotModel.portfolio_id == portfolio_id
            )
            snapshot_statement = snapshot_statement.where(PortfolioDailySnapshotModel.as_of_date == as_of_date)
            snapshot = session.scalar(
                snapshot_statement.order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            )
        if snapshot is None:
            return None

        rows = list(
            session.scalars(
                select(PortfolioDailyHoldingSnapshotModel)
                .where(
                    PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id,
                    PortfolioDailyHoldingSnapshotModel.as_of_date == snapshot.as_of_date,
                )
                .order_by(
                    PortfolioDailyHoldingSnapshotModel.position_reference_id,
                    PortfolioDailyHoldingSnapshotModel.account_id,
                )
            ).all()
        )
        snapshot_payload = _restore_snapshot(dict(snapshot.snapshot_json))
        snapshot_as_of_date = snapshot.as_of_date
        portfolio = _serialize_portfolio_row(portfolio_record)

    total_nav_base = _safe_float(snapshot_payload.get("nav"))
    aggregated_rows = _aggregate_holding_rows(rows, total_nav_base=total_nav_base)
    total_market_value_base = (
        _sum_complete([row.get("market_value_base") for row in aggregated_rows]) if aggregated_rows else 0.0
    )
    day_change_totals = holdings_market_profile.summarize_holding_day_change(
        aggregated_rows,
        total_market_value_base=total_market_value_base,
    )
    noncash_snapshot_rows = [
        row
        for row in rows
        if not holdings_market_profile.is_cash_holding_instrument_id(row.instrument_id)
        and not holdings_market_profile.is_pending_monetary_holding(
            row.holding_json if isinstance(row.holding_json, dict) else {}
        )
        and row.holding_kind != "option_obligation"
    ]
    total_cost_basis_base = (
        _sum_complete([row.cost_basis_base for row in noncash_snapshot_rows]) if noncash_snapshot_rows else 0.0
    )
    formal_position_rows = [
        row
        for row in aggregated_rows
        if str(row.get("holding_kind") or "position") == "position"
    ]
    position_count = len(formal_position_rows)
    priced_position_count = sum(
        1
        for row in formal_position_rows
        if holdings_market_profile.is_market_priced_holding(row)
    )

    return {
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_name": portfolio["portfolio_name"],
        "base_currency": snapshot_payload.get("base_currency") or portfolio.get("base_currency", "USD"),
        "as_of_date": snapshot_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "coverage_note": (
            "Holdings are served from materialized daily holding snapshots generated by the portfolio "
            "calculation refresh pipeline."
        ),
        "summary_cards": [
            {"label": "Positions", "value": str(position_count), "tone": "neutral"},
            {
                "label": "Open PositionLots",
                "value": str(sum(int(row.get("open_position_lot_count") or 0) for row in aggregated_rows)),
                "tone": "neutral",
            },
            {
                "label": "Priced Lines",
                "value": f"{priced_position_count} / {position_count}",
                "tone": "neutral",
            },
            {"label": "Coverage", "value": "Materialized SOA", "tone": "neutral"},
        ],
        "rows": aggregated_rows,
        "totals": {
            "market_value": total_market_value_base,
            "cash_balance": snapshot_payload.get("cash_balance"),
            "pending_settlement": snapshot_payload.get("pending_settlement"),
            "nav": total_nav_base,
            "day_change_pct": day_change_totals["day_change_pct"],
            "day_change_value": day_change_totals["day_change_value"],
            "cost_basis": total_cost_basis_base,
            "allocation": (
                total_market_value_base / total_nav_base
                if total_market_value_base is not None and total_nav_base is not None and total_nav_base > 1e-9
                else None
            ),
        },
    }


_INSTRUMENT_HOLDING_PROJECTION_FIELDS = (
    "line_id",
    "position_reference_id",
    "instrument_id",
    "derivative_contract_id",
    "derivative_contract",
    "holding_kind",
    "available_for_trading",
    "economic_instrument_id",
    "economic_instrument_ref",
    "transaction_ids",
    "instrument_core",
    "quantity",
    "last_price",
    "quote_as_of_date",
    "quote_metric_family",
    "quote_basis",
    "quote_provider",
    "quote_status",
    "market_value",
    "market_value_base",
    "day_change_pct",
    "day_change_value",
    "day_change_value_base",
    "cost_basis_method",
    "cost_basis",
    "cost_basis_base",
    "allocation",
    "coverage_status",
    "account_count",
    "open_position_lot_count",
    "is_liability",
    "performance_eligible",
    "risk_eligible",
    "open_contract_quantity",
    "required_underlying_quantity",
    "obligation_status",
    "related_underlying_id",
    "expiry_date",
    "days_to_expiry",
    "strike",
    "option_type",
    "contract_multiplier",
    "strike_notional",
    "strike_notional_base",
    "premium_received_gross",
    "premium_basis_remaining",
    "liability_value",
    "liability_value_base",
    "carrying_value",
    "carrying_value_base",
    "fair_value",
    "fair_value_coverage_status",
    "valuation_basis",
    "settlement_date",
    "pending_until_date",
    "pending_status",
    "settlement_amount",
    "settlement_amount_base",
)


def project_instrument_holding_row(source_row: dict[str, object]) -> dict[str, object]:
    return {
        field_name: deepcopy(source_row[field_name])
        for field_name in _INSTRUMENT_HOLDING_PROJECTION_FIELDS
        if field_name in source_row
    }


def build_materialized_position_holding_projection(
    portfolio_id: str,
    position_reference_id: str,
    *,
    as_of_date: date | None = None,
) -> dict[str, object] | None:
    """Read one position reference's holding kinds without portfolio-wide analytics."""

    ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return None
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return None

        if as_of_date is None:
            snapshot = default_portfolio_snapshot(session, portfolio_id)
        else:
            snapshot = session.scalar(
                select(PortfolioDailySnapshotModel)
                .where(
                    PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
                    PortfolioDailySnapshotModel.as_of_date == as_of_date,
                )
                .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            )
        if snapshot is None:
            return None

        rows = list(
            session.scalars(
                select(PortfolioDailyHoldingSnapshotModel)
                .where(
                    PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id,
                    PortfolioDailyHoldingSnapshotModel.as_of_date == snapshot.as_of_date,
                    PortfolioDailyHoldingSnapshotModel.position_reference_id
                    == position_reference_id,
                )
                .order_by(
                    PortfolioDailyHoldingSnapshotModel.account_id,
                    PortfolioDailyHoldingSnapshotModel.holding_kind,
                )
            ).all()
        )
        snapshot_payload = _restore_snapshot(dict(snapshot.snapshot_json))
        snapshot_as_of_date = snapshot.as_of_date
        portfolio = _serialize_portfolio_row(portfolio_record)

    total_nav_base = _safe_float(snapshot_payload.get("nav"))
    aggregated_rows = _aggregate_holding_rows(rows, total_nav_base=total_nav_base)
    projected_rows = [
        project_instrument_holding_row(row)
        for row in aggregated_rows
    ]
    return {
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_name": portfolio["portfolio_name"],
        "base_currency": snapshot_payload.get("base_currency") or portfolio.get("base_currency", "USD"),
        "as_of_date": snapshot_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "rows": projected_rows,
    }


def list_materialized_contribution_slices(
    portfolio_id: str,
    *,
    axis: str,
    start_date: date | None = None,
    end_date: date | None = None,
    ensure_current: bool = True,
) -> list[dict[str, object]]:
    if ensure_current:
        ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(PortfolioDailyContributionSliceModel).where(
            PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id,
            PortfolioDailyContributionSliceModel.axis == axis,
        )
        if start_date is not None:
            statement = statement.where(PortfolioDailyContributionSliceModel.as_of_date >= start_date)
        if end_date is not None:
            statement = statement.where(PortfolioDailyContributionSliceModel.as_of_date <= end_date)
        rows = session.scalars(
            statement.order_by(
                PortfolioDailyContributionSliceModel.as_of_date,
                PortfolioDailyContributionSliceModel.group_key,
            )
        ).all()
        return [_restore_dated_payload(dict(row.slice_json)) for row in rows]


def build_materialized_contribution_report(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    group_key: str | None = None,
) -> dict[str, object] | None:
    if axis not in attribution.MATERIALIZED_CONTRIBUTION_AXES:
        return None
    ensure_portfolio_daily_snapshots(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return None
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return None
        portfolio = _serialize_portfolio_row(portfolio_record)
        portfolio_as_of_date = portfolio_record.as_of_date
        transaction_payloads = [
            _serialize_transaction_row(item)
            for item in _load_transactions(session, portfolio_id)
        ]
        first_transaction_date = min(
            (
                effective_date
                for transaction in transaction_payloads
                if (
                    effective_date := transaction_performance_effective_date(
                        transaction
                    )
                )
                is not None
            ),
            default=None,
        )
        snapshot_bounds = session.execute(
            select(
                func.min(PortfolioDailySnapshotModel.as_of_date),
                func.max(PortfolioDailySnapshotModel.as_of_date),
            ).where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
        ).one()
        first_snapshot_date = snapshot_bounds[0]
        last_snapshot_date = snapshot_bounds[1]

    requested_report_end_date = end_date or portfolio_as_of_date or last_snapshot_date
    start_is_close_boundary = bool(
        start_date is not None
        and performance._period_start_is_close_boundary(
            transaction_payloads,
            requested_start_date=start_date,
            resolved_start_date=start_date,
        )
    )

    def with_window_metadata(
        report: dict[str, object],
        *,
        effective_start_date: date | None,
        effective_end_date: date | None,
        clamp_reason: str | None = None,
        start_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        report_summary = report.get("summary")
        if not isinstance(report_summary, dict):
            return report
        starts_on_imported_anchor = bool(
            effective_start_date is not None
            and performance._starts_on_imported_valuation_anchor(
                transaction_payloads,
                resolved_start_date=effective_start_date,
            )
        )
        starts_funded_segment = bool(
            effective_start_date is not None
            and performance._snapshot_starts_funded_segment(
                start_snapshot,
                resolved_start_date=effective_start_date,
            )
        )
        report_summary.update(
            {
                "start_date": effective_start_date,
                "end_date": effective_end_date,
                "requested_start_date": start_date,
                "requested_end_date": requested_report_end_date,
                "effective_start_date": effective_start_date,
                "effective_end_date": effective_end_date,
                "as_of_clamp_reason": clamp_reason,
                "start_boundary_kind": (
                    (
                        "funded_bod"
                        if starts_funded_segment
                        else (
                            "imported_opening_eod"
                            if starts_on_imported_anchor
                            else "close_eod"
                        )
                    )
                    if effective_start_date is not None
                    else None
                ),
                "include_start_date_return": starts_funded_segment,
            }
        )
        return report

    effective_end_date = end_date
    if (
        effective_end_date is not None
        and portfolio_as_of_date is not None
        and effective_end_date > portfolio_as_of_date
    ):
        effective_end_date = portfolio_as_of_date
    if first_snapshot_date is None or last_snapshot_date is None:
        if first_transaction_date is not None:
            return None
        return with_window_metadata(
            performance.build_contribution_report_from_daily_slices(
                portfolio,
                [],
                [],
                start_date=start_date,
                end_date=effective_end_date,
                axis=axis,
                group_key=group_key,
                start_is_close_boundary=start_is_close_boundary,
            ),
            effective_start_date=None,
            effective_end_date=None,
        )
    requested_start_date = start_date or first_snapshot_date
    requested_end_date = effective_end_date or last_snapshot_date
    if (
        requested_start_date < first_snapshot_date
        and first_transaction_date is not None
        and first_snapshot_date > first_transaction_date
    ):
        return None
    if (
        requested_end_date > last_snapshot_date
        and portfolio_as_of_date is not None
        and last_snapshot_date < portfolio_as_of_date
    ):
        return None
    if requested_end_date < first_snapshot_date or requested_start_date > last_snapshot_date:
        return with_window_metadata(
            performance.build_contribution_report_from_daily_slices(
                portfolio,
                [],
                [],
                start_date=requested_start_date,
                end_date=requested_end_date,
                axis=axis,
                group_key=group_key,
                start_is_close_boundary=start_is_close_boundary,
            ),
            effective_start_date=None,
            effective_end_date=None,
        )
    resolved_start_date = max(requested_start_date, first_snapshot_date)
    resolved_end_date = min(requested_end_date, last_snapshot_date)
    snapshots = list_materialized_daily_snapshots(
        portfolio_id,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        ensure_current=False,
    )
    if not snapshots:
        return None
    reliable_window = return_chain.resolve_reliable_snapshot_window(
        snapshots,
        requested_start_date=resolved_start_date,
        requested_end_date=requested_report_end_date,
        default_end_date=last_snapshot_date,
    )
    snapshots = list(reliable_window["snapshots"])
    reliable_end_date = _parse_date(reliable_window.get("effective_end_date"))
    if not snapshots or reliable_end_date is None:
        return None
    resolved_end_date = reliable_end_date
    raw_start_snapshot = next(
        (
            snapshot
            for snapshot in snapshots
            if _parse_date(snapshot.get("as_of_date")) == resolved_start_date
        ),
        None,
    )
    start_is_close_boundary = performance._period_start_is_close_boundary(
        transaction_payloads,
        requested_start_date=start_date,
        resolved_start_date=resolved_start_date,
        start_snapshot=raw_start_snapshot,
    )
    available_dates = [
        parsed_date
        for parsed_date in (_parse_date(snapshot.get("as_of_date")) for snapshot in snapshots)
        if parsed_date is not None
    ]
    if not available_dates:
        return None
    first_available_date = min(available_dates)
    last_available_date = max(available_dates)
    if first_available_date > resolved_start_date or last_available_date < resolved_end_date:
        return None

    slices = list_materialized_contribution_slices(
        portfolio_id,
        axis=axis,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        ensure_current=False,
    )
    report = performance.build_contribution_report_from_daily_slices(
        portfolio,
        snapshots,
        slices,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis=axis,
        group_key=group_key,
        start_is_close_boundary=start_is_close_boundary,
    )
    raw_clamp_reason = reliable_window.get("as_of_clamp_reason")
    return with_window_metadata(
        report,
        effective_start_date=resolved_start_date,
        effective_end_date=resolved_end_date,
        clamp_reason=(
            str(raw_clamp_reason)
            if raw_clamp_reason is not None
            else None
        ),
        start_snapshot=raw_start_snapshot,
    )
