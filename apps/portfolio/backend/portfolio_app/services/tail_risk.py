"""Empirical losses from today's signed exposures and common historical shocks.

This is a market-data scenario calculation, never an operational NAV-return
quantile. Actual observation intervals must agree; missing prices are not filled.
"""
from __future__ import annotations

from datetime import date, timedelta
from math import ceil, floor, isfinite
from urllib.parse import quote

from portfolio_app.services.market_data import market_calendar_sessions, resolve_quote_series
from portfolio_app.services.valuation_fx import fx_direct_instrument_map


DEFAULT_CONFIDENCE = 0.95
DEFAULT_LOOKBACK_DAYS = 1095


def _number(value):
    try:
        result = float(value) if value is not None and not isinstance(value, bool) else None
        return result if result is not None and isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def empirical_tail(losses: list[float], confidence: float) -> dict[str, object]:
    """Inverse empirical CDF VaR and integrated upper-tail ES, including atoms.

    Averaging `loss >= VaR` incorrectly changes the requested tail probability
    for ties and finite samples. Fractional boundary mass preserves it exactly.
    """
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be strictly between zero and one.")
    if any(_number(value) is None for value in losses):
        raise ValueError("Historical losses must be finite numbers.")
    ordered = sorted(float(value) for value in losses)
    count = len(ordered)
    mass = count * (1 - confidence)
    if abs(mass - round(mass)) < 1e-10:
        mass = float(round(mass))
    result = {"observation_count": count, "tail_effective_observations": mass,
              "tail_observation_count": ceil(mass),
              "tail_max_observation_weight": min(1.0, 1 / mass) if mass else None,
              "var": None, "expected_shortfall": None}
    # Below one scenario of tail mass, empirical VaR/ES would both use only the
    # maximum loss. Suppress that unresolved tail by product policy; the empirical
    # quantile is mathematically defined. Larger samples do not imply adequacy.
    if mass < 1:
        return result
    rank = ceil(confidence * count - 1e-10) - 1
    descending = ordered[::-1]
    whole = floor(mass)
    tail_sum = sum(descending[:whole])
    fraction = mass - whole
    if fraction:
        tail_sum += fraction * descending[whole]
    return {**result, "var": ordered[max(rank, 0)], "expected_shortfall": tail_sum / mass}


def _daily_periods(points, *, detail, start_date, end_date):
    """Keep observed one-session changes, never relabel weekly changes as daily."""
    settings = (detail or {}).get("source_settings") or {}
    frequency = str(settings.get("expected_frequency") or "").lower()
    # Registry's event_driven denotes a delivery schedule, not a weekly return
    # horizon. Such sources still need actual one-day observation boundaries.
    if frequency and frequency not in {"daily", "event_driven", "business_daily", "trading_daily"}:
        return {}, {"reason": "non_daily_source", "rejected_period_count": len(points), "calendar_basis": None}, None
    calendar = str(settings.get("market_calendar") or (detail or {}).get("exchange_code") or "")
    sessions = market_calendar_sessions(calendar, start_date, end_date) if calendar else None
    session_pairs = set(zip(sessions, sessions[1:])) if sessions is not None else None
    calendar_basis = f"market_calendar:{calendar}" if session_pairs is not None else "consecutive_calendar_dates_only"
    periods, rejected, seen_ends = {}, 0, set()
    for point in points:
        if not isinstance(point, dict):
            return {}, {"reason": "invalid_return_period", "rejected_period_count": len(points), "calendar_basis": calendar_basis}, session_pairs
        try:
            start, end = _date(point.get("start_date")), _date(point.get("date"))
        except (ValueError, TypeError):
            return {}, {"reason": "invalid_return_period", "rejected_period_count": len(points), "calendar_basis": calendar_basis}, session_pairs
        if end > end_date or start < start_date or end <= start_date:
            continue
        value = _number(point.get("value"))
        if start >= end or value is None or value < -1 or end in seen_ends:
            return {}, {"reason": "invalid_return_period", "rejected_period_count": len(points), "calendar_basis": calendar_basis}, session_pairs
        seen_ends.add(end)
        one_day = (start, end) in session_pairs if session_pairs is not None else (end - start).days == 1
        if not one_day:
            rejected += 1
            continue
        periods[(start, end)] = value
    return periods, {"reason": None if periods else "no_verified_daily_returns",
                     "rejected_period_count": rejected, "calendar_basis": calendar_basis}, session_pairs


