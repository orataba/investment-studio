from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core.db_models import InstrumentReferenceObservation, InstrumentReferenceSnapshot
from studio_data.services import instrument_reference as reference


def test_reference_http_read_never_calls_provider_and_cli_persists_snapshot(
    monkeypatch,
):
    engine = create_engine("sqlite://")
    from investment_studio_instrument_core.db_models import Instrument
    Instrument.__table__.create(engine)
    InstrumentReferenceSnapshot.__table__.create(engine)
    InstrumentReferenceObservation.__table__.create(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(reference, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        reference,
        "get_instrument",
        lambda _: {"instrument_id": "asset-1", "instrument_type": "etf"},
    )
    calls = []
    record = {
        "instrument_id": "asset-1",
        "instrument_type": "etf",
        "provider": "fmp",
        "provider_symbol": "SPY",
        "fetched_at": "2026-09-04T00:00:00Z",
        "source": {},
        "sections": {"profile": {"name": "Example"}},
        "section_errors": {},
    }
    monkeypatch.setattr(
        reference, "get_instrument_reference_data", lambda _: calls.append(1) or record
    )
    assert reference.read_instrument_reference_data("asset-1")["fetched_at"] is None
    assert calls == []
    reference.refresh_instrument_reference_data("asset-1")
    assert calls == [1]
    assert (
        reference.read_instrument_reference_data("asset-1")["sections"]
        == record["sections"]
    )
    assert calls == [1]
