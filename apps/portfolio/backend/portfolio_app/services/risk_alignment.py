"""Calendar carry and missing-source semantics shared by production risk and Research."""
from datetime import date
from typing import Hashable

import numpy as np
import pandas as pd
from investment_studio_instrument_core.return_series import return_interval_unavailable_reason
from portfolio_app.services.risk_basis import observation_schedule_verified


def align_risk_navs(
    nav_by_key: dict[Hashable, pd.Series],
    *,
    coverage_by_key: dict[Hashable, dict[str, object]],
    calendar: list[date] | None = None,
    end_date: date | None = None,
    return_contracts_by_key: dict[Hashable, dict[str, object]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carry legal publication/holiday marks while preserving unavailable quotes.

    Missing expected sessions join the diagnostic calendar even when every
    member missed them. A missing level invalidates both adjoining returns;
    the later observed multi-session change cannot masquerade as a daily one.
    """
    if not nav_by_key or any(series.empty for series in nav_by_key.values()):
        raise ValueError("Risk alignment requires non-empty observed histories for every member.")
    dates = set(calendar) if calendar is not None else set().union(*(set(series.index) for series in nav_by_key.values()))
    if not dates:
        raise ValueError("Risk alignment requires a non-empty observation calendar.")
    unverified = [str(key) for key in nav_by_key
                  if not observation_schedule_verified(coverage_by_key.get(key))]
    if unverified:
        raise ValueError(
            "Daily risk unavailable: observation schedule unverified for " + ", ".join(unverified)
            + "; requires a verified market calendar or explicit event_driven source contract."
        )
    first, last = min(dates), end_date or max(dates)
    gap_dates_by_key = {}
    for key in nav_by_key:
        coverage = coverage_by_key.get(key) or {}
        gaps = {date.fromisoformat(str(raw)[:10]) for raw in coverage.get("gap_dates", [])}
        gaps = {day for day in gaps if day <= last}
        gap_dates_by_key[key] = gaps
        dates.update(day for day in gaps if day >= first)
    index = sorted(day for day in dates if first <= day <= last)
    aligned_by_key, invalid_return_ends = {}, {}
    for key, series in nav_by_key.items():
        series = series.sort_index()
        coverage = coverage_by_key.get(key) or {}
        basis = str(coverage.get("gap_detection_basis") or "")
        invalid_return_ends[key] = {
            date.fromisoformat(str(day)[:10]) for day in coverage.get("invalid_return_dates", [])
        }
        full_index = sorted(set(series.index).union(index))
        aligned = series.reindex(full_index).ffill()
        # Explicit NaN includes missing required FX; it must not become carry.
        invalid = set(series.index[series.isna()])
        gaps = gap_dates_by_key[key]
        invalid.update(gaps)
        for missing_date in sorted(invalid):
            following = series.index[series.index > missing_date]
            stop = following[0] if len(following) else None
            mask = aligned.index >= missing_date
            if stop is not None:
                mask &= aligned.index < stop
            aligned.loc[mask] = np.nan
        legal_carry = basis.startswith("market_calendar:") or basis == "event_driven"
        if basis == "base_currency_components":
            components = coverage.get("component_coverage") or {}
            covered_through = coverage.get("end_date")
            legal_carry = (bool(components) and bool(covered_through)
                           and str(covered_through)[:10] >= last.isoformat()
                           and observation_schedule_verified(coverage))
        if not legal_carry:
            # Composite coverage cannot authorize carry after its verified cutoff.
            aligned.loc[aligned.index > series.index[-1]] = np.nan
        aligned_by_key[key] = aligned.reindex(index)
    navs = pd.DataFrame(aligned_by_key, index=index)
    returns = navs.pct_change(fill_method=None)
    for key, ends in invalid_return_ends.items():
        returns.loc[returns.index.isin(ends), key] = np.nan
    for key, contract in (return_contracts_by_key or {}).items():
        if not any(field in contract for field in ("total_return_segments", "total_return_unavailable_intervals")):
            continue
        if (coverage_by_key.get(key) or {}).get("gap_detection_basis") == "base_currency_components":
            # Native segment checks already ran before FX composition. Its
            # invalid_return_dates, above, preserve that result on the union clock.
            continue
        source_dates = pd.Series(nav_by_key[key].index, index=nav_by_key[key].index)
        source_dates = source_dates.reindex(sorted(set(source_dates.index).union(returns.index))).ffill()
        for start, end in zip(returns.index, returns.index[1:]):
            source_start, source_end = source_dates.loc[start], source_dates.loc[end]
            # Legal calendar carry has no new native economic observation.
            # Its FX leg can still move; TR segment endpoints are source dates.
            if (pd.notna(returns.at[end, key]) and source_start < source_end
                    and return_interval_unavailable_reason(source_start, source_end, source_settings=contract,
                                                           quote_basis=contract.get("quote_basis"))):
                returns.at[end, key] = np.nan
    # Attach interval metadata only after matrix operations. Pandas propagates
    # attrs by deepcopy even on temporary column selections and masks.
    returns.attrs["return_period_starts"] = {
        day.isoformat(): previous.isoformat() for previous, day in zip(index, index[1:])
    }
    return navs, returns
