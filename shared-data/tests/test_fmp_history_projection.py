"""A local history replay must not mix source versions or publish truncation."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from studio_data.services.fmp import crypto, eod


@pytest.fixture(params=["listed", "crypto"])
def projection(request, monkeypatch):
    module = eod if request.param == "listed" else crypto
    symbol = "REVIEW" if module is eod else "BTCUSD"
    instrument = {
        "instrument_type": "equity" if module is eod else "crypto",
        "currency": "USD",
        "exchange_code": "XNAS" if module is eod else None,
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": f"fmp:{symbol}"}],
    }
    publish = Mock(return_value=(1, 1))
    status = Mock(side_effect=lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(module, "upsert_price_history", publish)
    monkeypatch.setattr(module, "update_refresh_status", status)

    def refresh(store):
        if module is eod:
            return eod.refresh_fmp_eod("review", instrument_type="equity", store=store)
        return crypto.refresh_fmp_crypto_eod("review", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))

    return module, symbol, refresh, publish, status


def test_one_file_view_keeps_history_consistent_during_source_revision(projection, tmp_path, monkeypatch):
    module, symbol, refresh, publish, _ = projection
    dataset = "raw_eod_daily" if module is eod else "market_series_daily"
    revised_field = "adjusted_close" if module is eod else "close"
    start = date(2026, 9, 7) - timedelta(days=10000)
    rows = [dict(date=(start + timedelta(days=index)).isoformat(), symbol=symbol, series_id=symbol,
                 open=100, high=110, low=40, close=100, adjusted_close=100) for index in range(10001)]
    source = eod.NumericStore(eod.MarketSettings(f"sqlite:///{tmp_path / 'numeric.db'}", tmp_path / "numeric"))
    source.create_schema_for_testing()
    try:
        source.ingest(dataset, [rows], source="fixture", observed_at=datetime(2026, 9, 7, tzinfo=UTC))
        query = source.query
        revised = False

        def query_during_revision(*args, **kwargs):
            nonlocal revised
            result = query(*args, **kwargs)
            if not revised:
                revised = True
                # Publish after the first read, exactly where a paged reader
                # would switch its remaining observations to another version.
                source.ingest(dataset, [[{**row, revised_field: 50} for row in rows]], source="fixture",
                              observed_at=datetime(2026, 9, 8, tzinfo=UTC))
            return result

        monkeypatch.setattr(source, "query", query_during_revision)
        for expected in (Decimal("100"), Decimal("50")):
            refresh(source)
            payload = publish.call_args.kwargs
            basis = "adjusted_close" if module is eod else "close"
            points = [row for row in payload["market_data_rows"] if row["quote_basis"] == basis]
            assert len(points) == len(payload["price_bar_rows"]) == 10001
            assert {row["value"] for row in points} == {expected}
            if module is eod:
                assert {row["adjustment_factor"] for row in payload["price_bar_rows"]} == {expected / 100}
            else:
                assert {row["close"] for row in payload["price_bar_rows"]} == {expected}
        assert query(dataset, versions=True)["total"] == 20002
    finally:
        source.close()


@pytest.mark.parametrize("total", [1, 100001])
def test_incomplete_history_never_publishes_a_partial_result(projection, total):
    _, symbol, refresh, publish, status = projection
    row = dict(date="2026-09-07", symbol=symbol, series_id=symbol,
               open=100, high=110, low=90, close=100, adjusted_close=100)
    source = Mock()
    source.query.return_value = {"rows": [] if total == 1 else [row], "total": total}
    with pytest.raises(ValueError, match="incomplete history"):
        refresh(source)
    publish.assert_not_called()
    status.assert_not_called()
    source.query.assert_called_once()


@pytest.mark.parametrize("clock, expected", [
    (datetime(2026, 10, 8, 19, 59, tzinfo=UTC), "2026-10-07"),
    (datetime(2026, 10, 8, 20, 30, tzinfo=UTC), "2026-10-08"),
])
def test_listed_projection_excludes_unclosed_rows_and_shares_capture_clock(monkeypatch, tmp_path, clock, expected):
    source = eod.NumericStore(eod.MarketSettings(f"sqlite:///{tmp_path / 'numeric.db'}", tmp_path / "numeric"))
    source.create_schema_for_testing()
    source.ingest("raw_eod_daily", [[dict(symbol="CLOCK.L", date=day,
        open=100, high=110, low=90, close=100, adjusted_close=100)
        for day in ("2026-10-07", "2026-10-08", "2026-10-09")]], source="fixture", observed_at=clock)
    instrument = {"instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS",
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "fmp:CLOCK.L"}]}
    monkeypatch.setattr(eod, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(eod, "NumericStore", lambda _: source)
    monkeypatch.setattr(eod.MarketSettings, "from_environment", lambda: object())
    capture, publish = Mock(return_value=[]), Mock(return_value=(1, 1))
    monkeypatch.setattr(eod, "ensure_fmp_security_history", capture)
    monkeypatch.setattr(eod, "upsert_price_history", publish)
    monkeypatch.setattr(eod, "update_refresh_status", lambda **kwargs: kwargs)
    try:
        result = eod.refresh_fmp_eod("fixture", instrument_type="equity", acquire_history=True, now=clock)
        assert result["status"] == "refreshed"
        capture.assert_called_once_with("fixture", symbol="CLOCK.L", exchange_code="XNAS", currency="USD", now=clock)
        dates = {row["as_of_date"] for row in publish.call_args.kwargs["price_bar_rows"]}
        assert max(dates) == expected
        assert "2026-10-09" not in dates
    finally:
        source.close()


@pytest.mark.parametrize("market_calendar, expected", [("XNYS", "2026-10-08"), (None, None), ("UNKNOWN", None)])
def test_index_projection_uses_source_calendar_without_listing_mic(monkeypatch, market_calendar, expected):
    from studio_data.services.fmp.client import FmpApiError
    from studio_data.services.securities import service

    instrument = {"instrument_type": "index", "currency": "USD", "exchange_code": None,
        "source_settings": {"source_api_profile": "fmp", "market_calendar": market_calendar},
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "fmp:^INDEX"}]}
    source, publish, status = Mock(), Mock(return_value=(1, 1)), Mock(side_effect=lambda **kwargs: kwargs)
    source.query.return_value = {"rows": [dict(date="2026-10-08", symbol="^INDEX",
        open=100, high=110, low=90, close=100, adjusted_close=100)], "total": 1}
    monkeypatch.setattr(eod, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(service, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(eod, "upsert_price_history", publish)
    monkeypatch.setattr(eod, "update_refresh_status", status)
    clock = datetime(2026, 10, 8, 20, 30, tzinfo=UTC)
    monkeypatch.setattr(eod, "datetime", Mock(now=Mock(return_value=clock)))
    if expected is None:
        with pytest.raises(FmpApiError, match="verified source"):
            service.refresh_security_eod("index", store=source)
        source.query.assert_not_called()
        publish.assert_not_called()
    else:
        result = service.refresh_security_eod("index", store=source)
        assert result["status"] == "refreshed"
        assert source.query.call_args.kwargs["end"] == expected
        assert len(publish.call_args.kwargs["price_bar_rows"]) == 1