def _fx_rate_series(currency, base_currency, *, direct, details, as_of_date):
    """Exact observed FX levels; direct, inverse, or the existing USD pivot."""
    pair = (currency, base_currency)
    inverse = False
    instrument_id = direct.get(pair)
    if not instrument_id:
        instrument_id = direct.get(pair[::-1])
        inverse = True
    if instrument_id:
        detail = details.get(instrument_id)
        if not detail:
            return {}, [], None
        resolution = resolve_quote_series(detail, candidate_bases=["spot"], end_date=as_of_date)
        if not resolution.available:
            return {}, [instrument_id], detail
        rates = {}
        for point in resolution.points:
            value = _number(point.get("value"))
            if value is not None and value > 0:
                rates[_date(point["as_of_date"])] = 1 / value if inverse else value
        return rates, [instrument_id], detail
    if "USD" in pair:
        return {}, [], None
    left, left_ids, left_detail = _fx_rate_series(currency, "USD", direct=direct, details=details, as_of_date=as_of_date)
    right, right_ids, right_detail = _fx_rate_series("USD", base_currency, direct=direct, details=details, as_of_date=as_of_date)
    # Both source legs must have an actual observation at each boundary.
    rates = {day: left[day] * right[day] for day in left.keys() & right.keys()}
    # Both legs must prove the same daily horizon, including the exchange-code
    # calendar fallback used by _daily_periods. Equal source_settings alone does
    # not establish this: one leg may trade during the other's non-session days.
    # Otherwise keep the existing explicitly unverified consecutive-date basis.
    def daily_basis(detail):
        settings = (detail or {}).get("source_settings") or {}
        return (
            str(settings.get("expected_frequency") or "").lower(),
            str(settings.get("market_calendar") or (detail or {}).get("exchange_code") or ""),
        )

    matching_basis = daily_basis(left_detail) == daily_basis(right_detail)
    return rates, sorted(set(left_ids + right_ids)), left_detail if matching_basis else None


def _history_coverage(common, expected, *, start_date, end_date):
    ordered = sorted(common, key=lambda period: period[1])
    first = ordered[0][0] if ordered else None
    last = ordered[-1][1] if ordered else None
    missing = expected - common if expected is not None else None
    # Expected periods come from verified source calendars, never from filling
    # market observations. Leading absence may be a young fund or short source
    # history; it is not asserted to be a missing NAV before the fund existed.
    leading = sum(period[1] <= first for period in missing) if missing is not None and first else None
    trailing = sum(period[0] >= last for period in missing) if missing is not None and last else None
    internal = len(missing) - leading - trailing if leading is not None and trailing is not None else None
    status = "unavailable" if not common else "unverified" if expected is None else "partial" if missing else "complete"
    return {
        "history_coverage_status": status,
        "expected_common_observation_count": len(expected) if expected is not None else None,
        "uncovered_observation_count": len(missing) if missing is not None else None,
        "uncovered_leading_observation_count": leading,
        "missing_internal_observation_count": internal,
        "uncovered_trailing_observation_count": trailing,
        "actual_history_days": (last - first).days if first and last else None,
        "history_span_fraction": (last - first).days / (end_date - start_date).days if first and last else None,
    }


