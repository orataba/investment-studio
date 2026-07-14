from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4

from portfolio_ops_instrument_core.db_models import Instrument
from sqlalchemy import delete, func, or_, select, update

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import performance
from portfolio_app.services.instrument_charts import HOLDINGS_PRICE_CHART_RANGE_KEYS
from portfolio_app.services.portfolio_store import (
    _resolve_live_portfolio_as_of_date,
    _serialize_account_row,
    _serialize_portfolio_row,
    _serialize_transaction_row,
)
from portfolio_app.services.snapshot_selection import default_portfolio_snapshot

_LOCAL_REFRESH_LOCKS: dict[str, Lock] = {}
_LOCAL_REFRESH_LOCKS_GUARD = Lock()
_RUNNING_REFRESH_WAIT_SECONDS = 30.0
_RUNNING_REFRESH_POLL_SECONDS = 0.1
_RUNNING_REFRESH_LEASE_SECONDS = 900.0
DAILY_SNAPSHOT_CALCULATION_VERSION = "portfolio-daily-v20260712-flow-aligned-performance"


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
                TransactionRecordModel.transaction_id,
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
    return {
        "seed_date": prior_row.as_of_date,
        "persist_from": dirty_from,
        "cumulative_twr": _safe_float(prior_payload.get("cumulative_twr")),
        "peak_growth": max([1.0, *prefix_growth_values]),
        "cash_currency_gains": _safe_float(prior_payload.get("cash_currency_gains")),
        "instrument_currency_gains": _safe_float(prior_payload.get("instrument_currency_gains")),
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
    prefix_cash_fx = _safe_float(seed.get("cash_currency_gains"))
    prefix_instrument_fx = _safe_float(seed.get("instrument_currency_gains"))

    for snapshot in snapshots:
        snapshot_date = _parse_date(snapshot.get("as_of_date"))
        if snapshot_date is None or snapshot_date <= seed_date:
            continue
        daily_twr = _safe_float(snapshot.get("daily_twr"))
        if daily_twr is not None:
            has_return_history = True
            growth_index *= 1.0 + daily_twr
            peak_growth_index = max(peak_growth_index, growth_index)
        snapshot["cumulative_twr"] = growth_index - 1.0 if has_return_history else None
        snapshot["drawdown"] = (
            growth_index / peak_growth_index - 1.0
            if has_return_history and peak_growth_index > 1e-12
            else None
        )

        local_cash_fx = _safe_float(snapshot.get("cash_currency_gains"))
        local_instrument_fx = _safe_float(snapshot.get("instrument_currency_gains"))
        snapshot["cash_currency_gains"] = (
            prefix_cash_fx + local_cash_fx
            if prefix_cash_fx is not None and local_cash_fx is not None
            else None
        )
        snapshot["instrument_currency_gains"] = (
            prefix_instrument_fx + local_instrument_fx
            if prefix_instrument_fx is not None and local_instrument_fx is not None
            else None
        )
        total_pnl = _safe_float(snapshot.get("total_pnl"))
        if total_pnl is not None:
            if prefix_cash_fx is None or prefix_instrument_fx is None:
                snapshot["total_pnl"] = None
            else:
                snapshot["total_pnl"] = total_pnl + prefix_cash_fx + prefix_instrument_fx


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
    }


def _mark_portfolio_daily_snapshots_stale_in_session(
    session,
    portfolio_id: str,
    *,
    dirty_from: date | None,
) -> None:
    if session.get(PortfolioRecordModel, portfolio_id) is None:
        return
    state = _state_for_portfolio(session, portfolio_id)
    state.refresh_request_id = _new_refresh_request_id()
    if state.daily_snapshot_status != "running":
        state.daily_snapshot_status = "stale"
    if dirty_from is not None:
        state.dirty_from = min(state.dirty_from, dirty_from) if state.dirty_from is not None else dirty_from
    state.error_message = None


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


def _wait_for_running_daily_snapshot_refresh(portfolio_id: str) -> bool:
    deadline = monotonic() + _RUNNING_REFRESH_WAIT_SECONDS
    session_factory = get_session_factory()
    while monotonic() < deadline:
        sleep(_RUNNING_REFRESH_POLL_SECONDS)
        with session_factory() as session:
            state = session.get(PortfolioCalculationStateModel, portfolio_id)
            if state is None or state.daily_snapshot_status != "running":
                return True
    return False


