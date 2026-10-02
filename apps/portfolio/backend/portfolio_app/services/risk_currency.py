"""Base-currency risk wealth from observed native and FX component histories.

The caller owns source loading and publication identity. This module performs
no I/O and never substitutes a native return for unavailable currency risk.
"""
from dataclasses import dataclass
from datetime import date
from typing import Hashable

import numpy as np
import pandas as pd

from portfolio_app.services.risk_alignment import align_risk_navs


@dataclass(frozen=True)
class RiskFxHistory:
    instrument_id: str
    base_currency: str
    quote_currency: str
    levels: pd.Series
    observation_coverage: dict[str, object]


def risk_return_series_payload(
    navs: pd.Series, returns: pd.Series, metadata: dict[str, object],
) -> dict[str, object]:
    """Serialize every aligned interval; unavailable intervals remain null."""
    first = navs.first_valid_index()
    points = []
    for index in range(1, len(returns.index)):
        previous, day = returns.index[index - 1], returns.index[index]
        if first is not None and previous < first:
            continue
        value = returns.loc[day]
        points.append({"start_date": previous.isoformat(), "date": day.isoformat(),
                       "value": float(value) if pd.notna(value) else None})
    return {"first_return_start_date": first.isoformat() if first is not None else None,
            "points": points, **metadata}


def _currency(value: str) -> str:
    result = str(value or "").strip().upper()
    if not result:
        raise ValueError("Base-currency risk requires an explicit return currency.")
    return result


def _fx_path(currency, base_currency, histories):
    pairs = {}
    for key, history in histories.items():
        if key != history.instrument_id:
            raise ValueError("FX history key does not match its source instrument identity.")
        pair = (_currency(history.base_currency), _currency(history.quote_currency))
        if pair in pairs:
            raise ValueError(f"Ambiguous FX history for {pair[0]}/{pair[1]}.")
        pairs[pair] = history

    def leg(source, target):
        if (source, target) in pairs:
            return [(pairs[(source, target)], 1)]
        if (target, source) in pairs:
            return [(pairs[(target, source)], -1)]
        return None

    direct = leg(currency, base_currency)
    if direct is not None:
        return direct
    if currency != "USD" and base_currency != "USD":
        left, right = leg(currency, "USD"), leg("USD", base_currency)
        if left is not None and right is not None:
            return left + right
    raise ValueError(
        f"Base-currency risk requires observed FX history for {currency} to {base_currency}."
    )


def align_base_currency_navs(
    nav_by_key: dict[Hashable, pd.Series],
    *,
    currency_by_key: dict[Hashable, str],
    coverage_by_key: dict[Hashable, dict[str, object]],
    base_currency: str,
    fx_histories: dict[str, RiskFxHistory] | None = None,
    calendar: list[date] | None = None,
    end_date: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[Hashable, dict[str, object]]]:
    """Align components first, then multiply wealth and take same-period returns.

    FX-only observation days join the calendar, including a local exchange's
    holiday. A source's missing expected observation remains missing, even when
    another component is observed that day. Direct/inverse/USD-pivot selection
    applies to the full history; a broken selected path is not silently swapped.
    """
    base_currency = _currency(base_currency)
    if not nav_by_key or any(series.empty for series in nav_by_key.values()):
        raise ValueError("Base-currency risk requires non-empty native histories.")
    native_start = min(min(series.index) for series in nav_by_key.values())
    end_date = end_date or max(max(series.index) for series in nav_by_key.values())
    components, component_coverage, native_keys, paths = {}, {}, {}, {}
    for index, (key, series) in enumerate(nav_by_key.items()):
        native_key = f"native:{index}"
        native_keys[key] = native_key
        components[native_key] = series
        component_coverage[native_key] = coverage_by_key.get(key) or {}
        currency = _currency(currency_by_key.get(key, ""))
        paths[key] = [] if currency == base_currency else _fx_path(currency, base_currency, fx_histories or {})
        for history, _exponent in paths[key]:
            fx_key = f"fx:{history.instrument_id}"
            if fx_key in components:
                continue
            levels = history.levels.sort_index()
            if levels.index.has_duplicates:
                raise ValueError(f"FX history {history.instrument_id} has duplicate dates.")
            if end_date is not None:
                levels = levels.loc[levels.index <= end_date]
            observed = levels.dropna()
            if observed.empty or not np.isfinite(observed).all() or (observed <= 0).any():
                raise ValueError(f"FX history {history.instrument_id} requires finite positive observed levels.")
            components[fx_key] = levels
            component_coverage[fx_key] = history.observation_coverage

    # Keep pre-start component observations as lawful opening marks, without
    # adding years before any native exposure history to the output calendar.
    first_output = min(calendar) if calendar else native_start
    union = set(calendar or [])
    for series in components.values():
        union.update(day for day in series.index if first_output <= day <= end_date)
    aligned, component_returns = align_risk_navs(
        components, coverage_by_key=component_coverage,
        calendar=sorted(union), end_date=end_date,
    )
    wealth_by_key, invalid_returns, metadata = {}, {}, {}
    for key, native_key in native_keys.items():
        wealth = aligned[native_key].copy()
        relevant = [native_key]
        for history, exponent in paths[key]:
            fx_key = f"fx:{history.instrument_id}"
            wealth = wealth * aligned[fx_key].pow(exponent)
            relevant.append(fx_key)
        wealth_by_key[key] = wealth
        # Threshold-based source diagnostics may invalidate an interval while
        # both endpoint levels remain known; multiplication must retain that.
        invalid_returns[key] = component_returns[relevant].isna().any(axis=1)
        coverage = dict(coverage_by_key.get(key) or {})
        if paths[key]:
            gaps = set()
            first_valid = wealth.first_valid_index()
            gaps.update(day.isoformat() for day in wealth.index[wealth.isna()]
                        if first_valid is not None and day >= first_valid)
            coverage = {
                "start_date": wealth.first_valid_index().isoformat() if wealth.first_valid_index() else None,
                "end_date": (end_date or wealth.index[-1]).isoformat(),
                "gap_dates": sorted(day for day in gaps if day <= (end_date or wealth.index[-1]).isoformat()),
                "gap_detection_basis": "base_currency_components",
                "invalid_return_dates": [day.isoformat() for day in wealth.index[invalid_returns[key]]
                                         if first_valid is not None and day > first_valid],
                "component_coverage": {component_key: component_coverage[component_key] for component_key in relevant},
            }
        metadata[key] = {
            "currency": base_currency,
            "source_currency": _currency(currency_by_key[key]),
            "source_instrument_ids": sorted({history.instrument_id for history, _ in paths[key]}),
            "observation_coverage": coverage,
        }
    navs = pd.DataFrame(wealth_by_key, index=aligned.index)
    returns = navs.pct_change(fill_method=None)
    for key, missing in invalid_returns.items():
        returns.loc[missing, key] = np.nan
    return navs, returns, metadata
