from __future__ import annotations

from datetime import date
import json

import pytest

from platform_app.services import csindex_client


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_fetch_index_performance_uses_official_query_contract() -> None:
    captured: dict[str, object] = {}

    def fake_open(request: object, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "code": "200",
                "msg": "Success",
                "success": True,
                "data": [
                    {
                        "tradeDate": "20260730",
                        "indexCode": "H11001",
                        "close": 266.27,
                    }
                ],
            }
        )

    rows = csindex_client.fetch_index_performance(
        index_code="h11001",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 31),
        api_url="https://www.csindex.com.cn/csindex-home/",
        timeout_seconds=12,
        opener=fake_open,
    )

    assert captured["url"] == (
        "https://www.csindex.com.cn/csindex-home/perf/index-perf?"
        "indexCode=H11001&startDate=20260717&endDate=20260731"
    )
    assert captured["method"] == "GET"
    assert captured["timeout"] == 12
    assert captured["headers"]["Accept"] == "application/json"
    assert rows == [
        {
            "tradeDate": "20260730",
            "indexCode": "H11001",
            "close": 266.27,
        }
    ]


def test_fetch_index_performance_rejects_provider_error_payload() -> None:
    def fake_open(_request: object, timeout: int) -> FakeResponse:
        assert timeout == 30
        return FakeResponse(
            {
                "code": "500",
                "msg": "provider failure",
                "success": False,
                "data": [],
            }
        )

    with pytest.raises(
        csindex_client.CsindexClientError,
        match="provider failure",
    ):
        csindex_client.fetch_index_performance(
            index_code="H11001",
            start_date=date(2026, 7, 17),
            end_date=date(2026, 7, 31),
            opener=fake_open,
        )
