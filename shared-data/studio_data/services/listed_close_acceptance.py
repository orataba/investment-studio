"""Read-only acceptance of a scheduled batch's completed-session responsibility."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import func, or_, select

from investment_studio_instrument_core.db_models import (
    Instrument, InstrumentIdentifier, InstrumentMarketData,
)
from studio_market.calendars import closed_market_sessions, latest_closed_market_session
from studio_market.numeric.hkex_sessions import matching_no_trade_evidence
from studio_market.numeric.schema import batches, current
from studio_data.db.session import get_session_factory


ACCEPTANCE_STATUSES = frozenset({"imported", "refreshed", "no_new_data", "partial"})


def _read_listed_close_inputs(
    instrument_ids: list[str] | None, *, baseline: dict[str, str | None] | None = None,
) -> dict[str, dict]:
    """Read bounded registry facts and the latest complete raw quote per target."""
    with get_session_factory()() as session:
        identities = select(
            Instrument.instrument_id, Instrument.instrument_type, Instrument.currency,
            Instrument.exchange_code, Instrument.source_settings_json.label("source_settings"),
            Instrument.lifecycle_state_json.label("lifecycle_state"),
        )
        if instrument_ids is None:
            identities = identities.where(
                Instrument.instrument_type.in_(("equity", "etf")),
                Instrument.source_settings_json["source_mode"].as_string() == "api",
                func.coalesce(Instrument.lifecycle_state_json["status"].as_string(), "active") == "active",
            )
        else:
            identities = identities.where(Instrument.instrument_id.in_(instrument_ids))
        records = {row.instrument_id: dict(row) for row in session.execute(identities).mappings()}
        instrument_ids = list(records)
        if not instrument_ids:
            return records
        quotes = select(
            InstrumentMarketData.instrument_id, InstrumentMarketData.as_of_date,
            InstrumentMarketData.value, InstrumentMarketData.currency,
            InstrumentMarketData.provider, InstrumentMarketData.price_unit,
            InstrumentMarketData.price_scale,
        ).where(
            InstrumentMarketData.instrument_id.in_(instrument_ids),
            InstrumentMarketData.metric_family == "price", InstrumentMarketData.quote_basis == "close",
            InstrumentMarketData.status == "complete",
        )
        ranked = quotes.add_columns(func.row_number().over(
            partition_by=InstrumentMarketData.instrument_id,
            order_by=(InstrumentMarketData.as_of_date.desc(),
                      InstrumentMarketData.instrument_market_data_id.desc()),
        ).label("position")).subquery()
        for row in session.execute(select(ranked).where(ranked.c.position == 1)).mappings():
            records[row.instrument_id]["close"] = dict(row)
        if baseline:
            windows = [(InstrumentMarketData.instrument_id == iid)
                       & (InstrumentMarketData.as_of_date >= date.fromisoformat(day))
                       for iid, day in baseline.items() if iid in records and day is not None]
            if windows:
                for row in session.execute(quotes.where(or_(*windows))).mappings():
                    records[row.instrument_id].setdefault("window_closes", []).append(dict(row))
        for row in session.execute(select(
            InstrumentIdentifier.instrument_id, InstrumentIdentifier.identifier_value,
        ).where(InstrumentIdentifier.instrument_id.in_(instrument_ids),
                InstrumentIdentifier.identifier_type == "exchange_ticker")):
            if row.instrument_id in records:
                records[row.instrument_id].setdefault("exchange_tickers", []).append(row.identifier_value)
    return records


def listed_close_baseline(instrument_ids: list[str] | None = None) -> dict[str, str | None]:
    """Freeze each existing raw-price endpoint before any collection or retry."""
    return {iid: str(record["close"]["as_of_date"]) if record.get("close") else None
            for iid, record in _read_listed_close_inputs(instrument_ids).items()
            if record["instrument_type"] in {"equity", "etf"}
            and (record.get("source_settings") or {}).get("source_mode") == "api"
            and (record.get("lifecycle_state") or {}).get("status", "active") == "active"}


def _read_hkex_evidence(symbol: str, after: date, through: date) -> list[dict]:
    # Use the existing current projection in the same application database.
    # Acceptance never fetches missing evidence or initializes a NumericStore.
    with get_session_factory()() as session:
        return list(session.scalars(select(current.c.payload).join(
            batches, batches.c.id == current.c.batch_id,
        ).where(
            current.c.dataset == "hkex_security_sessions", current.c.symbol == symbol,
            batches.c.status == "ready",
            current.c.payload["date"].as_string() > after.isoformat(),
            current.c.payload["date"].as_string() <= through.isoformat(),
        )))


def _provider_matches(profile: str, provider: object) -> bool:
    if profile == "fmp":
        return provider == "fmp:historical-price-eod:non-split-adjusted"
    if profile == "futu":
        return provider == "futu:history-kline:unadjusted"
    if profile in {"tushare", "tushare_pro", "tushare-pro"}:
        return provider in {"datahub:tushare:daily", "datahub:tushare:fund_daily"}
    return False


def _valid_raw_quote(quote: dict, currency: str, profile: str) -> bool:
    try:
        close = Decimal(str(quote["value"]))
        scale = Decimal(str(quote["price_scale"]))
        date.fromisoformat(str(quote["as_of_date"]))
    except (KeyError, ValueError, InvalidOperation):
        return False
    return (close.is_finite() and close > 0 and currency and quote.get("currency") == currency
            and quote.get("price_unit") == "per_unit" and scale.is_finite() and scale == 1
            and _provider_matches(profile, quote.get("provider")))


def _check_close(result: dict, instrument: dict | None, cutoff: datetime, baseline_date: str | None) -> dict:
    checked = {"instrument_id": result["instrument_id"], "status": "unavailable",
               "baseline_close_date": baseline_date,
               "expected_close_date": None, "latest_close_date": None}
    if instrument is None:
        return {**checked, "reason": "instrument_missing"}
    if (instrument.get("lifecycle_state") or {}).get("status", "active") != "active":
        return {**checked, "status": "not_required", "reason": "inactive_instrument"}
    source = instrument.get("source_settings") or {}
    profile = str(source.get("source_api_profile") or "").lower()
    if (instrument.get("instrument_type") not in {"equity", "etf"}
            or source.get("source_mode") != "api"
            or profile != str(result.get("source_api_profile") or "").lower()):
        return {**checked, "reason": "source_identity_changed"}
    calendar = str(instrument.get("exchange_code") or "")
    try:
        expected = latest_closed_market_session(calendar, cutoff)
    except ValueError:
        return {**checked, "reason": "unverified_market_calendar"}
    checked["expected_close_date"] = expected.isoformat()
    quote = instrument.get("close")
    if not quote:
        return {**checked, "reason": "complete_raw_close_missing"}
    currency = instrument.get("currency")
    quotes = instrument.get("window_closes", []) if baseline_date else [quote]
    if not _valid_raw_quote(quote, currency, profile) or any(
        not _valid_raw_quote(point, currency, profile) for point in quotes
    ):
        return {**checked, "reason": "raw_close_contract_mismatch"}
    latest = date.fromisoformat(str(quote["as_of_date"]))
    checked["latest_close_date"] = latest.isoformat()
    window_start = date.fromisoformat(baseline_date) if baseline_date else latest
    if window_start > expected:
        return {**checked, "reason": "baseline_after_batch_cutoff" if baseline_date else "close_after_batch_cutoff"}
    sessions = closed_market_sessions(calendar, window_start + timedelta(days=1), expected, cutoff)
    if sessions is None:
        return {**checked, "reason": "unverified_market_calendar"}
    by_date = {date.fromisoformat(str(point["as_of_date"])): point for point in quotes}
    if baseline_date and window_start not in by_date:
        return {**checked, "reason": "baseline_raw_close_missing"}
    missing = [day for day in sessions if day not in by_date]
    if not missing:
        return {**checked, "status": "complete", "reason": None}
    checked["missing_session_dates"] = [day.isoformat() for day in missing]
    if calendar == "XHKG":
        tickers = instrument.get("exchange_tickers", [])
        match = re.fullmatch(r"([0-9]{1,5})\.HK", str(tickers[0])) if len(tickers) == 1 else None
        if match:
            # HKEX's close is in the source quote unit. The canonical close
            # has already applied this declared multiplier; never compare a
            # converted value against the exchange's unconverted evidence.
            try:
                multiplier = Decimal(str(source.get("source_price_multiplier", 1)))
            except InvalidOperation:
                multiplier = Decimal("NaN")
            if not multiplier.is_finite() or multiplier <= 0:
                return {**checked, "reason": "invalid_source_price_multiplier"}
            if source.get("source_provider_currency", currency) != currency:
                return {**checked, "reason": "no_trade_currency_unverified"}
            symbol = f"{int(match[1]):04d}.HK"
            evidence_rows = _read_hkex_evidence(symbol, window_start, expected)
            evidence = []
            for day in missing:
                previous_dates = [observed for observed in by_date if observed < day]
                if not previous_dates:
                    break
                previous_close = Decimal(str(by_date[max(previous_dates)]["value"])) / multiplier
                matched = matching_no_trade_evidence(
                    [row for row in evidence_rows if str(row.get("date")) == day.isoformat()],
                    symbol=symbol, currency=currency, last_close=previous_close,
                )
                if len(matched) != 1:
                    break
                evidence.extend(matched)
            if len(evidence) == len(missing):
                return {**checked, "status": "complete", "reason": "confirmed_no_trade",
                        "evidence_source_ids": [str(row["source_id"]) for row in evidence]}
    return {**checked, "reason": "closed_session_price_missing"}


def verify_listed_close_coverage(
    results: list[dict], *, cutoff: datetime, baseline: dict[str, str | None],
) -> dict:
    """Validate only this run's non-failed API listings at its frozen start clock.

    Item refresh statuses remain source receipts. This independent acceptance
    cannot repair, retry, or mark an older quote as a new observation.
    """
    latest_results = {str(row.get("instrument_id") or ""): row for row in results}
    targets = [row for iid, row in latest_results.items() if iid
               and row.get("instrument_type") in {"equity", "etf"}
               and row.get("source_mode") == "api" and row.get("status") in ACCEPTANCE_STATUSES]
    checked = []
    if targets:
        try:
            records = _read_listed_close_inputs([row["instrument_id"] for row in targets], baseline=baseline)
        except Exception as error:
            records = None
            checked = [{"instrument_id": row["instrument_id"], "status": "unavailable",
                        "reason": "verification_read_failed", "error_type": type(error).__name__}
                       for row in targets]
        if records is not None:
            for result in targets:
                try:
                    checked.append(_check_close(result, records.get(result["instrument_id"]), cutoff,
                                                baseline.get(result["instrument_id"])))
                except Exception as error:
                    checked.append({"instrument_id": result["instrument_id"], "status": "unavailable",
                                    "reason": "verification_failed", "error_type": type(error).__name__})
    failed = sum(row["status"] == "unavailable" for row in checked)
    return {"status": "failed" if failed else "passed" if targets else "not_required",
            "cutoff": cutoff.isoformat(), "checked_count": len(checked), "failed_count": failed,
            "results": checked}
