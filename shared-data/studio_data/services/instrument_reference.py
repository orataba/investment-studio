from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from studio_data.core.settings import get_settings
from studio_data.services import datahub_client
from studio_data.services.fmp import FmpApiError, FmpClient
from studio_data.services.instrument_store import get_instrument, list_instruments
from studio_data.contracts import StudioInstrumentReferenceData
from investment_studio_instrument_core.db_models import InstrumentReferenceSnapshot
from studio_data.db.session import get_session_factory


REFERENCE_INSTRUMENT_TYPES = {
    "public_fund",
    "private_fund",
    "etf",
    "equity",
    "index",
}


def _identifier(
    instrument: dict[str, object],
    *,
    identifier_type: str | None = None,
    prefix: str | None = None,
) -> str | None:
    for item in list(instrument.get("identifiers") or []):
        if not isinstance(item, dict):
            continue
        if identifier_type and item.get("identifier_type") != identifier_type:
            continue
        value = str(item.get("identifier_value") or "").strip()
        if prefix and not value.startswith(prefix):
            continue
        if value:
            return value.removeprefix(prefix or "")
    return None


def _tushare_code(instrument: dict[str, object]) -> str | None:
    provider_code = _identifier(instrument, identifier_type="provider_symbol", prefix="tushare:")
    if provider_code:
        return provider_code
    for identifier_type in ("exchange_ticker", "ticker", "fund_code"):
        value = _identifier(instrument, identifier_type=identifier_type)
        if value and "." in value:
            return value.upper()
    return None


def _source_context(instrument: dict[str, object]) -> dict[str, object]:
    settings = dict(instrument.get("source_settings") or {})
    refresh = dict(instrument.get("refresh_status") or {})
    return {
        "source_mode": settings.get("source_mode"),
        "source_location": settings.get("source_location"),
        "source_api_profile": settings.get("source_api_profile"),
        "expected_frequency": settings.get("expected_frequency"),
        "market_calendar": settings.get("market_calendar"),
        "release_lag_days": settings.get("release_lag_days"),
        "refresh_status": refresh.get("status"),
        "refresh_message": refresh.get("message"),
        "market_data_updated_at": instrument.get("market_data_updated_at"),
    }


def _run_section(
    sections: dict[str, object],
    errors: dict[str, str],
    name: str,
    loader: Callable[[], object],
) -> None:
    try:
        sections[name] = loader()
    except (FmpApiError, datahub_client.DataHubClientError, ValueError) as error:
        errors[name] = str(error)


def _datahub_rows(
    *,
    api_name: str,
    params: dict[str, object],
    fields: str,
) -> list[dict[str, object]]:
    settings = get_settings()
    return datahub_client.fetch_tushare_rows(
        api_key=settings.datahub_api_key,
        api_url=settings.datahub_tushare_api_url,
        api_name=api_name,
        params=params,
        fields=fields,
        timeout_seconds=settings.datahub_timeout_seconds,
    )


