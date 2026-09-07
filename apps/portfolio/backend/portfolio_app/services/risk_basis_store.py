from __future__ import annotations

from datetime import date, timedelta
from math import isfinite

from investment_studio_instrument_core import canonical_price_contract
from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData
from sqlalchemy import Numeric, and_, case, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.market_data import (
    SUPPORTED_PRIMARY_QUOTE_BASES,
    analytical_return_quote_bases,
)
from portfolio_app.services.risk_basis import (
    _normalized_instrument_ids,
    calculation_frequency_profile_from_observation_dates,
)


# Match the decimal format enforced by the canonical market-data write path.
# The CASE prevents a malformed persisted value from being cast by PostgreSQL.
_DECIMAL_PATTERN = r"^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"


def _valid_series_summary(summary, *, instrument_type: str, currency: str) -> bool:
    if (
        summary.invalid_value_count
        or summary.observation_count != summary.unique_date_count
        or summary.observation_count != summary.scale_count
        or summary.min_metric_family != summary.max_metric_family
        or summary.min_currency != summary.max_currency
        or summary.min_currency != currency.strip().upper()
        or summary.min_price_unit != summary.max_price_unit
        or summary.min_price_scale != summary.max_price_scale
    ):
        return False
    for value in (summary.min_value, summary.max_value):
        if value is None or not isfinite(float(value)) or float(value) <= 0:
            return False
    try:
        expected_unit, expected_scale = canonical_price_contract(
            instrument_type=instrument_type.strip().lower(),
            metric_family=summary.min_metric_family,
            quote_basis=summary.quote_basis,
        )
    except ValueError:
        return False
    return (
        summary.min_price_unit == expected_unit
        and summary.min_price_scale is not None
        and isfinite(float(summary.min_price_scale))
        and abs(float(summary.min_price_scale) - float(expected_scale)) <= 1e-12
    )


def calculation_frequency_profile_from_registry(
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    end_date: date,
    lookback_days: int = 366,
) -> dict[str, object]:
    """Read source identity summaries and window dates, without market-data JSON.

    Financial routes retain their published-generation check around this read.
    Basis selection examines all usable observations through the requested end;
    a preferred old or invalid series must never fall through to another basis.
    """
    normalized_ids = _normalized_instrument_ids(instrument_ids)
    observations: dict[str, list[date]] = {}
    source_settings: dict[str, object] = {}
    if normalized_ids:
        try:
            with get_session_factory()() as session:
                instruments = session.execute(
                    select(
                        Instrument.instrument_id,
                        Instrument.instrument_type,
                        Instrument.currency,
                        Instrument.quote_selection_policy_json,
                        Instrument.source_settings_json,
                    ).where(Instrument.instrument_id.in_(normalized_ids))
                ).all()
                candidates: dict[str, list[str]] = {}
                instrument_metadata = {}
                for instrument in instruments:
                    instrument_id = instrument.instrument_id
                    observations[instrument_id] = []
                    source_settings[instrument_id] = instrument.source_settings_json
                    bases = analytical_return_quote_bases({
                        "instrument_type": instrument.instrument_type,
                        "quote_selection_policy": instrument.quote_selection_policy_json,
                    })
                    if bases and all(basis in SUPPORTED_PRIMARY_QUOTE_BASES for basis in bases):
                        candidates[instrument_id] = bases
                        instrument_metadata[instrument_id] = instrument

                if candidates:
                    market = InstrumentMarketData
                    basis = func.lower(func.trim(market.quote_basis))
                    usable = func.lower(func.trim(market.status)) == "complete"
                    metric = func.lower(func.trim(market.metric_family))
                    currency = func.upper(func.trim(market.currency))
                    price_unit = func.lower(func.trim(func.coalesce(market.price_unit, "")))
                    decimal_value = func.trim(market.value)
                    valid_decimal = decimal_value.regexp_match(_DECIMAL_PATTERN)
                    numeric_value = case(
                        (valid_decimal, cast(decimal_value, Numeric(asdecimal=False))),
                        else_=None,
                    )
                    summaries = session.execute(
                        select(
                            market.instrument_id,
                            basis.label("quote_basis"),
                            func.count().label("observation_count"),
                            func.count(func.distinct(market.as_of_date)).label("unique_date_count"),
                            func.count(market.price_scale).label("scale_count"),
                            func.min(metric).label("min_metric_family"),
                            func.max(metric).label("max_metric_family"),
                            func.min(currency).label("min_currency"),
                            func.max(currency).label("max_currency"),
                            func.min(price_unit).label("min_price_unit"),
                            func.max(price_unit).label("max_price_unit"),
                            func.min(market.price_scale).label("min_price_scale"),
                            func.max(market.price_scale).label("max_price_scale"),
                            func.min(numeric_value).label("min_value"),
                            func.max(numeric_value).label("max_value"),
                            func.sum(case((valid_decimal, 0), else_=1)).label("invalid_value_count"),
                        )
                        .where(
                            market.instrument_id.in_(candidates),
                            basis.in_({item for bases in candidates.values() for item in bases}),
                            usable,
                            market.as_of_date <= end_date,
                        )
                        .group_by(market.instrument_id, basis)
                    ).all()
                    summary_by_identity = {
                        (row.instrument_id, row.quote_basis): row for row in summaries
                    }
                    selected: dict[str, str] = {}
                    for instrument_id, bases in candidates.items():
                        instrument = instrument_metadata[instrument_id]
                        for candidate in bases:
                            summary = summary_by_identity.get((instrument_id, candidate))
                            if summary is None:
                                continue
                            if _valid_series_summary(
                                summary,
                                instrument_type=instrument.instrument_type,
                                currency=instrument.currency,
                            ):
                                selected[instrument_id] = candidate
                            break

                    if selected:
                        start_date = end_date - timedelta(days=max(lookback_days, 1))
                        for instrument_id, observation_date in session.execute(
                            select(market.instrument_id, market.as_of_date)
                            .where(
                                or_(*[
                                    and_(market.instrument_id == instrument_id, basis == selected_basis)
                                    for instrument_id, selected_basis in selected.items()
                                ]),
                                usable,
                                market.as_of_date >= start_date,
                                market.as_of_date <= end_date,
                            )
                            .order_by(market.instrument_id, market.as_of_date)
                        ):
                            observations[instrument_id].append(observation_date)
        except SQLAlchemyError as error:
            raise InstrumentRegistryError("Failed to query shared instrument risk observations.") from error

    return calculation_frequency_profile_from_observation_dates(
        normalized_ids,
        observation_dates_by_instrument=observations,
        source_settings_by_instrument=source_settings,
        end_date=end_date,
        lookback_days=lookback_days,
    )
