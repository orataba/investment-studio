from __future__ import annotations

import pytest

from platform_app.services import tushare_client


class FakeFrame:
    pass


class FakePro:
    def __init__(self) -> None:
        self._DataApi__http_url = ""
        self.bound_calls: list[dict[str, object]] = []

    def index_basic(self, **kwargs: object) -> FakeFrame:
        self.bound_calls.append(kwargs)
        return FakeFrame()


class FakeTushareModule:
    def __init__(self) -> None:
        self.pro = FakePro()
        self.pro_api_calls: list[dict[str, object]] = []
        self.pro_bar_calls: list[dict[str, object]] = []

    def pro_api(self, token: str, timeout: int) -> FakePro:
        self.pro_api_calls.append({"token": token, "timeout": timeout})
        return self.pro

    def pro_bar(self, **kwargs: object) -> FakeFrame:
        self.pro_bar_calls.append(kwargs)
        return FakeFrame()


def test_create_tushare_client_applies_proxy_to_every_client() -> None:
    sdk = FakeTushareModule()

    client = tushare_client.create_tushare_client(
        token="secret-token",
        api_url="https://ttx.dailyfetch.top/",
        timeout_seconds=12,
        sdk_module=sdk,
    )

    assert client is sdk.pro
    assert sdk.pro_api_calls == [{"token": "secret-token", "timeout": 12}]
    assert client._DataApi__http_url == "https://ttx.dailyfetch.top/"


def test_invoke_tushare_api_uses_bound_pro_endpoint() -> None:
    sdk = FakeTushareModule()

    frame = tushare_client.invoke_tushare_api(
        client=sdk.pro,
        api_name="index_basic",
        params={"limit": 5},
        fields="ts_code,name",
        sdk_module=sdk,
    )

    assert isinstance(frame, FakeFrame)
    assert sdk.pro.bound_calls == [{"limit": 5, "fields": "ts_code,name"}]


def test_invoke_tushare_api_passes_client_to_module_level_pro_bar() -> None:
    sdk = FakeTushareModule()

    frame = tushare_client.invoke_tushare_api(
        client=sdk.pro,
        api_name="pro_bar",
        params={"ts_code": "000001.SZ", "limit": 3},
        fields="ignored-by-pro-bar",
        sdk_module=sdk,
    )

    assert isinstance(frame, FakeFrame)
    assert sdk.pro_bar_calls == [
        {"api": sdk.pro, "ts_code": "000001.SZ", "limit": 3}
    ]


def test_create_tushare_client_requires_token() -> None:
    with pytest.raises(
        tushare_client.TushareClientConfigurationError,
        match="Tushare token is not configured",
    ):
        tushare_client.create_tushare_client(token="   ")


def test_redact_tushare_error_message_removes_provider_echoed_token() -> None:
    token = "provider-echoed-secret"

    message = tushare_client.redact_tushare_error_message(
        RuntimeError(f"token不对，您传过来的是{token}请确认"),
        token=token,
    )

    assert token not in message
    assert "[REDACTED_TOKEN]" in message