def _latest_fund_holdings(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    latest_period = max(
        (str(row.get("end_date") or "") for row in rows),
        default="",
    )
    if not latest_period:
        return []
    selected = [row for row in rows if str(row.get("end_date") or "") == latest_period]
    return sorted(
        selected,
        key=lambda row: float(row.get("stk_mkv_ratio") or 0),
        reverse=True,
    )[:100]


def _tushare_reference(
    *,
    instrument_type: str,
    provider_symbol: str,
) -> tuple[dict[str, object], dict[str, str]]:
    sections: dict[str, object] = {}
    errors: dict[str, str] = {}
    today = date.today()
    if instrument_type == "equity":
        errors["reference"] = "Mainland equities use FMP, not Tushare."
        return sections, errors

    if instrument_type == "index":
        _run_section(
            sections,
            errors,
            "index_info",
            lambda: (_datahub_rows(
                api_name="index_basic",
                params={"ts_code": provider_symbol},
                fields=(
                    "ts_code,name,fullname,market,publisher,index_type,category,"
                    "base_date,base_point,list_date,weight_rule,desc,exp_date"
                ),
            ) or [{}])[0],
        )
        return sections, errors

    if instrument_type in {"etf", "public_fund"}:
        _run_section(
            sections,
            errors,
            "fund_info",
            lambda: (_datahub_rows(
                api_name="fund_basic",
                params={"ts_code": provider_symbol},
                fields=(
                    "ts_code,name,management,custodian,fund_type,found_date,due_date,"
                    "list_date,issue_date,delist_date,issue_amount,m_fee,c_fee,duration_year,"
                    "p_value,min_amount,exp_return,benchmark,status,invest_type,type,"
                    "trustee,purc_startdate,redm_startdate,market"
                ),
            ) or [{}])[0],
        )

        def load_holdings() -> list[dict[str, object]]:
            rows = _datahub_rows(
                api_name="fund_portfolio",
                params={
                    "ts_code": provider_symbol,
                    "start_date": (today - timedelta(days=900)).strftime("%Y%m%d"),
                    "end_date": today.strftime("%Y%m%d"),
                },
                fields=(
                    "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio"
                ),
            )
            return _latest_fund_holdings(rows)

        _run_section(sections, errors, "holdings", load_holdings)
        return sections, errors

    errors["reference"] = f"Tushare reference data is not defined for {instrument_type}."
    return sections, errors


def _fmp_reference(
    *,
    instrument_type: str,
    provider_symbol: str,
    client: FmpClient,
) -> tuple[dict[str, object], dict[str, str]]:
    sections: dict[str, object] = {}
    errors: dict[str, str] = {}
    if instrument_type == "equity":
        _run_section(sections, errors, "profile", lambda: client.profile(provider_symbol))
        _run_section(
            sections,
            errors,
            "financials",
            lambda: client.income_statements(provider_symbol, limit=5),
        )
        _run_section(
            sections,
            errors,
            "key_metrics",
            lambda: client.key_metrics(provider_symbol, limit=5),
        )
        _run_section(
            sections,
            errors,
            "ratios",
            lambda: client.financial_ratios(provider_symbol, limit=5),
        )
        _run_section(
            sections,
            errors,
            "dividends",
            lambda: client.dividends(provider_symbol, limit=20),
        )
        _run_section(
            sections,
            errors,
            "splits",
            lambda: client.splits(provider_symbol, limit=20),
        )
        return sections, errors

    if instrument_type in {"etf", "public_fund"}:
        _run_section(sections, errors, "fund_info", lambda: client.fund_info(provider_symbol))
        _run_section(
            sections,
            errors,
            "holdings",
            lambda: client.fund_holdings(provider_symbol)[:100],
        )
        _run_section(
            sections,
            errors,
            "sector_weights",
            lambda: client.fund_sector_weights(provider_symbol),
        )
        _run_section(
            sections,
            errors,
            "country_weights",
            lambda: client.fund_country_weights(provider_symbol),
        )
        return sections, errors

    if instrument_type == "index":
        _run_section(sections, errors, "index_info", lambda: client.index_info(provider_symbol))
        return sections, errors

    errors["reference"] = f"FMP reference data is not defined for {instrument_type}."
    return sections, errors


def get_instrument_reference_data(
    instrument_id: str,
    *,
    client: FmpClient | None = None,
) -> dict[str, object] | None:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        return None
    instrument_type = str(instrument.get("instrument_type") or "").strip().lower()
    if instrument_type not in REFERENCE_INSTRUMENT_TYPES:
        raise ValueError(f"Reference data is not supported for {instrument_type or 'unknown'} instruments.")

    source_settings = dict(instrument.get("source_settings") or {})
    source_profile = str(source_settings.get("source_api_profile") or "").strip().lower()
    provider = "manual"
    provider_symbol: str | None = None
    sections: dict[str, object] = {}
    errors: dict[str, str] = {}

    if source_profile in {"tushare", "tushare_pro", "tushare-pro"}:
        provider = "datahub:tushare"
        provider_symbol = _tushare_code(instrument)
        if provider_symbol:
            sections, errors = _tushare_reference(
                instrument_type=instrument_type,
                provider_symbol=provider_symbol,
            )
        else:
            errors["reference"] = "The instrument has no Tushare code identifier."
    elif source_profile == "fmp" or _identifier(
        instrument,
        identifier_type="provider_symbol",
        prefix="fmp:",
    ):
        provider = "fmp"
        provider_symbol = _identifier(
            instrument,
            identifier_type="provider_symbol",
            prefix="fmp:",
        )
        if provider_symbol:
            sections, errors = _fmp_reference(
                instrument_type=instrument_type,
                provider_symbol=provider_symbol,
                client=client or FmpClient(),
            )
        else:
            errors["reference"] = "The instrument has no FMP provider symbol."
    elif instrument_type == "private_fund":
        provider = "email/manual"
    else:
        errors["reference"] = "No external reference-data provider is configured."

    return {
        "instrument_id": str(instrument["instrument_id"]),
        "instrument_type": instrument_type,
        "provider": provider,
        "provider_symbol": provider_symbol,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": _source_context(instrument),
        "sections": sections,
        "section_errors": errors,
    }


def refresh_instrument_reference_data(instrument_id: str) -> dict[str, object] | None:
    """Collect provider reference data in the maintenance process only."""
    record = get_instrument_reference_data(instrument_id)
    if record is None:
        return None
    record = StudioInstrumentReferenceData.model_validate(record).model_dump(mode="json")
    with get_session_factory()() as session:
        key = instrument_id
        stored = session.get(InstrumentReferenceSnapshot, key)
        if stored is None:
            session.add(InstrumentReferenceSnapshot(instrument_id=key, value_json=record))
        else:
            stored.value_json = record
        session.commit()
    return record


def refresh_reference_data_batch(*, instrument_ids: list[str] | None = None) -> dict[str, object]:
    """Collect reference snapshots for registered assets; leave prices/NAV untouched."""
    selected = set(instrument_ids) if instrument_ids is not None else None
    results = []
    for instrument in list_instruments(include_inactive=False):
        instrument_id = str(instrument["instrument_id"])
        if instrument["instrument_type"] not in REFERENCE_INSTRUMENT_TYPES:
            continue
        if selected is not None and instrument_id not in selected:
            continue
        try:
            record = refresh_instrument_reference_data(instrument_id)
            errors = record.get("section_errors", {}) if record else {"reference": "Instrument not found"}
            status = "failed" if errors else "refreshed"
            if record and record["provider"] == "manual":
                status = "skipped"
            results.append({"instrument_id": instrument_id, "status": status, "message": str(errors) if errors else "Reference snapshot collected"})
        except Exception as error:
            results.append({"instrument_id": instrument_id, "status": "failed", "message": str(error)})
    return {"results": results, "refreshed_count": sum(item["status"] == "refreshed" for item in results)}


def read_instrument_reference_data(instrument_id: str) -> dict[str, object] | None:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        return None
    with get_session_factory()() as session:
        stored = session.get(InstrumentReferenceSnapshot, instrument_id)
        if stored is not None:
            return dict(stored.value_json)
    return {
        "instrument_id": instrument_id,
        "instrument_type": instrument["instrument_type"],
        "provider": "unavailable",
        "provider_symbol": None,
        "fetched_at": None,
        "source": _source_context(instrument),
        "sections": {},
        "section_errors": {"reference": "Reference data has not been collected by backend maintenance."},
    }
