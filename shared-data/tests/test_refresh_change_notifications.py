"""Committed changes survive quality warnings and handled refresh failures."""
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from studio_data.commands import instruments
from studio_data.contracts import StudioBulkRefreshRequest
from studio_data.services import market_data_ops
from studio_data.services.futu import FutuRefreshError


@pytest.mark.parametrize("watermark", ["market_data_updated_at", "calculation_inputs_updated_at"])
@pytest.mark.parametrize("changed", [True, False])
@pytest.mark.parametrize("failure_type", [FutuRefreshError, market_data_ops.MarketDataItemTimeout])
def test_failed_refresh_keeps_committed_change_for_batch_notification(
    monkeypatch, watermark, changed, failure_type,
):
    before = {
        "instrument_id": "etf-test", "instrument_name": "ETF fixture", "instrument_type": "etf",
        "source_settings": {"source_mode": "api", "source_api_profile": "futu"},
        watermark: "2026-10-07T10:00:00+00:00",
    }
    after = {
        **before, watermark: "2026-10-08T10:00:00+00:00" if changed else before[watermark],
        "refresh_status": {"status": "failed", "message": "Refresh failed after projection."},
    }
    read = Mock(return_value=before)
    monkeypatch.setattr(market_data_ops, "get_instrument", read)
    monkeypatch.setattr(market_data_ops, "list_instruments", lambda **_: [before])
    monkeypatch.setattr(market_data_ops, "get_settings", Mock())
    monkeypatch.setattr(market_data_ops, "market_data_item_timeout", lambda _: nullcontext())
    monkeypatch.setattr(market_data_ops, "refresh_market_data",
                        Mock(side_effect=failure_type("Refresh failed after projection.")))
    monkeypatch.setattr(market_data_ops, "update_refresh_status", Mock(return_value=after))
    notify = Mock()
    monkeypatch.setattr(instruments, "notify_market_data_downstream_refresh", notify)

    result = instruments.refresh_instrument_market_data_batch(
        StudioBulkRefreshRequest(source="futu"),
    ).model_dump(mode="json")
    item = result["results"][0]
    assert item["status"] == "failed"
    assert item["message"] == "Refresh failed after projection."
    assert item["canonical_changed"] is changed
    assert result["refreshed_count"] == 0
    assert notify.call_count == int(changed)
    if changed:
        notify.assert_called_once_with(instrument_ids=["etf-test"], raise_on_error=True)
    # The failed refresh's returned status record already includes watermarks.
    # A second query cannot mask the original failure.
    read.assert_called_once_with("etf-test")


def test_watermark_read_failure_prevents_refresh_and_preserves_original_error(monkeypatch):
    failure = RuntimeError("Registry read unavailable")
    refresh, status = Mock(), Mock()
    monkeypatch.setattr(market_data_ops, "get_instrument", Mock(side_effect=failure))
    monkeypatch.setattr(market_data_ops, "refresh_market_data", refresh)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", status)
    with pytest.raises(RuntimeError) as caught:
        market_data_ops.refresh_market_data_with_timeout(instrument_id="fixture", updated_by=None)
    assert caught.value is failure
    refresh.assert_not_called()
    status.assert_not_called()


def test_wind_failure_does_not_enter_immediate_paid_retry(monkeypatch):
    from studio_market.wind import WindError

    before = {"instrument_id": "881001-wi", "source_settings": {"source_api_profile": "wind"}}
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda _: before)
    monkeypatch.setattr(market_data_ops, "market_data_item_timeout", lambda _: nullcontext())
    query = Mock(side_effect=WindError("Wind quota unavailable", 429))
    monkeypatch.setattr(market_data_ops, "refresh_market_data", query)
    status = Mock(side_effect=lambda **kw: {**before, "refresh_status": kw})
    monkeypatch.setattr(market_data_ops, "update_refresh_status", status)
    result = market_data_ops.refresh_market_data_with_timeout(
        instrument_id="881001-wi", updated_by="test", source="configured")
    assert result["refresh_status"]["status"] == "blocked"
    assert result["canonical_changed"] is False
    query.assert_called_once()


def test_configured_wind_dispatch_preserves_source_ownership(monkeypatch):
    from studio_data.services import wind_index

    before = {"instrument_id": "881001-wi", "source_settings": {
        "source_mode": "api", "source_api_profile": "wind"}}
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda _: before)
    query = Mock(return_value=before)
    monkeypatch.setattr(wind_index, "refresh_wind_index", query)
    assert market_data_ops.refresh_market_data(
        instrument_id="881001-wi", updated_by="test") == before
    query.assert_called_once_with("881001-wi", full_history=False)
    with pytest.raises(wind_index.WindIndexError, match="differs"):
        market_data_ops.refresh_market_data(
            instrument_id="881001-wi", updated_by="test", source="fmp")
    assert query.call_count == 1
