"""Gross declared-cash reinvestment, independent of Futu's additive rehab factors.

P_t is the raw regular-session close. An ordinary cash dividend D gives
G_t=(P_t+D)/P_(t-1); a verified split q_new/q_old gives G_t=q*P_t/P_(t-1).
A segment starts at its first raw close and compounds G. Missing observations or
uninterpretable events cut a link; they never become a zero distribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import json
import hashlib
import re

import exchange_calendars

from studio_market.calendars import confirmed_market_closures

METHOD = "futu_declared_cash_reinvestment_v1"
PROVIDER = "futu:declared-cash-reinvestment:v1"
ACTION_SOURCE = "futu:corporate-actions:rehab"


@dataclass(frozen=True)
class FutuReturns:
    rows: list[dict]
    metadata: dict


def _decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("invalid_numeric_action")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_numeric_action") from None
    if not result.is_finite():
        raise ValueError("invalid_numeric_action")
    return result


def _event_terms(event: dict, currency: str) -> tuple[Decimal, Decimal]:
    """Return cash per old share and new shares per old share, or a reason.

    Only separately documented ordinary cash and simple split/join events are
    accepted. Simultaneous actions need an order and cash share-basis contract.
    """
    types = event.get("action_types")
    if types not in (["DIVIDEND"], ["SPLIT"], ["JOIN"]):
        raise ValueError("unsupported_or_combined_corporate_action")
    fields = ("per_share_div_ratio", "per_share_trans_ratio", "allotment_ratio",
              "stk_spo_ratio", "spin_off_ratio", "special_dividend", "bonus_ratio",
              "transfer_ratio", "allotment_price", "bonus_ert", "transfer_ert",
              "allot_ert", "add_ert", "spin_off_ert")
    if any(event.get(key) is not None and _decimal(event[key]) != 0 for key in fields):
        raise ValueError("unclassified_corporate_action_component")
    if types == ["DIVIDEND"]:
        cash = _decimal(event.get("per_cash_div", event.get("dividend")))
        if cash <= 0:
            raise ValueError("invalid_cash_dividend")
        if event.get("dividend") is not None and _decimal(event["dividend"]) != cash:
            raise ValueError("conflicting_cash_dividend_fields")
        # Production rehab has no structured currency. Require its complete
        # provider statement to confirm both the contemporaneous share base and
        # original currency. Unknown wording remains explicitly unavailable.
        description = str(event.get("desc_sc") or "").strip()
        match = re.fullmatch(r"1股派股息([0-9]+(?:\.[0-9]+)?)([A-Z]{3})", description)
        if not match:
            raise ValueError("unverified_dividend_currency_or_share_basis")
        if _decimal(match[1]) != cash:
            raise ValueError("dividend_amount_conflicts_with_statement")
        if event.get("currency") is not None and event["currency"] != match[2]:
            raise ValueError("dividend_currency_conflicts_with_statement")
        if match[2] != currency:
            raise ValueError("dividend_currency_requires_verified_fx")
        if event.get("split_ratio") is not None and _decimal(event["split_ratio"]) not in (0, 1):
            raise ValueError("unclassified_corporate_action_component")
        if any(event.get(key) is not None and _decimal(event[key]) != 0
               for key in ("split_base", "split_ert", "join_base", "join_ert")):
            raise ValueError("unclassified_corporate_action_component")
        return cash, Decimal(1)
    if any(event.get(key) is not None and _decimal(event[key]) != 0 for key in ("per_cash_div", "dividend")):
        raise ValueError("combined_split_and_cash_requires_share_basis")
    prefix = "split" if types == ["SPLIT"] else "join"
    old, new = _decimal(event.get(f"{prefix}_base")), _decimal(event.get(f"{prefix}_ert"))
    ratio = _decimal(event.get("split_ratio"))
    if old <= 0 or new <= 0 or ratio <= 0:
        raise ValueError("invalid_split_share_basis")
    # Provider factors are rounded decimals. Check direct share-count ratio to
    # its declared 1e-9 precision, rather than applying any A/B price factor.
    if abs(ratio - old / new) > Decimal("0.000000001"):
        raise ValueError("split_ratio_conflicts_with_share_basis")
    if (types == ["SPLIT"] and new <= old) or (types == ["JOIN"] and new >= old):
        raise ValueError("split_type_conflicts_with_share_basis")
    return Decimal(0), new / old


def derive_returns(rows: list[dict], snapshot: dict | None, *, currency: str,
                   calendar_name: str) -> FutuReturns:
    """Rebuild every retained date, including explicit withdrawal observations."""
    ordered = sorted(rows, key=lambda row: str(row["date"]))
    evidence = {"corporate_actions_source_id": (snapshot or {}).get("source_id"),
                "corporate_actions_batch_id": (snapshot or {}).get("batch_id"),
                "corporate_actions_raw_ref": (snapshot or {}).get("raw_ref"),
                "coverage_basis": (snapshot or {}).get("coverage_basis", "unavailable"),
                "coverage_status": (snapshot or {}).get("coverage_status", "unavailable"),
                "coverage_start": (snapshot or {}).get("coverage_start"),
                "coverage_end": (snapshot or {}).get("coverage_end"),
                "corporate_actions_observed_at": (snapshot or {}).get("observed_at"),
                "raw_price_inputs_sha256": hashlib.sha256(json.dumps(
                    [[str(row["date"]), row.get("source_id"), str(row["close"])] for row in ordered],
                    separators=(",", ":")).encode()).hexdigest(),
                "cash_basis": "provider_declared_cash_per_contemporaneous_share",
                "currency_validation": "explicit_provider_statement_matches_listing_currency",
                "raw_price_dataset": "futu_raw_eod_daily",
                "derived_dataset": "futu_return_daily"}
    metadata = {"total_return_method": METHOD, "total_return_tax_basis": "gross",
                "total_return_tax_treatment": "no_investor_tax_deduction",
                "total_return_reinvestment": "ex_date_close", "total_return_evidence": evidence,
                "total_return_segments": [], "total_return_unavailable_intervals": []}
    if not ordered:
        return FutuReturns([], metadata)
    unknown = "missing_corporate_action_evidence"
    events = []
    if snapshot:
        unknown = str(snapshot.get("coverage_status") or "missing_corporate_action_coverage")
        if unknown in {"provider_reported", "covered_no_actions"}:
            try:
                events = json.loads(snapshot["events_json"])
                if not isinstance(events, list) or (unknown == "covered_no_actions" and events):
                    raise ValueError
                if snapshot.get("event_count") != len(events):
                    raise ValueError
                if not events and snapshot.get("stock_type") != "STOCK":
                    raise ValueError
                coverage_start = date.fromisoformat(snapshot["coverage_start"])
                coverage_end = date.fromisoformat(snapshot["coverage_end"])
                if coverage_start > coverage_end:
                    raise ValueError
                # Shape and ex-date must remain valid even for unsupported types:
                # without a date, the affected interval cannot be bounded.
                for event in events:
                    if not isinstance(event, dict):
                        raise ValueError
                    date.fromisoformat(event["ex_div_date"])
                unknown = ""
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                unknown = "invalid_corporate_action_snapshot"
    by_day: dict[str, list[dict]] = {}
    for event in events if not unknown else []:
        by_day.setdefault(event["ex_div_date"], []).append(event)
    first, last = str(ordered[0]["date"]), str(ordered[-1]["date"])
    # Explicit bounds permit old listings outside the library's default window.
    calendar = exchange_calendars.get_calendar(calendar_name, start=str(date.fromisoformat(first) - timedelta(days=7)),
                                               end=str(date.fromisoformat(last) + timedelta(days=7)))
    closures = confirmed_market_closures(calendar_name)
    sessions = [stamp.date().isoformat() for stamp in calendar.sessions_in_range(first, last)
                if stamp.date() not in closures]
    positions = {day: index for index, day in enumerate(sessions)}
    derived, previous, segment, units = [], None, None, None
    intervals = metadata["total_return_unavailable_intervals"]
    with localcontext() as context:
        context.prec = 34
        for raw in ordered:
            day = str(raw["date"])
            current_date = date.fromisoformat(day)
            close = _decimal(raw["close"])
            if close <= 0:
                raise ValueError("invalid_raw_close")
            unavailable = unknown
            if not unavailable and not coverage_start <= current_date <= coverage_end:
                unavailable = "outside_corporate_action_coverage"
            if day not in positions:
                unavailable = "raw_price_outside_market_session"
            item = {"symbol": raw["symbol"], "stock_id": raw["stock_id"], "date": day,
                    "currency": currency, "close": str(close), "adjusted_close": None,
                    "adjustment_factor": None, "segment_id": None, "return_status": "unavailable",
                    "unavailable_reason": unavailable or None, "gross_return": None,
                    "method": METHOD, "raw_source_id": raw.get("source_id"),
                    "raw_batch_id": raw.get("batch_id"), "raw_price_raw_ref": raw.get("raw_ref"),
                    "previous_raw_source_id": previous.get("source_id") if previous else None,
                    "corporate_actions_source_id": (snapshot or {}).get("source_id"),
                    "corporate_actions_batch_id": (snapshot or {}).get("batch_id"),
                    "corporate_actions_raw_ref": (snapshot or {}).get("raw_ref")}
            if unavailable:
                intervals.append({"start_date": day, "end_date": day, "reason": unavailable})
                segment, units, previous = None, None, None
                derived.append(item)
                continue
            boundary = None
            cash, shares = Decimal(0), Decimal(1)
            if previous is not None:
                prior_day = str(previous["date"])
                between = [event for event_day, values in by_day.items()
                           if prior_day < event_day <= day for event in values]
                if positions[day] != positions[prior_day] + 1:
                    boundary = "missing_market_session_close"
                elif any(event["ex_div_date"] != day for event in between):
                    boundary = "missing_ex_date_close"
                elif len(between) > 1:
                    boundary = "multiple_actions_require_verified_order"
                elif between:
                    try:
                        cash, shares = _event_terms(between[0], currency)
                    except ValueError as error:
                        boundary = str(error)
                if boundary:
                    intervals.append({"start_date": prior_day, "end_date": day, "reason": boundary})
            if previous is None or boundary:
                segment = {"start_date": day, "end_date": day}
                metadata["total_return_segments"].append(segment)
                units = Decimal(1)
                item["return_status"] = "segment_anchor"
            else:
                gross = (close * shares + cash) / _decimal(previous["close"])
                units *= shares + cash / close
                segment["end_date"] = day
                item["gross_return"] = str(gross)
                item["return_status"] = "available"
            item.update(adjusted_close=str(close * units), adjustment_factor=str(units),
                        segment_id=segment["start_date"], unavailable_reason=boundary)
            derived.append(item)
            previous = raw
    # Unknown full history should remain compact in instrument metadata.
    if unknown:
        metadata["total_return_unavailable_intervals"] = [
            {"start_date": first, "end_date": last, "reason": unknown}]
    return FutuReturns(derived, metadata)
