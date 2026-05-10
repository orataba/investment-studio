from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4

from sqlalchemy import delete, func, select, update

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
from portfolio_app.services.instrument_charts import HOLDINGS_PRICE_CHART_RANGE_KEYS, build_instrument_trend_metrics
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.portfolio_store import (
    _resolve_live_portfolio_as_of_date,
    _serialize_account_row,
    _serialize_portfolio_row,
    _serialize_transaction_row,
)

_LOCAL_REFRESH_LOCKS: dict[str, Lock] = {}
_LOCAL_REFRESH_LOCKS_GUARD = Lock()
_RUNNING_REFRESH_WAIT_SECONDS = 30.0
_RUNNING_REFRESH_POLL_SECONDS = 0.1
DAILY_SNAPSHOT_CALCULATION_VERSION = "portfolio-daily-v20260509-holdings-chart-columns"


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        "calculation_version": DAILY_SNAPSHOT_CALCULATION_VERSION,
    }


def mark_portfolio_daily_snapshots_stale(portfolio_id: str, dirty_from: date | None = None) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get(PortfolioRecordModel, portfolio_id) is None:
            return
        state = _state_for_portfolio(session, portfolio_id)
        state.refresh_request_id = _new_refresh_request_id()
        if state.daily_snapshot_status != "running":
            state.daily_snapshot_status = "stale"
        if dirty_from is not None:
            state.dirty_from = min(state.dirty_from, dirty_from) if state.dirty_from is not None else dirty_from
        state.error_message = None
        session.commit()


