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
    market.latest.return_value = {"rows": [{"date": latest}]}
    collector = Mock()
    statuses = Mock()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "pending_revisions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    eod_capture.ensure_fmp_security_history("300750-sz", symbol="300750.SZ", exchange_code="XSHE",
        store=market, collector=collector, now=clock)
    collector.raw_eod.assert_not_called()
    statuses.assert_not_called()
    market.latest.assert_called_once_with("raw_eod_daily", symbols=["300750.SZ"], limit=1)


def test_missing_shenzhen_close_still_fails_readiness_after_successful_empty_collection(monkeypatch):
    market = Mock()
    market.latest.return_value = {"rows": [{"date": "2026-09-10"}]}
    collector = Mock()
    collector.raw_eod.return_value = {"status": "ready"}
    statuses = Mock()
    monkeypatch.setattr(eod_capture.MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(eod_capture, "pending_revisions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(eod_capture, "update_refresh_status", statuses)
    with pytest.raises(FmpApiError, match="latest 2026-09-10, expected closed session 2026-09-11"):
        eod_capture.ensure_fmp_security_history("300750-sz", symbol="300750.SZ", exchange_code="XSHE",
            store=market, collector=collector, now=datetime(2026, 9, 11, 8, tzinfo=UTC))
    collector.raw_eod.assert_called_once_with(date(2026, 9, 10), date(2026, 9, 11), ["300750.SZ"])
    assert statuses.call_args.kwargs["status"] == "failed"
    assert statuses.call_args.kwargs["instrument_id"] == "300750-sz"
