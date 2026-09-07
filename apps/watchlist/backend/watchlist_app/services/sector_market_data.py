"""Read sector ETF inputs retained in Investment Studio's instrument data."""
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from investment_studio_instrument_core.db_models import InstrumentReferenceObservation, InstrumentReferenceSnapshot

from investment_studio_instrument_core.listing_contract import SECTOR_ETF_TICKERS


def read_sector_market_data(session: Session, ticker: str, *, as_of: datetime | None = None) -> dict | None:
    """Read the owned snapshot without fetching providers or changing its clocks.

    Shared ingestion owns constituent classification and provider normalization.
    Each collection is retained independently from the research runs that read it.
    A missing retained snapshot stays missing; there is no external data fallback.
    """
    ticker = ticker.strip().upper()
    if ticker not in SECTOR_ETF_TICKERS:
        raise ValueError("Only the 11 US Select Sector SPDR ETFs are supported")
    if as_of is None:
        snapshot = session.get(InstrumentReferenceSnapshot, ticker.lower())
    else:
        if as_of.tzinfo is None:
            raise ValueError("Sector reference cutoff must include a timezone.")
        snapshot = session.scalar(select(InstrumentReferenceObservation).where(
            InstrumentReferenceObservation.instrument_id == ticker.lower(),
            InstrumentReferenceObservation.collected_at <= as_of,
        ).order_by(InstrumentReferenceObservation.collected_at.desc(),
                   InstrumentReferenceObservation.observation_id.desc()).limit(1))
    retained = (snapshot.value_json.get("sections") or {}).get("sector_market_data") if snapshot else None
    if retained is None:
        return None
    if retained.get("ticker") != ticker:
        raise ValueError("行业ETF资料快照与登记标的归属不一致")
    result = deepcopy(retained)
    read_at = datetime.now(UTC)
    result["source"] = {**result["source"], "read_at": read_at.isoformat()}
    if as_of is not None:
        result["source"]["observation_id"] = snapshot.observation_id
    today = (as_of or read_at).astimezone(UTC).date().isoformat()
    # Whether a retained forecast is still forward-looking changes with the clock.
    result["gaps"] = [gap for gap in result["gaps"]
                      if gap["kind"] not in {"no_forward_annual_estimates", "no_forward_quarter_estimates"}]
    for holding in result["holdings"]:
        if holding["holding_type"] != "equity":
            continue
        for period, field in (("annual", "annual_estimates"), ("quarter", "quarterly_estimates")):
            if not any(row["target_period_end"] >= today for row in holding[field]):
                result["gaps"].append({"kind": f"no_forward_{period}_estimates", "symbol": holding["holding_symbol"]})
    return result