def _claim_daily_snapshot_refresh(portfolio_id: str) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_record is None:
            return {"status": "missing"}
        state = _state_for_portfolio(session, portfolio_id)

        snapshot_count = _snapshot_count(session, portfolio_id)
        if state.daily_snapshot_status == "current" and snapshot_count > 0:
            if _latest_snapshot_calculation_version(session, portfolio_id) == DAILY_SNAPSHOT_CALCULATION_VERSION:
                return {
                    "status": "current",
                    "result": _current_refresh_result(session, portfolio_id),
                }
            state.daily_snapshot_status = "stale"
            state.refresh_request_id = _new_refresh_request_id()
            state.error_message = None
            session.flush()
        if state.daily_snapshot_status == "running":
            return {"status": "running"}

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

    try:
        with session_factory() as session:
            portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
            if portfolio_record is None:
                return None, False
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

            snapshots = performance.build_daily_portfolio_snapshots(
                portfolio,
                accounts,
                transactions,
                start_date=None,
                end_date=resolved_end_date,
                include_materialized_rows=True,
            )
            calculated_at = _current_utc_timestamp()

            session.execute(
                delete(PortfolioDailyContributionSliceModel).where(
                    PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id
                )
            )
            session.execute(
                delete(PortfolioDailyHoldingSnapshotModel).where(
                    PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id
                )
            )
            session.execute(
                delete(PortfolioDailySnapshotModel).where(
                    PortfolioDailySnapshotModel.portfolio_id == portfolio_id
                )
            )
            for snapshot in snapshots:
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
                    if axis not in {"instrument", "account"} or not group_key:
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

            latest_snapshot = snapshots[-1] if snapshots else None
            portfolio_record.as_of_date = resolved_end_date
            if latest_snapshot is not None:
                portfolio_record.nav = _safe_float(latest_snapshot.get("nav")) or 0.0
                portfolio_record.day_change_value = _safe_float(latest_snapshot.get("absolute_change")) or 0.0
                portfolio_record.day_change_pct = _safe_float(latest_snapshot.get("daily_twr")) or 0.0
                portfolio_record.securities_count = int(latest_snapshot.get("total_position_count") or 0)

            state = _state_for_portfolio(session, portfolio_id)
            refreshed_from = _parse_date(snapshots[0].get("as_of_date")) if snapshots else None
            refreshed_to = _parse_date(snapshots[-1].get("as_of_date")) if snapshots else None
            update_result = session.execute(
                update(PortfolioCalculationStateModel)
                .where(PortfolioCalculationStateModel.portfolio_id == portfolio_id)
                .where(PortfolioCalculationStateModel.refresh_request_id == request_id)
                .values(
                    daily_snapshot_status="current",
                    dirty_from=None,
                    refreshed_from=refreshed_from,
                    refreshed_to=refreshed_to,
                    refreshed_at=calculated_at,
                    refresh_request_id=None,
                    refresh_completed_at=calculated_at,
                    error_message=None,
                )
            )
            request_superseded = int(update_result.rowcount or 0) == 0
            if request_superseded and state.daily_snapshot_status == "running":
                state.daily_snapshot_status = "stale"
                state.refresh_completed_at = calculated_at
            session.commit()

            return {
                "portfolio_id": portfolio_id,
                "snapshot_count": len(snapshots),
                "refreshed_from": refreshed_from,
                "refreshed_to": refreshed_to,
                "refreshed_at": calculated_at,
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
                    if state.daily_snapshot_status == "running":
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
    return _latest_snapshot_calculation_version(session, portfolio_id) != DAILY_SNAPSHOT_CALCULATION_VERSION


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
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
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


def _instrument_trend_metrics(instrument_id: str, as_of_date: date | None) -> dict[str, object]:
    if not instrument_id or as_of_date is None:
        return {}
    try:
        return build_instrument_trend_metrics(instrument_id, as_of_date=as_of_date)
    except InstrumentRegistryError:
        return {}


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
            "instrument_current_drawdown": _first_present(instrument_rows, "instrument_current_drawdown"),
            "instrument_max_drawdown": _first_present(instrument_rows, "instrument_max_drawdown"),
            "instrument_holding_max_drawdown": _first_present(instrument_rows, "instrument_holding_max_drawdown"),
            "instrument_holding_start_date": _first_present(instrument_rows, "instrument_holding_start_date"),
            "instrument_trend_as_of_date": _first_present(instrument_rows, "instrument_trend_as_of_date"),
            "instrument_trend_basis": _first_present(instrument_rows, "instrument_trend_basis"),
        }
        if all(value is None for value in trend_metrics.values()):
            snapshot_as_of_date = _parse_date(_first_present(instrument_rows, "_snapshot_as_of_date"))
            trend_metrics = _instrument_trend_metrics(instrument_id, snapshot_as_of_date)
        aggregated_rows.append(
            {
                "line_id": instrument_id,
                "instrument_core": deepcopy(first_row.get("instrument_ref") or {}),
                "quantity": _sum_complete([row.get("quantity") for row in instrument_rows]),
                "last_price": _first_present(instrument_rows, "last_price"),
                "quote_as_of_date": _first_present(instrument_rows, "quote_as_of_date"),
                "quote_metric_family": _first_present(instrument_rows, "quote_metric_family"),
                "quote_basis": _first_present(instrument_rows, "quote_basis"),
                "quote_provider": _first_present(instrument_rows, "quote_provider"),
                "quote_status": _first_present(instrument_rows, "quote_status"),
                "market_value": _sum_complete([row.get("market_value") for row in instrument_rows]),
                "market_value_base": market_value_base,
                "day_change_pct": None,
                "day_change_value": None,
                "cost_basis_method": (
                    cost_basis_methods[0]
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
                "coverage_status": "price-nav-fx" if market_value_base is not None else "unpriced",
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

        snapshot_statement = select(PortfolioDailySnapshotModel).where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id
        )
        if as_of_date is not None:
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
    total_market_value_base = _safe_float(snapshot_payload.get("position_market_value"))
    total_cost_basis_base = _sum_complete([row.cost_basis_base for row in rows]) if rows else 0.0
    aggregated_rows = _aggregate_holding_rows(rows, total_nav_base=total_nav_base)
    position_count = len(aggregated_rows)
    priced_position_count = sum(1 for row in aggregated_rows if row.get("market_value_base") is not None)

    return {
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_name": portfolio["portfolio_name"],
        "base_currency": snapshot_payload.get("base_currency") or portfolio.get("base_currency", "USD"),
        "as_of_date": snapshot_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "coverage_note": (
            "Statement of Assets is served from materialized daily holdings snapshots generated by the portfolio "
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
            "day_change_pct": snapshot_payload.get("daily_twr"),
            "day_change_value": snapshot_payload.get("absolute_change"),
            "cost_basis": total_cost_basis_base,
            "allocation": (
                total_market_value_base / total_nav_base
                if total_market_value_base is not None and total_nav_base is not None and total_nav_base > 1e-9
                else None
            ),
        },
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
    if axis not in {"instrument", "account"}:
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

    snapshots = list_materialized_daily_snapshots(
        portfolio_id,
        start_date=start_date,
        end_date=end_date,
        ensure_current=False,
    )
    if not snapshots:
        return performance.build_contribution_report_from_daily_slices(
            portfolio,
            [],
            [],
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            group_key=group_key,
        )
    available_dates = [
        parsed_date
        for parsed_date in (_parse_date(snapshot.get("as_of_date")) for snapshot in snapshots)
        if parsed_date is not None
    ]
    if not available_dates:
        return None
    resolved_start_date = start_date or min(available_dates)
    resolved_end_date = end_date or max(available_dates)
    if resolved_start_date < min(available_dates) or resolved_end_date > max(available_dates):
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
