"""Current FMP projections, with immutable public captures as their audit history."""
from datetime import date

from sqlalchemy import select

from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData, InstrumentPriceBar
from studio_data.db.session import get_session_factory
from studio_data.services.fmp.client import FmpApiError


def publish_price_history(instrument, *, market_data_rows, price_bar_rows, generation=None,
                          unavailable_reason=None):
    """Reconcile a complete source history, never an incremental query result.

    Retired levels remain explicitly unavailable for audit; their immutable
    source prices remain in market_data. Bars are a current display projection.
    Source configuration and canonical watermarks fence concurrent refreshes.
    """
    instrument_id = instrument["instrument_id"]
    failure = unavailable_reason
    with get_session_factory()() as session:
        target = session.scalar(select(Instrument).where(
            Instrument.instrument_id == instrument_id).with_for_update())
        if target is None:
            raise FmpApiError("Registered FMP instrument disappeared during projection.")
        current = shared_store._instrument_to_store_dict(target)
        if any(current.get(key) != instrument.get(key) for key in (
                "instrument_type", "currency", "exchange_code", "market_data_updated_at", "calculation_inputs_updated_at")) or (
                shared_store._normalized_source_settings(current) != shared_store._normalized_source_settings(instrument)):
            raise FmpApiError("Instrument inputs changed during FMP refresh; retry from the current source.")
        bindings = lambda item: {(row["identifier_type"], row["identifier_value"]) for row in item.get("identifiers", [])}
        if bindings(current) != bindings(instrument):
            raise FmpApiError("Instrument identity changed during FMP refresh; retry from the current source.")
        points = list(session.scalars(select(InstrumentMarketData).where(
            InstrumentMarketData.instrument_id == instrument_id)))
        bars = list(session.scalars(select(InstrumentPriceBar).where(
            InstrumentPriceBar.instrument_id == instrument_id)))
        if any(not str(row.provider or "").startswith("fmp:") for row in [*points, *bars]):
            raise FmpApiError("Existing non-FMP prices require a reviewed source migration before FMP projection.")
        settings = dict(target.source_settings_json or {})
        if not generation and settings.get("fmp_price_series"):
            failure = "Bound FMP security history is missing its reviewed identity generation."
            settings["fmp_price_series"] = {**settings["fmp_price_series"],
                "projection_status": "unavailable", "projection_reason": failure}
        if generation:
            retained = settings.get("source_provider_security_id")
            security_id = generation.get("security_id")
            if not failure and (generation.get("status") != "verified" or not security_id or not generation.get("base_capture_id")):
                failure = "Reviewed FMP security history has no complete published generation."
            if not failure and retained is not None and str(retained) != str(security_id):
                failure = "FMP security identity differs from the registered binding; a reviewed instrument migration is required."
            if not failure:
                settings["source_provider_security_id"] = str(security_id)
            settings["fmp_price_series"] = {**generation, "projection_status": "unavailable" if failure else "complete",
                                           "projection_reason": failure}
        if failure:
            market_data_rows, price_bar_rows = [], []
        dates = lambda value: value if isinstance(value, date) else date.fromisoformat(str(value))
        desired = {(row["metric_family"], row["quote_basis"], dates(row["as_of_date"]), row["currency"])
                   for row in market_data_rows}
        withdrawn = 0
        for row in points:
            if (row.metric_family, row.quote_basis, row.as_of_date, row.currency) not in desired and row.status != "unavailable":
                row.status = "unavailable"
                withdrawn += 1
        desired_bars = {dates(row["as_of_date"]) for row in price_bar_rows}
        removed_bars = 0
        for bar in bars:
            if bar.as_of_date not in desired_bars:
                session.delete(bar)
                removed_bars += 1
        if withdrawn or removed_bars:
            target.market_data_updated_at = shared_store._next_market_data_watermark(session)
        if settings != target.source_settings_json:
            target.source_settings_json = settings
            target.calculation_inputs_updated_at = shared_store._next_calculation_input_watermark(session)
        point_count = shared_store._upsert_market_data_points(session, instrument_id=instrument_id, rows=market_data_rows)
        bar_count = shared_store._upsert_price_bars(session, instrument_id=instrument_id, rows=price_bar_rows)
        session.commit()
    if failure:
        # Invalidation is itself a committed input change. Existing scheduler
        # failure handling compares watermarks and notifies calculation owners.
        raise FmpApiError(failure)
    return int(point_count or 0) + withdrawn, int(bar_count or 0) + removed_bars