def _refresh_portfolio_daily_snapshots_once(
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
                session.add(
                    PortfolioDailySnapshotModel(
                        portfolio_id=portfolio_id,
                        as_of_date=snapshot_date,
                        coverage_state=str(snapshot.get("coverage_state") or "unavailable"),
                        nav=_safe_float(snapshot.get("nav")),
                        beginning_nav=_safe_float(snapshot.get("beginning_nav")),
                        ending_nav=_safe_float(snapshot.get("ending_nav")),
                        daily_twr=_safe_float(snapshot.get("daily_twr")),
                        cumulative_twr=_safe_float(snapshot.get("cumulative_twr")),
                        drawdown=_safe_float(snapshot.get("drawdown")),
                        snapshot_json=_json_safe(public_snapshot),  # type: ignore[arg-type]
                        calculated_at=calculated_at,
                    )
                )
                for holding in holding_rows:
                    account_id = str(holding.get("account_id") or "")
                    instrument_id = str(holding.get("instrument_id") or "")
                    if not account_id or not instrument_id:
                        continue
                    session.add(
                        PortfolioDailyHoldingSnapshotModel(
                            portfolio_id=portfolio_id,
                            as_of_date=snapshot_date,
                            account_id=account_id,
                            instrument_id=instrument_id,
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
                    if axis not in performance.MATERIALIZED_CONTRIBUTION_AXES or not group_key:
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
                session.refresh(state)
                if (
                    state.daily_snapshot_status == "running"
                    and state.refresh_started_at == claimed_started_at
                ):
                    state.daily_snapshot_status = "stale"
                    state.refresh_completed_at = calculated_at
            session.commit()

            return {
                "portfolio_id": portfolio_id,
                "snapshot_count": snapshot_count,
                "refreshed_from": refreshed_from,
                "refreshed_to": refreshed_to,
                "refreshed_at": calculated_at,
                "source_market_data_updated_at": source_market_data_updated_at,
                "recalculated_from": persist_from or refreshed_from,
            }, request_superseded
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


def refresh_portfolio_daily_snapshots(portfolio_id: str, end_date: date | None = None) -> dict[str, object] | None:
    lock = _refresh_lock_for_portfolio(portfolio_id)
    with lock:
        while True:
            claim = _claim_daily_snapshot_refresh(portfolio_id)
            claim_status = str(claim.get("status") or "")
            if claim_status == "missing":
                return None
            if claim_status == "current":
                result = claim.get("result")
                return result if isinstance(result, dict) else None
            if claim_status == "running":
                if not _wait_for_running_daily_snapshot_refresh(portfolio_id):
                    return None
                continue

            request_id = str(claim.get("request_id") or "")
            if not request_id:
                continue
            result, request_superseded = _refresh_portfolio_daily_snapshots_once(
                portfolio_id,
                request_id=request_id,
                end_date=end_date,
            )
            if request_superseded:
                continue
            return result


def refresh_all_portfolio_daily_snapshots() -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_ids = list(session.scalars(select(PortfolioRecordModel.portfolio_id)).all())
    return refresh_selected_portfolio_daily_snapshots(portfolio_ids)


def refresh_selected_portfolio_daily_snapshots(
    portfolio_ids: list[str],
    *,
    dirty_from: date | None = None,
) -> list[dict[str, object]]:
    normalized_portfolio_ids = list(
        dict.fromkeys(str(portfolio_id).strip() for portfolio_id in portfolio_ids if str(portfolio_id).strip())
    )
    refreshed: list[dict[str, object]] = []
    for portfolio_id in normalized_portfolio_ids:
        mark_portfolio_daily_snapshots_stale(portfolio_id, dirty_from=dirty_from)
        result = refresh_portfolio_daily_snapshots(portfolio_id)
        if result is not None:
            refreshed.append(result)
    return refreshed


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


def refresh_portfolio_daily_snapshots_for_instrument_change(
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
    return refresh_selected_portfolio_daily_snapshots(portfolio_ids, dirty_from=dirty_from)


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
        refresh_portfolio_daily_snapshots(portfolio_id)


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
        rows = session.scalars(statement.order_by(PortfolioDailySnapshotModel.as_of_date)).all()
        return [_restore_snapshot(dict(row.snapshot_json)) for row in rows]


def get_materialized_daily_snapshot(portfolio_id: str, as_of_date: date) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        if _state_requires_refresh(session, portfolio_id):
            return None
        row = session.get(PortfolioDailySnapshotModel, {"portfolio_id": portfolio_id, "as_of_date": as_of_date})
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

    calculation_start_date = start_date - timedelta(days=1) if start_date is not None else None
    snapshots = list_materialized_daily_snapshots(
        portfolio_id,
        start_date=calculation_start_date,
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


def _is_cash_holding_payload(row: dict[str, object]) -> bool:
    instrument_core = row.get("instrument_ref") if isinstance(row.get("instrument_ref"), dict) else None
    instrument_type = str((instrument_core or {}).get("instrument_type") or "").strip().lower()
    return instrument_type == "cash" or performance.is_cash_holding_instrument_id(row.get("instrument_id") or row.get("line_id"))


def _aggregate_holding_rows(
    rows: list[PortfolioDailyHoldingSnapshotModel],
    *,
    total_nav_base: float | None,
) -> list[dict[str, object]]:
    rows_by_instrument: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        payload = dict(row.holding_json) if isinstance(row.holding_json, dict) else {}
        payload["_snapshot_as_of_date"] = row.as_of_date
        rows_by_instrument.setdefault(row.instrument_id, []).append(payload)

    aggregated_rows: list[dict[str, object]] = []
    for instrument_id, instrument_rows in rows_by_instrument.items():
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
            "instrument_holding_return_series": _first_present(instrument_rows, "instrument_holding_return_series"),
            "instrument_current_drawdown": _first_present(instrument_rows, "instrument_current_drawdown"),
            "instrument_max_drawdown": _first_present(instrument_rows, "instrument_max_drawdown"),
            "instrument_holding_max_drawdown": _first_present(instrument_rows, "instrument_holding_max_drawdown"),
            "instrument_holding_start_date": _first_present(instrument_rows, "instrument_holding_start_date"),
            "instrument_trend_as_of_date": _first_present(instrument_rows, "instrument_trend_as_of_date"),
            "instrument_trend_basis": _first_present(instrument_rows, "instrument_trend_basis"),
            "instrument_risk_frequency": _first_present(instrument_rows, "instrument_risk_frequency"),
        }
        market_value = _sum_complete([row.get("market_value") for row in instrument_rows])
        day_change_value = _sum_complete([row.get("day_change_value") for row in instrument_rows])
        day_change_value_base = _sum_complete([row.get("day_change_value_base") for row in instrument_rows])
        is_cash_row = _is_cash_holding_payload(first_row)
        aggregated_rows.append(
            {
                "line_id": instrument_id,
                "instrument_core": performance.normalize_instrument_core(
                    instrument_id,
                    first_row.get("instrument_ref") if isinstance(first_row.get("instrument_ref"), dict) else None,
                    fallback_currency=str(first_row.get("currency") or "USD"),
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
                    if is_cash_row
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
                    else "price-nav-fx"
                    if market_value_base is not None
                    else "unpriced"
                ),
                "account_count": len(account_ids),
                "open_position_lot_count": sum(int(row.get("open_position_lot_count") or 0) for row in instrument_rows),
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
            snapshot = session.scalar(snapshot_statement.order_by(PortfolioDailySnapshotModel.as_of_date.desc()))
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
                    PortfolioDailyHoldingSnapshotModel.instrument_id,
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
    day_change_totals = performance.summarize_holding_day_change(
        aggregated_rows,
        total_market_value_base=total_market_value_base,
    )
    noncash_snapshot_rows = [row for row in rows if not performance.is_cash_holding_instrument_id(row.instrument_id)]
    total_cost_basis_base = (
        _sum_complete([row.cost_basis_base for row in noncash_snapshot_rows]) if noncash_snapshot_rows else 0.0
    )
    position_count = len(aggregated_rows)
    priced_position_count = sum(1 for row in aggregated_rows if row.get("market_value_base") is not None)

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
)


def project_instrument_holding_row(source_row: dict[str, object]) -> dict[str, object]:
    return {
        field_name: deepcopy(source_row[field_name])
        for field_name in _INSTRUMENT_HOLDING_PROJECTION_FIELDS
        if field_name in source_row
    }


def build_materialized_instrument_holding_projection(
    portfolio_id: str,
    instrument_id: str,
    *,
    as_of_date: date | None = None,
) -> dict[str, object] | None:
    """Read one holding directly from daily snapshots without portfolio-wide analytics."""

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
                    PortfolioDailyHoldingSnapshotModel.instrument_id == instrument_id,
                )
                .order_by(PortfolioDailyHoldingSnapshotModel.account_id)
            ).all()
        )
        snapshot_payload = _restore_snapshot(dict(snapshot.snapshot_json))
        snapshot_as_of_date = snapshot.as_of_date
        portfolio = _serialize_portfolio_row(portfolio_record)

    total_nav_base = _safe_float(snapshot_payload.get("nav"))
    aggregated_rows = _aggregate_holding_rows(rows, total_nav_base=total_nav_base)
    source_row = aggregated_rows[0] if aggregated_rows else None
    projected_row = project_instrument_holding_row(source_row) if source_row is not None else None
    return {
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_name": portfolio["portfolio_name"],
        "base_currency": snapshot_payload.get("base_currency") or portfolio.get("base_currency", "USD"),
        "as_of_date": snapshot_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "row": projected_row,
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
    if axis not in performance.MATERIALIZED_CONTRIBUTION_AXES:
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
        first_transaction_date = session.scalar(
            select(func.min(TransactionRecordModel.trade_date)).where(
                TransactionRecordModel.portfolio_id == portfolio_id
            )
        )
        snapshot_bounds = session.execute(
            select(
                func.min(PortfolioDailySnapshotModel.as_of_date),
                func.max(PortfolioDailySnapshotModel.as_of_date),
            ).where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
        ).one()
        first_snapshot_date = snapshot_bounds[0]
        last_snapshot_date = snapshot_bounds[1]

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
        return performance.build_contribution_report_from_daily_slices(
            portfolio,
            [],
            [],
            start_date=start_date,
            end_date=effective_end_date,
            axis=axis,
            group_key=group_key,
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
        return performance.build_contribution_report_from_daily_slices(
            portfolio,
            [],
            [],
            start_date=requested_start_date,
            end_date=requested_end_date,
            axis=axis,
            group_key=group_key,
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
    return performance.build_contribution_report_from_daily_slices(
        portfolio,
        snapshots,
        slices,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        axis=axis,
        group_key=group_key,
    )
