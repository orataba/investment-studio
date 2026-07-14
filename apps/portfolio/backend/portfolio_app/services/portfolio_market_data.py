from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core import (
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesPoint,
    CanonicalQuoteSeriesResolution,
    resolve_quote_series_observation_at,
)
from portfolio_ops_instrument_core.canonical_fx import CanonicalFxWindowBook
from portfolio_ops_instrument_core.db_models import Instrument
from portfolio_ops_instrument_core.models import TOTAL_RETURN_QUOTE_BASES

from portfolio_app.db.session import get_session_factory
from portfolio_app.services.canonical_fx import (
    portfolio_fx_freshness_policy,
    resolve_portfolio_fx_window_book_in_session,
)
from portfolio_app.services.canonical_quotes import (
    CanonicalQuoteWindowBook,
    resolve_role_quote_window_book_in_session,
)
from portfolio_app.services.fact_currency import require_portfolio_fact_currency


PORTFOLIO_MARKET_DATA_POLICY_VERSION = "allocation_research_market_data.v1"


@dataclass(frozen=True)
class PortfolioMarketDataContext:
    policy_version: str
    base_currency: str
    start_date: date
    end_date: date
    instrument_ids: frozenset[str]
    instrument_name_by_id: dict[str, str]
    instrument_currency_by_id: dict[str, str]
    total_return_book: CanonicalQuoteWindowBook
    fx_book: CanonicalFxWindowBook
    adopted_quote_dependencies: dict[str, dict[str, object]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    adopted_fx_dependencies: dict[str, dict[str, object]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


class PortfolioMarketDataError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_codes: list[str],
        dependency: dict[str, object],
    ) -> None:
        normalized_reasons = list(dict.fromkeys(reason_codes or ["unavailable_dependency"]))
        super().__init__(f"{message} [reason_codes={','.join(normalized_reasons)}]")
        self.reason_codes = tuple(normalized_reasons)
        self.dependency = dependency


class _PortfolioMarketDataState(Protocol):
    base_currency: str
    market_data: PortfolioMarketDataContext | None


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _normalized_currency(value: object, *, context: str) -> str:
    return require_portfolio_fact_currency(value, context=context)


def _lock_portfolio_market_data_in_session(
    session: Session,
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> PortfolioMarketDataContext:
    normalized_ids = sorted(
        {str(instrument_id or "").strip() for instrument_id in instrument_ids}
        - {""}
    )
    normalized_base_currency = _normalized_currency(
        base_currency,
        context="Portfolio market-data base",
    )
    metadata_rows = session.execute(
        select(
            Instrument.instrument_id,
            Instrument.instrument_name,
            Instrument.currency,
        ).where(Instrument.instrument_id.in_(normalized_ids))
    ).all() if normalized_ids else []
    instrument_name_by_id = {
        str(instrument_id): str(instrument_name)
        for instrument_id, instrument_name, _currency in metadata_rows
    }
    instrument_currency_by_id = {
        str(instrument_id): _normalized_currency(
            currency,
            context=f"Portfolio market-data instrument '{instrument_id}'",
        )
        for instrument_id, _instrument_name, currency in metadata_rows
    }
    total_return_book = resolve_role_quote_window_book_in_session(
        session,
        instrument_ids=normalized_ids,
        role="total_return",
        start_date=start_date,
        end_date=end_date,
    )
    currency_pairs = {
        (currency, normalized_base_currency)
        for currency in instrument_currency_by_id.values()
        if currency
    }
    currency_pairs.add((normalized_base_currency, normalized_base_currency))
    fx_book = resolve_portfolio_fx_window_book_in_session(
        session,
        currency_pairs=sorted(currency_pairs),
        start_date=start_date,
        end_date=end_date,
        freshness_policy=portfolio_fx_freshness_policy(),
    )
    return PortfolioMarketDataContext(
        policy_version=PORTFOLIO_MARKET_DATA_POLICY_VERSION,
        base_currency=normalized_base_currency,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=frozenset(normalized_ids),
        instrument_name_by_id=instrument_name_by_id,
        instrument_currency_by_id=instrument_currency_by_id,
        total_return_book=total_return_book,
        fx_book=fx_book,
    )


def lock_portfolio_market_data_in_session(
    session: Session,
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> PortfolioMarketDataContext:
    """Public one-session canonical market-data boundary for risk consumers."""

    return _lock_portfolio_market_data_in_session(
        session,
        instrument_ids=instrument_ids,
        base_currency=base_currency,
        start_date=start_date,
        end_date=end_date,
    )


def _lock_portfolio_market_data(
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> PortfolioMarketDataContext:
    session_factory = get_session_factory()
    with session_factory() as session:
        return _lock_portfolio_market_data_in_session(
            session,
            instrument_ids=instrument_ids,
            base_currency=base_currency,
            start_date=start_date,
            end_date=end_date,
        )


def _require_portfolio_market_data(
    state: _PortfolioMarketDataState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
) -> PortfolioMarketDataContext:
    context = state.market_data
    if context is None:
        raise PortfolioMarketDataError(
            "Portfolio calculations require a pre-locked canonical market-data context.",
            reason_codes=["missing_portfolio_market_data_context"],
            dependency={
                "policy_version": PORTFOLIO_MARKET_DATA_POLICY_VERSION,
                "instrument_id": instrument_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
    if start_date < context.start_date or end_date > context.end_date:
        raise PortfolioMarketDataError(
            f"{instrument_id} requested dates fall outside the locked market-data window.",
            reason_codes=["portfolio_market_data_window_not_locked"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "locked_start_date": context.start_date.isoformat(),
                "locked_end_date": context.end_date.isoformat(),
                "requested_start_date": start_date.isoformat(),
                "requested_end_date": end_date.isoformat(),
            },
        )
    if instrument_id not in context.instrument_ids:
        raise PortfolioMarketDataError(
            f"{instrument_id} was not included in the locked market-data input set.",
            reason_codes=["portfolio_market_data_instrument_not_locked"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "locked_instrument_ids": sorted(context.instrument_ids),
            },
        )
    return context


def _quote_dependency_payload(
    *,
    context: PortfolioMarketDataContext,
    instrument_id: str,
    window: CanonicalQuoteSeriesResolution | None,
    endpoint: CanonicalQuoteResolution | None,
    non_complete_observations: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "policy_version": context.policy_version,
        "instrument_id": instrument_id,
        "role": "total_return",
        "window_calculation_dependency": (
            window.calculation_dependency.model_dump(mode="json")
            if window is not None
            else None
        ),
        "endpoint_calculation_dependency": (
            endpoint.calculation_dependency.model_dump(mode="json")
            if endpoint is not None
            else None
        ),
        "non_complete_observations": non_complete_observations,
    }


def _resolved_total_return_points(
    state: _PortfolioMarketDataState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
) -> tuple[
    PortfolioMarketDataContext,
    CanonicalQuoteSeriesResolution,
    CanonicalQuoteResolution,
    list[CanonicalQuoteSeriesPoint],
]:
    context = _require_portfolio_market_data(
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
    )
    window = context.total_return_book.windows.get(instrument_id)
    if window is None:
        reason_code = (
            "instrument_not_found"
            if instrument_id in context.total_return_book.missing_instrument_ids
            else "missing_quote_series"
        )
        dependency = _quote_dependency_payload(
            context=context,
            instrument_id=instrument_id,
            window=None,
            endpoint=None,
            non_complete_observations=[],
        )
        raise PortfolioMarketDataError(
            f"{instrument_id} has no locked canonical total-return window.",
            reason_codes=[reason_code],
            dependency=dependency,
        )
    identity_reasons: list[str] = []
    if window.role != "total_return":
        identity_reasons.append("invalid_quote_role")
    if window.quote_series_id is None:
        identity_reasons.extend(window.reason_codes or ["missing_quote_series"])
    elif window.quote_basis not in TOTAL_RETURN_QUOTE_BASES:
        identity_reasons.append("invalid_total_return_quote_basis")
    endpoint: CanonicalQuoteResolution | None = None
    if not identity_reasons:
        try:
            endpoint = resolve_quote_series_observation_at(
                window,
                requested_as_of_date=end_date,
            )
        except ValueError as error:
            identity_reasons.append(
                str(getattr(error, "reason_code", "unavailable_total_return_endpoint"))
            )
    non_complete_observations = [
        {
            "observation_date": observation.observation_date.isoformat(),
            "status": observation.status,
            "observation_id": observation.observation_id,
            "revision_id": observation.revision_id,
            "revision_number": observation.revision_number,
            "payload_hash": observation.payload_hash,
        }
        for observation in window.observations
        if start_date <= observation.observation_date <= end_date
        and observation.status != "complete"
    ]
    reason_by_status = {
        "partial": "partial_series",
        "rejected": "rejected_observation",
        "withdrawn": "withdrawn_observation",
    }
    failure_reasons = list(identity_reasons)
    if endpoint is not None and endpoint.resolution_status != "resolved":
        failure_reasons.extend(endpoint.reason_codes or ["unavailable_total_return_endpoint"])
    failure_reasons.extend(
        reason_by_status.get(str(item["status"]), "unavailable_observation")
        for item in non_complete_observations
    )
    dependency = _quote_dependency_payload(
        context=context,
        instrument_id=instrument_id,
        window=window,
        endpoint=endpoint,
        non_complete_observations=non_complete_observations,
    )
    dependency_key = (
        endpoint.calculation_dependency.fingerprint
        if endpoint is not None
        else window.calculation_dependency.fingerprint
    )
    context.adopted_quote_dependencies[dependency_key] = dependency
    if failure_reasons or endpoint is None:
        raise PortfolioMarketDataError(
            f"{instrument_id} canonical total-return dependency is unavailable.",
            reason_codes=failure_reasons or ["unavailable_total_return_endpoint"],
            dependency=dependency,
        )
    points = [
        point
        for point in window.points
        if start_date <= point.observation_date <= end_date
    ]
    if not points:
        raise PortfolioMarketDataError(
            f"{instrument_id} has no canonical total-return observations in the requested window.",
            reason_codes=["insufficient_history"],
            dependency=dependency,
        )
    return context, window, endpoint, points


def portfolio_market_data_manifest(
    context: PortfolioMarketDataContext,
) -> dict[str, object]:
    """Render adopted canonical quote/FX dependencies without re-resolving them."""

    return {
        "policy_version": context.policy_version,
        "base_currency": context.base_currency,
        "start_date": context.start_date.isoformat(),
        "end_date": context.end_date.isoformat(),
        "instrument_ids": sorted(context.instrument_ids),
        "quote_dependencies": [
            context.adopted_quote_dependencies[key]
            for key in sorted(context.adopted_quote_dependencies)
        ],
        "fx_dependencies": [
            context.adopted_fx_dependencies[key]
            for key in sorted(context.adopted_fx_dependencies)
        ],
    }


def validate_portfolio_market_data_dependencies_current(
    dependencies: dict[str, object],
) -> list[str]:
    """Re-resolve one persisted Portfolio market-data manifest.

    The manifest records the canonical quote/NAV window adopted by the solver
    and every point-in-time FX dependency adopted by the solve/Policy Replay.  A
    reliability read re-locks that exact window and compares those canonical
    dependencies directly; it does not rerun optimization or infer freshness
    from mutable "latest observation" dates.

    An empty result means the persisted dependencies are still current.  Any
    returned reason code is fail-closed and means the completed run must not be
    represented as current.
    """

    invalid_contract = ["market_data_dependency_contract_invalid"]
    if not isinstance(dependencies, dict):
        return invalid_contract

    policy_version = str(dependencies.get("policy_version") or "").strip()
    base_currency = str(dependencies.get("base_currency") or "").strip()
    start_date = _parse_iso_date(dependencies.get("start_date"))
    end_date = _parse_iso_date(dependencies.get("end_date"))
    raw_instrument_ids = dependencies.get("instrument_ids")
    raw_quote_dependencies = dependencies.get("quote_dependencies")
    raw_fx_dependencies = dependencies.get("fx_dependencies")
    if (
        not policy_version
        or not base_currency
        or start_date is None
        or end_date is None
        or start_date > end_date
        or not isinstance(raw_instrument_ids, list)
        or not isinstance(raw_quote_dependencies, list)
        or not isinstance(raw_fx_dependencies, list)
    ):
        return invalid_contract

    instrument_ids = [str(value or "").strip() for value in raw_instrument_ids]
    if (
        any(not value for value in instrument_ids)
        or len(instrument_ids) != len(set(instrument_ids))
        or instrument_ids != sorted(instrument_ids)
    ):
        return invalid_contract

    try:
        context = _lock_portfolio_market_data(
            instrument_ids=instrument_ids,
            base_currency=base_currency,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        # A reliability read must stay available even when a now-broken market
        # data dependency cannot be reconstructed.  The caller will surface a
        # stale reason rather than accepting the old run or failing open.
        return ["market_data_dependency_unavailable"]

    reasons: list[str] = []
    if context.policy_version != policy_version:
        reasons.append("market_data_policy_changed")

    for raw_dependency in raw_quote_dependencies:
        if not isinstance(raw_dependency, dict):
            reasons.append("market_data_dependency_contract_invalid")
            continue
        instrument_id = str(raw_dependency.get("instrument_id") or "").strip()
        role = str(raw_dependency.get("role") or "").strip()
        expected_window = raw_dependency.get("window_calculation_dependency")
        endpoint_dependency = raw_dependency.get("endpoint_calculation_dependency")
        if (
            not instrument_id
            or instrument_id not in instrument_ids
            or role != "total_return"
            or not isinstance(expected_window, dict)
            or not str(expected_window.get("fingerprint") or "").strip()
            or not isinstance(endpoint_dependency, dict)
            or not str(endpoint_dependency.get("fingerprint") or "").strip()
        ):
            reasons.append("market_data_dependency_contract_invalid")
            continue
        current_window = context.total_return_book.windows.get(instrument_id)
        if current_window is None:
            reasons.append("quote_dependency_changed")
            continue
        current_window_dependency = current_window.calculation_dependency.model_dump(
            mode="json"
        )
        if current_window_dependency != expected_window:
            reasons.append("quote_dependency_changed")

    for raw_dependency in raw_fx_dependencies:
        if not isinstance(raw_dependency, dict):
            reasons.append("market_data_dependency_contract_invalid")
            continue
        fx_base_currency = str(raw_dependency.get("base_currency") or "").strip()
        fx_quote_currency = str(raw_dependency.get("quote_currency") or "").strip()
        requested_as_of_date = _parse_iso_date(
            raw_dependency.get("requested_as_of_date")
        )
        if (
            not fx_base_currency
            or not fx_quote_currency
            or requested_as_of_date is None
            or requested_as_of_date < start_date
            or requested_as_of_date > end_date
            or not str(raw_dependency.get("fingerprint") or "").strip()
        ):
            reasons.append("market_data_dependency_contract_invalid")
            continue
        try:
            current_fx_dependency = context.fx_book.rate_at(
                fx_base_currency,
                fx_quote_currency,
                requested_as_of_date,
            ).calculation_dependency.model_dump(mode="json")
        except Exception:
            reasons.append("market_data_dependency_unavailable")
            continue
        if current_fx_dependency != raw_dependency:
            reasons.append("fx_dependency_changed")

    return list(dict.fromkeys(reasons))


@dataclass(frozen=True)
class _CanonicalSeriesContext:
    """Narrow adapter that keeps canonical series construction in one kernel."""

    base_currency: str
    market_data: PortfolioMarketDataContext


def _build_instrument_nav_series(
    state: _PortfolioMarketDataState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
    warn_on_start_clip: bool = True,
) -> tuple[pd.Series, list[str]]:
    context, window, endpoint, selected_points = _resolved_total_return_points(
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
    )
    warnings: list[str] = []
    rows: dict[date, Decimal] = {}
    for point in selected_points:
        fx_resolution = context.fx_book.rate_at(
            window.currency,
            state.base_currency,
            point.observation_date,
        )
        fx_dependency = fx_resolution.calculation_dependency.model_dump(mode="json")
        context.adopted_fx_dependencies[
            fx_resolution.calculation_dependency.fingerprint
        ] = fx_dependency
        if fx_resolution.resolution_status != "resolved" or fx_resolution.rate is None:
            raise PortfolioMarketDataError(
                f"{instrument_id} canonical FX dependency is unavailable on "
                f"{point.observation_date.isoformat()}.",
                reason_codes=list(fx_resolution.reason_codes or ["unavailable_fx_dependency"]),
                dependency={
                    "policy_version": context.policy_version,
                    "instrument_id": instrument_id,
                    "quote_observation_id": point.observation_id,
                    "quote_revision_id": point.revision_id,
                    "fx_calculation_dependency": fx_dependency,
                },
            )
        rows[point.observation_date] = point.value * fx_resolution.rate
    if not rows:
        raise PortfolioMarketDataError(
            f"{instrument_id} has no FX-complete canonical total-return history.",
            reason_codes=["insufficient_history"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "quote_calculation_dependency": endpoint.calculation_dependency.model_dump(
                    mode="json"
                ),
            },
        )
    visible = pd.Series(
        {point_date: float(value) for point_date, value in rows.items()},
        dtype="float64",
    ).sort_index()
    if warn_on_start_clip and visible.index[0] > start_date:
        warnings.append(
            f"{instrument_id} history starts on {visible.index[0].isoformat()}, so the market-data window is clipped for this member."
        )
    if endpoint.reliability_status == "qualified":
        warnings.append(
            f"{instrument_id} total-return endpoint is qualified under the explicit canonical freshness policy."
        )
    return visible, warnings


def build_canonical_total_return_nav_series(
    context: PortfolioMarketDataContext,
    *,
    instrument_id: str,
    base_currency: str,
    start_date: date,
    end_date: date,
    warn_on_start_clip: bool = True,
) -> tuple[pd.Series, list[str]]:
    """Build FX-complete canonical total-return NAV through the portfolio market-data kernel."""

    state = _CanonicalSeriesContext(
        base_currency=_normalized_currency(
            base_currency,
            context=f"Portfolio series '{instrument_id}' base",
        ),
        market_data=context,
    )
    return _build_instrument_nav_series(
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
        warn_on_start_clip=warn_on_start_clip,
    )
