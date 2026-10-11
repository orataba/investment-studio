"""Canonical listing identity and actual completed session are separate contracts."""
from datetime import UTC, date, datetime
from unittest.mock import Mock

import exchange_calendars
import pytest

from investment_studio_instrument_core.listing_contract import (
    SUPPORTED_LISTING_EXCHANGES, session_calendar_name,
)
from studio_data.services.fmp import eod_capture
from studio_data.services.fmp.client import FmpApiError


def test_every_supported_listing_resolves_a_calendar_without_rewriting_its_mic():
    for mic in SUPPORTED_LISTING_EXCHANGES:
        resolved = session_calendar_name(mic)
        assert resolved == ("XSHG" if mic == "XSHE" else mic)
        assert exchange_calendars.get_calendar(resolved) is not None
    assert "XSHE" in SUPPORTED_LISTING_EXCHANGES
    assert session_calendar_name("not-a-calendar") == "not-a-calendar"
    with pytest.raises(exchange_calendars.errors.InvalidCalendarName):
        exchange_calendars.get_calendar(session_calendar_name("not-a-calendar"))


@pytest.mark.parametrize("clock, latest", [
    (datetime(2026, 9, 13, tzinfo=UTC), "2026-09-11"),  # Sunday: Friday is complete.
    (datetime(2026, 10, 1, 10, tzinfo=UTC), "2026-09-30"),  # Mainland holiday.
    (datetime(2026, 9, 11, 3, tzinfo=UTC), "2026-09-10"),  # Before Shenzhen's close.
])
def test_shenzhen_capture_uses_actual_completed_mainland_session(monkeypatch, clock, latest):
    market = Mock()
    market.query.return_value = {"rows": [{"date": latest}], "total": 1}
    collector = Mock()
    statuses = Mock()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "pending_revisions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    eod_capture.ensure_fmp_security_history("300750-sz", symbol="300750.SZ", exchange_code="XSHE",
        store=market, collector=collector, now=clock)
    collector.raw_eod.assert_not_called()
    statuses.assert_not_called()
    market.query.assert_called_once_with("raw_eod_daily", symbols=["300750.SZ"], end=latest, limit=100000)


def test_missing_shenzhen_close_still_fails_readiness_after_successful_empty_collection(monkeypatch):
    market = Mock()
    market.query.return_value = {"rows": [{"date": "2026-09-10"}], "total": 1}
    collector = Mock()
    collector.raw_eod.return_value = {"status": "ready"}
    statuses = Mock()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "pending_revisions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    with pytest.raises(FmpApiError, match="latest 2026-09-10, expected closed session 2026-09-11"):
        eod_capture.ensure_fmp_security_history("300750-sz", symbol="300750.SZ", exchange_code="XSHE",
            store=market, collector=collector, now=datetime(2026, 9, 11, 8, tzinfo=UTC))
    collector.raw_eod.assert_called_once_with(date(2026, 9, 10), date(2026, 9, 11), ["300750.SZ"],
        now=datetime(2026, 9, 11, 8, tzinfo=UTC), exchanges={"300750.SZ": "XSHE"})
    assert statuses.call_args.kwargs["status"] == "failed"
    assert statuses.call_args.kwargs["instrument_id"] == "300750-sz"


@pytest.mark.parametrize("mic, clock, latest", [
    ("XNAS", datetime(2026, 10, 8, 20, 30, tzinfo=UTC), "2026-10-08"),  # 16:30 NY, before old 17h cutoff.
    ("XNAS", datetime(2026, 11, 27, 18, 30, tzinfo=UTC), "2026-11-27"),  # Thanksgiving half-day, 13:30 NY.
    ("XNAS", datetime(2026, 11, 27, 17, 59, tzinfo=UTC), "2026-11-25"),
    ("XNAS", datetime(2026, 11, 9, 20, 30, tzinfo=UTC), "2026-11-06"),  # 15:30 winter NY, still open.
    ("XHKG", datetime(2026, 9, 11, 8, 5, tzinfo=UTC), "2026-09-10"),  # Closing auction not finished.
    ("XHKG", datetime(2026, 9, 11, 8, 11, tzinfo=UTC), "2026-09-11"),
    ("XHKG", datetime(2023, 9, 1, 10, tzinfo=UTC), "2023-08-31"),  # Official full-day closure.
])
def test_registered_exchange_and_actual_close_define_readiness(monkeypatch, mic, clock, latest):
    market, collector, statuses = Mock(), Mock(), Mock()
    market.query.return_value = {"rows": [{"date": latest}], "total": 1}
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "pending_revisions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    # The intentionally misleading suffix must not override the registry MIC.
    eod_capture.ensure_fmp_security_history("fixture", symbol="FIXTURE.L", exchange_code=mic,
        store=market, collector=collector, now=clock)
    market.query.assert_called_once_with("raw_eod_daily", symbols=["FIXTURE.L"], end=latest, limit=100000)
    collector.raw_eod.assert_not_called()
    statuses.assert_not_called()


def test_unknown_registered_exchange_cannot_fall_back_to_symbol(monkeypatch):
    market, collector, statuses = Mock(), Mock(), Mock()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    with pytest.raises(FmpApiError):
        eod_capture.ensure_fmp_security_history("spy", symbol="SPY", exchange_code="UNKNOWN",
            store=market, collector=collector, now=datetime(2026, 10, 8, 20, 30, tzinfo=UTC))
    collector.raw_eod.assert_not_called()
    market.query.assert_not_called()
    assert statuses.call_args.kwargs["status"] == "failed"