def project_portfolio_tail_risk(workspace, *, confidence=DEFAULT_CONFIDENCE,
                                lookback_days=DEFAULT_LOOKBACK_DAYS,
                                instrument_details=None, fx_payload=None):
    if not 0 < confidence < 1 or not 1 <= lookback_days <= 3650:
        raise ValueError("Confidence must be between zero and one; lookback must be 1–3650 days.")
    as_of_date = _date(workspace["as_of_date"])
    start_date = as_of_date - timedelta(days=lookback_days)
    nav = _number((workspace.get("totals") or {}).get("nav"))
    base_currency = str(workspace.get("base_currency") or "").upper()
    details = instrument_details or {}
    direct = fx_direct_instrument_map(fx_payload or {})
    rows, modeled, issues = [], [], []
    for index, holding in enumerate(workspace.get("rows") or []):
        core = holding.get("instrument_core") or {}
        iid = core.get("instrument_id")
        value = _number(holding.get("market_value_base"))
        currency = str(core.get("currency") or "").upper()
        kind = str(core.get("instrument_type") or "").lower()
        category = holding.get("holding_category")
        row = {"holding_id": holding.get("position_reference_id") or holding.get("derivative_contract_id") or holding.get("line_id") or str(index),
               "instrument_id": iid, "name": core.get("instrument_name") or (holding.get("derivative_contract") or {}).get("contract_name") or iid or "—",
               "market_value_base": value, "weight": value / nav if value is not None and nav and nav > 0 else None,
               "status": "excluded", "reason": None, "observation_count": 0,
               "rejected_period_count": 0, "calendar_basis": None, "fx_instrument_ids": [],
               "fx_rejected_period_count": 0, "unmatched_fx_period_count": 0,
               "first_scenario_start_date": None, "last_scenario_end_date": None}
        rows.append(row)
        if holding.get("derivative_contract_id") or category == "derivatives" or kind in {"fcn", "option"}:
            row["reason"] = "derivative_fair_value_unmodeled"
            continue
        if value is None:
            row["reason"] = "missing_base_value"
            continue
        if value == 0:
            row.update(status="no_exposure", reason="zero_exposure")
            continue
        if not currency or not base_currency:
            row["reason"] = "missing_currency"
            continue
        monetary = category == "cash_and_settlement" or holding.get("holding_kind") in {"cash", "pending_settlement"} or kind == "cash"
        if monetary and currency == base_currency:
            row.update(status="base_currency_cash", reason="base_currency_cash_zero_market_shock")
            continue
        detail = details.get(iid) or {}
        if monetary:
            local = None
            expected = None
            metadata = {"rejected_period_count": 0, "calendar_basis": None}
        else:
            points = (holding.get("instrument_return_series_all") or {}).get("points") or []
            local, metadata, expected = _daily_periods(points, detail=detail, start_date=start_date, end_date=as_of_date)
            row.update(metadata)
            if not local:
                continue
        if currency != base_currency:
            rates, source_ids, fx_detail = _fx_rate_series(currency, base_currency, direct=direct, details=details, as_of_date=as_of_date)
            row["fx_instrument_ids"] = source_ids
            ordered = sorted(rates)
            fx_points = [{"start_date": left, "date": right, "value": rates[right] / rates[left] - 1}
                         for left, right in zip(ordered, ordered[1:])]
            fx_returns, fx_metadata, fx_expected = _daily_periods(fx_points, detail=fx_detail, start_date=start_date, end_date=as_of_date)
            row["fx_rejected_period_count"] = fx_metadata["rejected_period_count"]
            row["unmatched_fx_period_count"] = len(local.keys() - fx_returns.keys()) if local is not None else 0
            if not fx_returns:
                row["reason"] = "missing_aligned_fx_returns"
                continue
            if local is None:
                local = fx_returns
                expected = fx_expected
                row.update(fx_metadata)
            else:
                expected = expected & fx_expected if expected is not None and fx_expected is not None else None
                local = {period: (1 + local[period]) * (1 + fx_returns[period]) - 1
                         for period in local.keys() & fx_returns.keys()}
            if not local:
                row["reason"] = "missing_aligned_fx_returns"
                continue
        if not local:
            row["reason"] = "no_verified_daily_returns"
            continue
        row.update(status="modeled", reason=None, observation_count=len(local),
                   first_scenario_start_date=min(period[0] for period in local).isoformat(),
                   last_scenario_end_date=max(period[1] for period in local).isoformat())
        modeled.append((row, local, expected))

    common = set.intersection(*(set(series) for _, series, _ in modeled)) if modeled else set()
    expected_common = (set.intersection(*(expected for _, _, expected in modeled))
                       if modeled and all(expected is not None for _, _, expected in modeled) else None)
    history = _history_coverage(common, expected_common, start_date=start_date, end_date=as_of_date)
    ordered_periods = sorted(common, key=lambda item: item[1])
    losses = [-sum(row["market_value_base"] * series[period] for row, series, _ in modeled) for period in ordered_periods]
    distribution = empirical_tail(losses, confidence)
    excluded = [row for row in rows if row["status"] == "excluded"]
    modeled_gross = sum(abs(row["market_value_base"]) for row, _, _ in modeled)
    excluded_gross = (sum(abs(row["market_value_base"]) for row in excluded)
                      if all(row["market_value_base"] is not None for row in excluded) else None)
    total_gross = sum(abs(row["market_value_base"]) for row in rows if row["market_value_base"] is not None)
    if nav is None or nav <= 0:
        issues.append("invalid_portfolio_nav")
    if not modeled:
        issues.append("no_modeled_market_exposure")
    elif not common:
        issues.append("no_common_daily_periods")
    elif distribution["var"] is None:
        issues.append("less_than_one_tail_observation")
    if excluded:
        issues.append("partial_market_risk_coverage")
    if any(row["rejected_period_count"] for row in rows):
        issues.append("non_daily_or_unverified_intervals_removed")
    if any(row["fx_rejected_period_count"] for row in rows):
        issues.append("unverified_fx_intervals_removed")
    if any(row["unmatched_fx_period_count"] for row in rows):
        issues.append("unmatched_security_fx_periods_removed")
    if modeled and any(row["observation_count"] != len(common) for row, _, _ in modeled):
        issues.append("common_period_intersection")
    if history["history_coverage_status"] == "partial":
        issues.append("requested_history_partially_covered")
    elif history["history_coverage_status"] == "unverified":
        issues.append("requested_history_calendar_unverified")
    latest = ordered_periods[-1][1] if ordered_periods else None
    if latest and latest < as_of_date:
        issues.append("scenario_history_ends_before_as_of")
    available = distribution["var"] is not None and nav is not None and nav > 0
    return {
        "portfolio_id": workspace.get("portfolio_id"), "as_of_date": as_of_date.isoformat(),
        "base_currency": base_currency, "portfolio_nav": nav,
        "status": "available" if available else "unavailable",
        "coverage_status": "partial" if excluded else "complete",
        "method": "historical_simulation_current_exposures", "horizon": "one_observed_market_session",
        "confidence": confidence, "lookback_days": lookback_days,
        "window_start_date": start_date.isoformat(), "window_end_date": as_of_date.isoformat(),
        **history,
        "first_scenario_start_date": ordered_periods[0][0].isoformat() if ordered_periods else None,
        "last_scenario_end_date": latest.isoformat() if latest else None,
        "observation_count": distribution["observation_count"],
        "tail_effective_observations": distribution["tail_effective_observations"],
        "tail_observation_count": distribution["tail_observation_count"],
        "tail_max_observation_weight": distribution["tail_max_observation_weight"],
        "var_amount": distribution["var"] if available else None,
        "var_nav_fraction": distribution["var"] / nav if available else None,
        "expected_shortfall_amount": distribution["expected_shortfall"] if available else None,
        "expected_shortfall_nav_fraction": distribution["expected_shortfall"] / nav if available else None,
        "modeled_gross_exposure": modeled_gross, "excluded_gross_exposure": excluded_gross,
        "modeled_gross_nav_fraction": modeled_gross / nav if nav and nav > 0 else None,
        "excluded_gross_nav_fraction": excluded_gross / nav if excluded_gross is not None and nav and nav > 0 else None,
        "modeled_fraction_of_known_gross": modeled_gross / total_gross if total_gross else None,
        "rows": rows, "limitations": list(dict.fromkeys(issues)),
        "sources": [{
            "source_id": f"portfolio-tail-risk:{workspace.get('portfolio_id')}:{as_of_date.isoformat()}:{confidence}:{lookback_days}",
            "source_type": "portfolio_tail_risk",
            "title": "Current portfolio historical-scenario VaR and Expected Shortfall",
            "portfolio_id": workspace.get("portfolio_id"),
            "start_date": start_date.isoformat(), "end_date": as_of_date.isoformat(),
            "holdings_as_of_date": as_of_date.isoformat(), "currency": base_currency,
            "frequency": "daily", "method": "historical_simulation_current_exposures",
            "confidence": confidence, "lookback_days": lookback_days,
            "scenario_start_date": ordered_periods[0][0].isoformat() if ordered_periods else None,
            "scenario_end_date": latest.isoformat() if latest else None,
            "observation_count": distribution["observation_count"],
            "tail_effective_observations": distribution["tail_effective_observations"],
            "coverage_status": "partial" if excluded else "complete",
            **history,
            "result_status": "available" if available else "unavailable",
            "date_basis": "Current signed holdings at the stated date; actual historical one-session return intervals within the requested window. Calendar-date alignment does not imply identical intraday closing times across markets.",
            "detail_path": f"/portfolios/{quote(str(workspace.get('portfolio_id') or ''), safe='')}/risk",
        }],
        "interpretation": "Observed market shocks applied to current signed exposures; equal scenario probabilities; no square-root-of-time scaling. Positive values are losses. Negative VaR/ES remain signed gains, not clipped losses.",
        "precision_note": "Empirical tail mass is N × (1 − confidence), not independent evidence count. One observation has probability weight up to 1 / tail mass in the ES average, not a bound on its monetary contribution. Dependence, missing sessions and a changing regime limit precision; a numeric result is not a claim of adequate tail coverage.",
        "scope_note": "FCN and Option daily fair values are not modeled. Excluded assets are not zero-risk assets. Percentages use full portfolio NAV, not renormalized modeled capital. Current holdings replay historical total-return and synchronized FX shocks; historical operational returns are not inputs.",
    }


