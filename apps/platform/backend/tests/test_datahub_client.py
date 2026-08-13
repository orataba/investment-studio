from __future__ import annotations

import pytest

from platform_app.services import datahub_client


class FakeResponse:
    def __init__(self, payload: object, *, status_error: Exception | None = None) -> None:
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_fetch_tushare_rows_maps_endpoint_and_decodes_fields_items() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": ["ts_code", "trade_date", "close"],
                        "items": [["000300.SH", "20260810", 4210.5]],
                        "has_more": False,
                    },
                }
            )
        ]
    )

    rows = datahub_client.fetch_tushare_rows(
        api_key="secret-key",
        api_url="http://datahubco.com/app-api/openapi/v1/tushare/",
        api_name="index_daily",
        params={"ts_code": "000300.SH"},
        fields="ts_code,trade_date,close",
        timeout_seconds=12,
        session=session,
    )

    assert rows == [
        {"ts_code": "000300.SH", "trade_date": "20260810", "close": 4210.5}
    ]
    assert session.calls == [
        {
            "url": (
                "http://datahubco.com/app-api/openapi/v1/tushare/index-daily"
            ),
            "headers": {"X-API-Key": "secret-key"},
            "params": {
                "ts_code": "000300.SH",
                "fields": "ts_code,trade_date,close",
                "limit": 5000,
                "offset": 0,
            },
            "timeout": 12,
        }
    ]


def test_fetch_tushare_rows_follows_offset_pagination() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code"],
                        "items": [["000001.SZ"], ["000002.SZ"]],
                        "has_more": True,
                    },
                }
            ),
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code"],
                        "items": [["000004.SZ"]],
                        "has_more": False,
                    },
                }
            ),
        ]
    )

    rows = datahub_client.fetch_tushare_rows(
        api_key="secret-key",
        api_name="stock_basic",
        session=session,
    )

    assert [row["ts_code"] for row in rows] == [
        "000001.SZ",
        "000002.SZ",
        "000004.SZ",
    ]
    assert [call["params"]["offset"] for call in session.calls] == [0, 2]


def test_fetch_tushare_rows_rejects_provider_error() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {"code": 40203, "msg": "访问频率受限", "data": None}
            )
            for _ in range(5)
        ]
    )

    with pytest.raises(
        datahub_client.DataHubClientError,
        match="40203.*访问频率受限",
    ):
        datahub_client.fetch_tushare_rows(
            api_key="secret-key",
            api_name="daily",
            session=session,
        )

    assert len(session.calls) == 5


def test_fetch_tushare_rows_retries_transient_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {"code": 40204, "msg": "IP数量超限", "data": None}
            ),
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code"],
                        "items": [["159819.SZ"]],
                        "has_more": False,
                    },
                }
            ),
        ]
    )
    delays: list[float] = []
    monkeypatch.setattr(datahub_client.time, "sleep", delays.append)

    rows = datahub_client.fetch_tushare_rows(
        api_key="secret-key",
        api_name="fund_daily",
        session=session,
    )

    assert rows == [{"ts_code": "159819.SZ"}]
    assert delays == [0.25]


def test_fetch_tushare_rows_requires_api_key() -> None:
    with pytest.raises(
        datahub_client.DataHubClientError,
        match="PORTFOLIO_OPS_PLATFORM_DATAHUB_API_KEY",
    ):
        datahub_client.fetch_tushare_rows(
            api_key="   ",
            api_name="daily",
            session=FakeSession([]),
        )


def test_redact_datahub_error_message_removes_provider_echoed_key() -> None:
    api_key = "provider-echoed-secret"

    message = datahub_client.redact_datahub_error_message(
        RuntimeError(f"invalid key: {api_key}"),
        api_key=api_key,
    )

    assert api_key not in message
    assert "[REDACTED_API_KEY]" in message
