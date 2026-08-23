from __future__ import annotations

from platform_app.services.securities import service


def test_fmp_index_refresh_uses_the_index_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        service,
        "get_instrument",
        lambda _instrument_id: {
            "instrument_id": "fmp-index",
            "instrument_type": "index",
            "source_settings": {"source_api_profile": "fmp"},
        },
    )

    def fake_refresh_fmp_eod(
        instrument_id: str,
        *,
        instrument_type: str,
        full_history: bool,
        client: object,
    ) -> dict[str, object]:
        captured.update(
            {
                "instrument_id": instrument_id,
                "instrument_type": instrument_type,
                "full_history": full_history,
                "client": client,
            }
        )
        return {"instrument_id": instrument_id}

    monkeypatch.setattr(service, "refresh_fmp_eod", fake_refresh_fmp_eod)
    client = object()

    result = service.refresh_security_eod(
        "fmp-index",
        full_history=True,
        client=client,  # type: ignore[arg-type]
    )

    assert result == {"instrument_id": "fmp-index"}
    assert captured == {
        "instrument_id": "fmp-index",
        "instrument_type": "index",
        "full_history": True,
        "client": client,
    }


def test_tushare_index_refresh_uses_only_the_configured_primary_source(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        service,
        "get_instrument",
        lambda _instrument_id: {
            "instrument_id": "tushare-index",
            "instrument_type": "index",
            "source_settings": {"source_api_profile": "tushare"},
        },
    )

    from platform_app.services import market_data_ops

    def fake_refresh_market_data(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": "tushare-index"}

    monkeypatch.setattr(market_data_ops, "refresh_market_data", fake_refresh_market_data)

    result = service.refresh_security_eod("tushare-index", full_history=True)

    assert result == {"instrument_id": "tushare-index"}
    assert captured == {
        "instrument_id": "tushare-index",
        "updated_by": "tushare_index_sync",
        "full_history": True,
        "source": "tushare",
    }