def read_portfolio_tail_risk(portfolio_id: str, *, as_of_date: date | None = None,
                            confidence=DEFAULT_CONFIDENCE, lookback_days=DEFAULT_LOOKBACK_DAYS,
                            workspace=None):
    from portfolio_app.api.routes.workspace import holdings_workspace
    from portfolio_app.services.instrument_registry import get_registry_instrument_details, get_shared_fx_rates
    if workspace is None:
        workspace = holdings_workspace(portfolio_id=portfolio_id, as_of_date=as_of_date, include_details=True)
    if workspace.get("portfolio_id") != portfolio_id:
        raise ValueError("Tail-risk workspace belongs to another portfolio.")
    if as_of_date is not None and _date(workspace["as_of_date"]) != as_of_date:
        raise ValueError("Tail-risk workspace date differs from the requested date.")
    ids = {(row.get("instrument_core") or {}).get("instrument_id") for row in workspace.get("rows") or []}
    base = workspace.get("base_currency")
    has_fx = any((row.get("instrument_core") or {}).get("currency") not in {None, base}
                 for row in workspace.get("rows") or [])
    fx_payload = get_shared_fx_rates() if has_fx else {}
    ids.update(fx_direct_instrument_map(fx_payload).values())
    details = get_registry_instrument_details([iid for iid in ids if iid])
    return project_portfolio_tail_risk(workspace, confidence=confidence, lookback_days=lookback_days,
                                       instrument_details=details, fx_payload=fx_payload)
