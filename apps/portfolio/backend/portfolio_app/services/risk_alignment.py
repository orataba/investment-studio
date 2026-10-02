"""Calendar carry and missing-source semantics shared by production risk and Research."""
from datetime import date
from typing import Hashable

import numpy as np
import pandas as pd


def align_risk_navs(
    nav_by_key: dict[Hashable, pd.Series],
    *,
    coverage_by_key: dict[Hashable, dict[str, object]],
    calendar: list[date] | None = None,
    end_date: date | None = None,
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
    first, last = min(dates), end_date or max(dates)
    gap_dates_by_key = {}
    for key in nav_by_key:
        coverage = coverage_by_key.get(key) or {}
        gaps = {date.fromisoformat(str(raw)[:10]) for raw in coverage.get("gap_dates", [])}
        gaps = {day for day in gaps if day <= last}
        gap_dates_by_key[key] = gaps
        dates.update(day for day in gaps if day >= first)
    index = sorted(day for day in dates if first <= day <= last)
    aligned_by_key, threshold_ends = {}, {}
    for key, series in nav_by_key.items():
        series = series.sort_index()
        coverage = coverage_by_key.get(key) or {}
        basis = str(coverage.get("gap_detection_basis") or "")
        full_index = sorted(set(series.index).union(index))
        aligned = series.reindex(full_index).ffill()
        # Explicit NaN includes missing required FX; it must not become carry.
        invalid = set(series.index[series.isna()])
        gaps = gap_dates_by_key[key]
        if basis == "calendar_day_threshold":
            threshold_ends[key] = gaps
        else:
            invalid.update(gaps)
        for missing_date in sorted(invalid):
            following = series.index[series.index > missing_date]
            stop = following[0] if len(following) else None
            mask = aligned.index >= missing_date
            if stop is not None:
                mask &= aligned.index < stop
            aligned.loc[mask] = np.nan
        if not (basis.startswith("market_calendar:") or basis == "event_driven"):
            # Unknown schedules cannot authorize an indefinitely missing tail.
            aligned.loc[aligned.index > series.index[-1]] = np.nan
        aligned_by_key[key] = aligned.reindex(index)
    navs = pd.DataFrame(aligned_by_key, index=index)
    returns = navs.pct_change(fill_method=None)
    for key, ends in threshold_ends.items():
        returns.loc[returns.index.isin(ends), key] = np.nan
    return navs, returns
