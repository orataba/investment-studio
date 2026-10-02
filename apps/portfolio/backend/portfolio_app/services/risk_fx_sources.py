"""Resolve maintained, dated FX observations at the risk calculation boundary."""
from __future__ import annotations

from datetime import date

import pandas as pd

from investment_studio_instrument_core.fx_contract import FX_INSTRUMENT_IDENTITIES
from portfolio_app.services.market_data import analytical_return_quote_bases, resolve_quote_series
from portfolio_app.services.risk_basis import observation_coverage_from_dates, observation_source_settings
from portfolio_app.services.risk_currency import RiskFxHistory, align_base_currency_navs, risk_return_series_payload


def risk_fx_instrument_ids(currencies: set[str], base_currency: str) -> list[str]:
    base = base_currency.strip().upper()
    foreign = {currency.strip().upper() for currency in currencies} - {base, ""}
    if not foreign:
        return []
    required = {base, "USD", *foreign}
    return [identity.instrument_id for identity in FX_INSTRUMENT_IDENTITIES
            if {identity.base_currency, identity.quote_currency} <= required]


def risk_fx_histories_from_details(
    details: dict[str, dict[str, object] | None], *, end_date: date,
) -> dict[str, RiskFxHistory]:
    histories = {}
    for identity in FX_INSTRUMENT_IDENTITIES:
        detail = details.get(identity.instrument_id)
        if (not isinstance(detail, dict)
                or str(detail.get("instrument_type") or "").lower() != "fx"
                or str(detail.get("currency") or "").upper() != identity.quote_currency):
            continue
        resolved = resolve_quote_series(detail, candidate_bases=["spot"], end_date=end_date)
        if not resolved.available or not resolved.points:
            continue
        levels = pd.Series({point["as_of_date"]: float(point["value"])
                            for point in resolved.points}, dtype="float64").sort_index()
        histories[identity.instrument_id] = RiskFxHistory(
            instrument_id=identity.instrument_id,
            base_currency=identity.base_currency,
            quote_currency=identity.quote_currency,
            levels=levels,
            observation_coverage=observation_coverage_from_dates(
                list(levels.index), source_settings=observation_source_settings(detail), end_date=end_date,
            ),
        )
    return histories


def base_currency_risk_profiles(
    details: dict[str, dict[str, object] | None], *, base_currency: str, end_date: date,
    fx_histories: dict[str, RiskFxHistory],
) -> dict[str, dict[str, object] | None]:
    """Risk-only profiles with common periods across the entire catalogue.

    Prepare the union before converting members, so one market's holiday and
    another market's observation refer to the same return period. A missing FX
    path remains an individual member's failure, not a catalogue-wide failure.
    """
    prepared = {}
    calendar = set()
    profiles = dict.fromkeys(details)
    for key, detail in details.items():
        if not isinstance(detail, dict):
            continue
        resolved = resolve_quote_series(
            detail, candidate_bases=analytical_return_quote_bases(detail), end_date=end_date,
        )
        if not resolved.available or not resolved.points:
            continue
        native = pd.Series({point["as_of_date"]: float(point["value"])
                            for point in resolved.points}, dtype="float64").sort_index()
        coverage = observation_coverage_from_dates(
            list(native.index), source_settings=observation_source_settings(detail), end_date=end_date,
        )
        prepared[key] = (native, coverage, str(detail.get("currency") or ""))
        calendar.update(native.index)
        calendar.update(date.fromisoformat(str(day)[:10]) for day in coverage.get("gap_dates", []))
    if not prepared:
        return profiles
    first = min(calendar)
    for history in fx_histories.values():
        calendar.update(day for day in history.levels.index if first <= day <= end_date)
        calendar.update(date.fromisoformat(str(day)[:10])
                        for day in history.observation_coverage.get("gap_dates", [])
                        if first.isoformat() <= str(day)[:10] <= end_date.isoformat())
    shared_calendar = sorted(day for day in calendar if first <= day <= end_date)
    for key, (native, coverage, currency) in prepared.items():
        try:
            navs, returns, metadata = align_base_currency_navs(
                {key: native}, currency_by_key={key: currency}, coverage_by_key={key: coverage},
                base_currency=base_currency, fx_histories=fx_histories,
                calendar=shared_calendar, end_date=end_date,
            )
        except ValueError as error:
            profiles[key] = {"currency": base_currency, "source_currency": currency,
                             "points": [], "unavailable_reason": str(error)}
            continue
        profiles[key] = risk_return_series_payload(navs[key], returns[key], metadata[key])
    return profiles


def base_currency_risk_profile(
    detail: dict[str, object] | None, *, base_currency: str, end_date: date,
    fx_histories: dict[str, RiskFxHistory],
) -> dict[str, object] | None:
    return base_currency_risk_profiles(
        {"instrument": detail}, base_currency=base_currency, end_date=end_date,
        fx_histories=fx_histories,
    )["instrument"]
