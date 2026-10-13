"""Retain Wind's unadjusted index observations and publish verified closes."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
from zoneinfo import ZoneInfo

from sqlalchemy import select

from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core.db_models import Instrument, InstrumentPriceBar
from studio_market.calendars import closed_market_sessions, latest_closed_market_session
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.raw import archive_response
from studio_market.wind import WindError, query
from studio_data.core.settings import get_settings
from studio_data.db.session import get_session_factory
from studio_data.services.instrument_store import get_instrument, get_price_bars, update_refresh_status


SYMBOL = "881001.WI"
PROVIDER = "wind:index-kline:unadjusted"
CLOSE_POLICY = {role: ["close"]
                for role in ("trading", "valuation", "total_return", "chart", "reference")}
SHANGHAI = ZoneInfo("Asia/Shanghai")


class WindIndexError(ValueError):
    """Safe diagnosis of an unsupported identity, response or coverage gap."""


def _configuration(instrument: dict) -> date:
    source = dict(instrument.get("source_settings") or {})
    if source.get("source_mode") != "api" or source.get("source_api_profile") != "wind":
        raise WindIndexError("Wind index refresh requires the explicitly configured api/wind source.")
    if (instrument.get("instrument_type") != "index" or instrument.get("currency") != "CNY"
            or instrument.get("exchange_code") not in (None, "", "XSHG")):
        raise WindIndexError("Wind index refresh currently supports the CNY 881001.WI index only.")
    tickers = {str(item.get("identifier_value")) for item in instrument.get("identifiers", [])
               if item.get("identifier_type") in {"ticker", "exchange_ticker"}}
    if tickers != {SYMBOL}:
        raise WindIndexError("Wind index refresh requires the exact registered ticker 881001.WI.")
    if dict(instrument.get("lifecycle_state") or {}).get("status", "active") != "active":
        raise WindIndexError("Wind index refresh requires an active registered instrument.")
    if source.get("market_calendar") != "XSHG" or source.get("return_semantics") != "price_return":
        raise WindIndexError("Wind unadjusted index history requires XSHG and explicit price_return semantics.")
    try:
        multiplier = Decimal(str(source.get("source_price_multiplier", 1)))
        start = date.fromisoformat(str(source["history_start_date"]))
    except (KeyError, ValueError, InvalidOperation) as error:
        raise WindIndexError("Wind index history requires an explicit ISO history_start_date and unit multiplier.") from error
    if not multiplier.is_finite() or multiplier != 1:
        raise WindIndexError("Wind index points must not be rescaled; source_price_multiplier must be 1.")
    return start


def _owned_history(instrument: dict, bars: list[dict], *, full_history: bool, sessions: set[date]) -> set[date]:
    """Explicit full history may replace old closes, never unrelated quote bases."""
    dates = set()
    for row in instrument.get("market_data", []):
        day = date.fromisoformat(str(row["as_of_date"]))
        if row.get("quote_basis") != "close":
            raise WindIndexError("Existing non-close prices need a separate reviewed source migration.")
        if row.get("provider") != PROVIDER and (not full_history or day not in sessions):
            raise WindIndexError("Existing non-Wind closes require explicit full_history covering their dates.")
        if row.get("provider") == PROVIDER and row.get("status") == "complete":
            dates.add(day)
    for row in bars:
        day = date.fromisoformat(str(row.get("date") or row.get("as_of_date")))
        if row.get("adjustment_factor") is not None:
            raise WindIndexError("Existing adjusted OHLC needs a separate reviewed source migration.")
        if row.get("provider") != PROVIDER and (not full_history or day not in sessions):
            raise WindIndexError("Existing non-Wind OHLC requires explicit full_history covering its dates.")
    return dates


def _gap_error(missing: set[date], *, retained: bool = False) -> WindIndexError:
    days = sorted(missing)
    sample = ", ".join(day.isoformat() for day in days[:12])
    suffix = f" … through {days[-1]}" if len(days) > 12 else ""
    remedy = " Request explicit full_history to repair retained gaps." if retained else " No canonical prices were published."
    return WindIndexError(f"Wind index history is missing {len(days)} closed XSHG session(s): {sample}{suffix}.{remedy}")


def _table(response: dict) -> dict:
    tables = []
    if not isinstance(response, dict) or response.get("isError"):
        raise WindIndexError("Wind returned no usable index response.")
    for block in response.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(block["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            raise WindIndexError("Wind returned an index data error; no canonical prices were published.")
        data = payload.get("data")
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            tables.append(data)
    if len(tables) != 1:
        raise WindIndexError("Wind index response must contain exactly one column/row data table.")
    return tables[0]


def _number(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise WindIndexError(f"Wind index {field} is unavailable or invalid; missing values are not zero.") from error
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise WindIndexError(f"Wind index {field} must be a finite {'non-negative' if allow_zero else 'positive'} value.")
    return result


def _rows(response: dict, sessions: set[date]) -> tuple[list[dict], list[dict], list[dict]]:
    data = _table(response)
    columns = data["columns"]
    if not isinstance(columns, list) or any(not isinstance(col, dict) or not isinstance(col.get("name"), str) for col in columns):
        raise WindIndexError("Wind index column definitions are invalid.")
    names = [col["name"] for col in columns]
    if len(names) != len(set(names)) or not {"TIME", "OPEN", "MATCH", "HIGH", "LOW"}.issubset(names):
        raise WindIndexError("Wind index response lacks unique TIME and OHLC columns.")
    if not isinstance(data["rows"], list):
        raise WindIndexError("Wind index rows must be a list.")
    units = data.get("unit") or {}
    turnover_yuan = isinstance(units, dict) and units.get("TURNOVER 单位：") == "元"
    points, bars, observations = [], [], []
    previous = None
    for values in data["rows"]:
        if not isinstance(values, list) or len(values) != len(names):
            raise WindIndexError("Wind index row width differs from its columns.")
        row = dict(zip(names, values))
        try:
            clock = datetime.fromisoformat(row["TIME"])
        except (TypeError, ValueError) as error:
            raise WindIndexError("Wind index TIME must be an explicit Shanghai daily timestamp.") from error
        local = clock.astimezone(SHANGHAI) if clock.tzinfo else None
        # TIME identifies the period=1d session, not a publication instant.
        # Real Wind history mixes midnight +08:00 with midnight +00:00.
        # Both preserve the same Shanghai date; reject other or crossing clocks.
        if (local is None or clock.time() != time(0)
                or clock.utcoffset() not in {timedelta(0), timedelta(hours=8)}
                or local.date() != clock.date()):
            raise WindIndexError("Wind index TIME must be a midnight UTC/+08 daily label preserving the Shanghai session date.")
        day = local.date()
        if day not in sessions:
            raise WindIndexError(f"Wind index date {day} is outside the requested closed XSHG sessions.")
        if previous is not None and day <= previous:
            raise WindIndexError("Wind index dates must be strictly increasing, without duplicate or conflicting rows.")
        previous = day
        prices = {field: _number(row[column], column) for field, column in
                  (("open", "OPEN"), ("high", "HIGH"), ("low", "LOW"), ("close", "MATCH"))}
        if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
            raise WindIndexError("Wind index prices violate OHLC high/low ordering.")
        # The provider establishes TURNOVER in yuan, but not VOLUME's unit.
        # Keep the latter in raw evidence only; never infer shares or contracts.
        turnover = (_number(row["TURNOVER"], "TURNOVER", allow_zero=True)
                    if turnover_yuan and row.get("TURNOVER") not in (None, "", "INVALID") else None)
        points.append(dict(metric_family="price", quote_basis="close", as_of_date=day,
                           value=prices["close"], currency="CNY", provider=PROVIDER, status="complete"))
        bars.append(dict(as_of_date=day, **prices, volume=None, volume_unit=None, turnover=turnover,
                         turnover_unit="CNY" if turnover is not None else None, adjustment_factor=None,
                         currency="CNY", provider=PROVIDER, status="complete"))
        observations.append(dict(series_id=SYMBOL, date=day.isoformat(), value=str(prices["close"]),
            **{key: str(value) for key, value in prices.items()}, currency="CNY", value_unit="index_points",
            return_semantics="price_return", ohlc_adjustment="unadjusted", provider_fields=row,
            provider_units=units or None, date_semantics="provider_daily_session_label",
            limitations=["TIME is a daily session label, not a disclosure clock; Wind can mix midnight UTC and +08 labels for the same Shanghai date.",
                "VOLUME unit is not established; no canonical volume was inferred.",
                "INVALID and empty values are unavailable, not zero; reported totals do not establish completeness."]))
    missing = sessions - {point["as_of_date"] for point in points}
    if missing:
        raise _gap_error(missing)
    return points, bars, observations


def _publish(instrument: dict, points: list[dict], bars: list[dict], *, full_history: bool, sessions: set[date]) -> tuple[int, int]:
    instrument_id = str(instrument["instrument_id"])
    with get_session_factory()() as session:
        target = session.scalar(select(Instrument).where(Instrument.instrument_id == instrument_id).with_for_update())
        if target is None:
            raise WindIndexError("Registered Wind index disappeared during acquisition.")
        current = shared_store._instrument_to_store_dict(target)
        if (_configuration(current) != _configuration(instrument)
                or shared_store._normalized_source_settings(current) != shared_store._normalized_source_settings(instrument)):
            raise WindIndexError("Instrument source settings changed during Wind acquisition; no canonical prices were published.")
        existing_bars = [dict(provider=row.provider, adjustment_factor=row.adjustment_factor, as_of_date=row.as_of_date)
                         for row in session.scalars(select(InstrumentPriceBar).where(InstrumentPriceBar.instrument_id == instrument_id))]
        _owned_history(current, existing_bars, full_history=full_history, sessions=sessions)
        if target.quote_selection_policy_json != CLOSE_POLICY:
            target.quote_selection_policy_json = {role: list(bases) for role, bases in CLOSE_POLICY.items()}
            target.calculation_inputs_updated_at = shared_store._next_calculation_input_watermark(session)
        point_count = shared_store._upsert_market_data_points(session, instrument_id=instrument_id, rows=points)
        bar_count = shared_store._upsert_price_bars(session, instrument_id=instrument_id, rows=bars)
        session.commit()
    return int(point_count or 0), int(bar_count or 0)


def refresh_wind_index(instrument_id: str, *, full_history: bool = False, now: datetime | None = None, store=None) -> dict:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise WindIndexError("Registered Wind index not found.")
    owned, market = store is None, store
    try:
        start = _configuration(instrument)
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise WindIndexError("Wind index refresh requires a timezone-aware acquisition cutoff.")
        end = latest_closed_market_session("XSHG", now)
        expected = closed_market_sessions("XSHG", start, end, now)
        if not expected:
            raise WindIndexError("Wind index history_start_date has no verified closed XSHG sessions through the cutoff.")
        sessions = set(expected)
        existing = _owned_history(instrument, get_price_bars(instrument_id=instrument_id),
                                  full_history=full_history, sessions=sessions)
        if existing and not full_history:
            missing = {day for day in sessions if day <= max(existing)} - existing
            if missing:
                raise _gap_error(missing, retained=True)
        fetch_start = max(start, max(existing)) if existing and not full_history else start
        requested = {day for day in sessions if day >= fetch_start}
        if not requested:
            raise WindIndexError("Existing Wind history is later than the verified acquisition cutoff.")
        credential = get_settings().wind_api_key
        if credential is None or not credential.get_secret_value().strip():
            raise WindIndexError("Configure the data service's Wind authorization before refreshing index history.")
        if market is None:
            market = NumericStore(MarketSettings.from_environment())
        arguments = dict(windcode=SYMBOL, begin_date=fetch_start.isoformat(), end_date=end.isoformat(),
                         period="1d", aftype="2", count=0)
        response = query("index_data", "get_index_kline", arguments, key=credential.get_secret_value())
        observed_at = datetime.now(UTC)
        # This is the exact parsed MCP response, not a generated price series.
        digest, raw_ref = archive_response(market.settings, "wind", json.dumps(
            response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
        points, bars, observations = _rows(response, requested)
        market.ingest("market_series_daily", [observations], source=PROVIDER, observed_at=observed_at, raw_ref=raw_ref,
                      details=dict(tool="get_index_kline", domain="index_data", arguments=arguments,
                                   raw_sha256=digest, request_cutoff=now.isoformat(),
                                   return_semantics="price_return", calendar="XSHG"))
        changes = _publish(instrument, points, bars, full_history=full_history, sessions=sessions)
        record = update_refresh_status(instrument_id=instrument_id,
            status="refreshed" if any(changes) else "no_new_data", updated_by="wind_index_sync", mode="api",
            message=f"Verified {len(points)} Wind unadjusted daily closes and OHLC bars for {SYMBOL}, "
                    f"{min(requested)} through {end}; retained coverage starts {expected[0]}. "
                    "Price-return index points only; no total-return series or inferred volume.")
        if record is None:
            raise WindIndexError("Registered Wind index disappeared during publication.")
        return record
    except Exception as error:
        failure = error if isinstance(error, (WindIndexError, WindError)) else WindIndexError(
            f"Wind index refresh could not validate or publish the response ({type(error).__name__}); no automatic retry.")
        update_refresh_status(instrument_id=instrument_id, status="blocked", message=str(failure),
                              updated_by="wind_index_sync", mode="api")
        if failure is error:
            raise
        raise failure from error
    finally:
        if owned and market is not None:
            market.close()
