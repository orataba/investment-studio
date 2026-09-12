from __future__ import annotations

import pytest

from studio_data.services import datahub_client


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
        api_name="fund_basic",
        session=session,
    )

    assert [row["ts_code"] for row in rows] == [
        "000001.SZ",
        "000002.SZ",
        "000004.SZ",
    ]
    assert [call["params"]["offset"] for call in session.calls] == [0, 2]


def test_index_history_partitions_date_window_and_restarts_offset() -> None:
    def page(day, more=False):
        return FakeResponse({"code":0, "data":{"fields":["ts_code", "trade_date", "close"],
            "items":[["H11001.CSI",day,265.1]], "has_more":more}})
    session = FakeSession([page("20240102", True), page("20250101"), page("20250102")])
    rows = datahub_client.fetch_tushare_rows(api_key="secret-key", api_name="index_daily",
        params={"ts_code":"H11001.CSI", "start_date":"20240102", "end_date":"20250102"}, session=session)
    assert [row["trade_date"] for row in rows] == ["20240102", "20250101", "20250102"]
    # The leap-year window is inclusive. The next request neither overlaps nor
    # omits the first day after it; row pagination belongs to its own window.
    assert [(call["params"]["start_date"], call["params"]["end_date"], call["params"]["offset"]) for call in session.calls] == [
        ("20240102", "20250101", 0), ("20240102", "20250101", 1), ("20250102", "20250102", 0)]


def test_later_index_history_failure_never_returns_partial_history() -> None:
    from requests import HTTPError
    session = FakeSession([
        FakeResponse({"code":0, "data":{"fields":["trade_date"], "items":[["20240102"]], "has_more":False}}),
        FakeResponse({}, status_error=HTTPError("HTTP 503")),
    ])
    with pytest.raises(datahub_client.DataHubClientError, match="HTTP 503"):
        datahub_client.fetch_tushare_rows(api_key="secret-key", api_name="index_daily",
            params={"ts_code":"H11001.CSI", "start_date":"20240102", "end_date":"20250102"}, session=session)
    assert len(session.calls) == 2


def test_date_window_contract_does_not_change_other_endpoints() -> None:
    session = FakeSession([FakeResponse({"code":0, "data":{"fields":[], "items":[], "has_more":False}})])
    assert datahub_client.fetch_tushare_rows(api_key="secret-key", api_name="fund_nav",
        params={"ts_code":"018654.OF", "start_date":"20000101", "end_date":"20260913"}, session=session) == []
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["start_date"] == "20000101"
    assert session.calls[0]["params"]["end_date"] == "20260913"


@pytest.mark.parametrize(
    ("api_name", "endpoint_path"),
    [
        ("fund_basic", "fund-basic"),
        ("fund_portfolio", "fund-portfolio"),
        ("fund_daily", "fund-daily"),
        ("fund_adj", "fund-adj"),
        ("fund_nav", "fund-nav"),
        ("index_basic", "index-basic"),
    ],
)
def test_reference_endpoints_map_to_datahub_paths(
    api_name: str,
    endpoint_path: str,
) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {"fields": [], "items": [], "has_more": False},
                }
            )
        ]
    )

    assert datahub_client.fetch_tushare_rows(
        api_key="secret-key",
        api_name=api_name,
        session=session,
    ) == []
    assert session.calls[0]["url"].endswith(f"/{endpoint_path}")


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
            api_name="index_daily",
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
        api_name="fund_nav",
        session=session,
    )

    assert rows == [{"ts_code": "159819.SZ"}]
    assert delays == [0.25]


def test_fetch_tushare_rows_requires_api_key() -> None:
    with pytest.raises(
        datahub_client.DataHubClientError,
        match="INVESTMENT_STUDIO_DATA_DATAHUB_API_KEY",
    ):
        datahub_client.fetch_tushare_rows(
            api_key="   ",
            api_name="index_daily",
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
