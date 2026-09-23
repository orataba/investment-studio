"""Prepare current page analysis after the durable accounting queue is drained."""
from __future__ import annotations

from datetime import date
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from studio_runtime import operation

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioRecordModel, PortfolioWorkspaceReadModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.taxonomy_configuration import taxonomy_configuration_version
from portfolio_app.services.holdings_workspace import (
    _portfolio_calculation_frequency_profile, read_holdings_analysis,
)
from portfolio_app.services.risk_model import normalize_portfolio_risk_policy, portfolio_risk_model_snapshot
from portfolio_app.services.snapshot_selection import default_portfolio_snapshot
from portfolio_app.services.workspace_cache import (
    get_cached_materialized_performance_report, holdings_analysis_args,
    _snapshot_fingerprint_in_session, snapshot_projection_identity,
)
from portfolio_app.services.workspace_read_models import (
    publish_workspace_projection, serialize_source_key,
)


logger = logging.getLogger(__name__)


def _current_projection_inputs(
    portfolio_id: str, *, validate_sources: bool = True,
) -> tuple[dict, date, dict[str, tuple | None]] | None:
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        portfolio_record = session.get(PortfolioRecordModel, portfolio_id)
        if (state is None or portfolio_record is None
                or state.daily_snapshot_status not in {"current", "failed"}):
            return None
        fingerprint = (_snapshot_fingerprint_in_session(session, portfolio_id) if validate_sources
                       else snapshot_projection_identity(state, portfolio_record))
        if fingerprint is None:
            return None
        latest = default_portfolio_snapshot(session, portfolio_id)
        if latest is None:
            # A gap before the first reliable valuation has no dated page to
            # prepare. Do not invent a date or rebuild live accounting here.
            return None
        as_of = latest.as_of_date
        policy = portfolio_risk_model_snapshot(normalize_portfolio_risk_policy(portfolio_record.risk_policy_json))
        args = holdings_analysis_args(as_of, policy, taxonomy_configuration_version(portfolio_id, session=session))
    portfolio = {"portfolio_id": portfolio_id, "as_of_date": as_of.isoformat()}
    keys = {
        "holdings_analytics": ("holdings_analytics", *args, *fingerprint),
        "portfolio_risk_basis": ("portfolio_risk_basis", as_of.isoformat(), *fingerprint),
    }
    return portfolio, as_of, keys


def precompute_portfolio_workspace(portfolio_id: str, *, force: bool = False) -> bool:
    # Idle scheduling checks metadata only. The accounting worker independently
    # reconciles live inputs; builders/readers/publishers still verify sources.
    inputs = _current_projection_inputs(portfolio_id, validate_sources=False)
    if inputs is None:
        return False
    portfolio, as_of, keys = inputs
    if any(key is None for key in keys.values()):
        return False
    with get_session_factory()() as session:
        completed = dict(session.execute(select(
            PortfolioWorkspaceReadModel.surface, PortfolioWorkspaceReadModel.source_key,
        ).where(PortfolioWorkspaceReadModel.portfolio_id == portfolio_id)).all())
    missing = [surface for surface, key in keys.items()
               if force or completed.get(surface) != serialize_source_key(key)]
    if not missing:
        return False
    if _current_projection_inputs(portfolio_id) != inputs:
        return False

    def current_key(surface: str):
        current = _current_projection_inputs(portfolio_id)
        return current[2].get(surface) if current is not None else None

    # Holdings constructs the risk basis while sharing the same registry reads.
    # Save each surface only if every input still identifies this generation.
    builders = {
        "holdings_analytics": lambda: read_holdings_analysis(portfolio_id, as_of),
        "portfolio_risk_basis": lambda: _portfolio_calculation_frequency_profile(portfolio, as_of_date=as_of),
    }
    for surface in missing:
        key = keys[surface]
        try:
            with operation("portfolio_workspace_precompute", portfolio_id=portfolio_id, surface=surface):
                payload = builders[surface]()
                publish_workspace_projection(
                    portfolio_id, surface, key, current_source_key=lambda: current_key(surface), payload=payload,
                )
        except (SQLAlchemyError, OSError):
            # Connection/DB failures use the worker's bounded backoff; they
            # must not permanently suppress preparation of unchanged inputs.
            raise
        except Exception as exc:
            # Registry readers and the holdings service wrap DB failures with
            # explicit causes. Preserve their retry policy through those
            # domain/HTTP wrappers instead of freezing a recoverable outage.
            cause = exc.__cause__
            while cause is not None:
                if isinstance(cause, (SQLAlchemyError, OSError)):
                    raise
                cause = cause.__cause__
            # Record this generation's failure rather than hot-looping or
            # marking accounting facts failed. A changed input retries it.
            publish_workspace_projection(
                portfolio_id, surface, key, current_source_key=lambda: current_key(surface),
                payload=None, error_type=type(exc).__name__,
            )
            logger.exception("Portfolio workspace precomputation failed for %s/%s", portfolio_id, surface)
    # Common Performance is cheap interval aggregation over published days.
    # It remains an in-memory convenience, not another durable financial model.
    get_cached_materialized_performance_report(portfolio_id)
    return True


def workspace_precomputation_errors(portfolio_id: str) -> list[str]:
    inputs = _current_projection_inputs(portfolio_id)
    if inputs is None:
        return []
    with get_session_factory()() as session:
        rows = {surface: (key, error) for surface, key, error in session.execute(select(
            PortfolioWorkspaceReadModel.surface, PortfolioWorkspaceReadModel.source_key,
            PortfolioWorkspaceReadModel.error_type,
        ).where(PortfolioWorkspaceReadModel.portfolio_id == portfolio_id))}
    return [f"{portfolio_id}/{surface}:{rows.get(surface, ('', 'missing'))[1] or 'unpublished'}"
            for surface, key in inputs[2].items()
            if rows.get(surface) != (serialize_source_key(key), None)]


def precompute_next_portfolio_workspace(
    *, after_portfolio_id: str | None, batch_size: int,
) -> tuple[bool, str | None]:
    with get_session_factory()() as session:
        statement = select(PortfolioCalculationStateModel.portfolio_id).where(
            PortfolioCalculationStateModel.daily_snapshot_status.in_(("current", "failed")),
        )
        if after_portfolio_id is not None:
            statement = statement.where(PortfolioCalculationStateModel.portfolio_id > after_portfolio_id)
        ids = list(session.scalars(statement.order_by(PortfolioCalculationStateModel.portfolio_id).limit(batch_size)))
    for portfolio_id in ids:
        if precompute_portfolio_workspace(portfolio_id):
            return True, portfolio_id
    return False, ids[-1] if len(ids) == batch_size else None
