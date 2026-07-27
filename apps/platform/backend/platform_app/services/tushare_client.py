"""Single initialization boundary for all Tushare SDK requests.

Callers must create ``pro`` through :func:`create_tushare_client` so the
configured compatible endpoint is always applied.  ``pro_bar`` is dispatched
through the SDK module with ``api=pro``; ordinary Pro endpoints are dispatched
on the returned client.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_TUSHARE_API_URL = "https://ttx.dailyfetch.top"


try:
    import tushare as ts
except ImportError:  # pragma: no cover - optional dependency
    ts = None


class TushareClientConfigurationError(RuntimeError):
    """Raised when the SDK client cannot be initialized safely."""


def _sdk_module(sdk_module: Any | None = None) -> Any:
    module = sdk_module if sdk_module is not None else ts
    if module is None:
        raise TushareClientConfigurationError(
            "Tushare SDK is not installed. Install the backend dependency first."
        )
    return module


def create_tushare_client(
    *,
    token: str | None,
    api_url: str = DEFAULT_TUSHARE_API_URL,
    timeout_seconds: int = 30,
    sdk_module: Any | None = None,
) -> Any:
    """Create a Pro client and apply the configured compatible endpoint."""

    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise TushareClientConfigurationError(
            "Tushare token is not configured. Set "
            "PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN first."
        )
    # The installed Tushare SDK appends ``/{api_name}`` itself.  Persisting a
    # trailing slash here produces ``//fund_nav`` and similar malformed proxy
    # paths, which some compatible endpoints terminate during TLS handling.
    normalized_api_url = str(api_url or "").strip().rstrip("/")
    if not normalized_api_url:
        raise TushareClientConfigurationError(
            "Tushare API URL is not configured. Set "
            "PORTFOLIO_OPS_PLATFORM_TUSHARE_API_URL first."
        )

    module = _sdk_module(sdk_module)
    client = module.pro_api(
        normalized_token,
        timeout=max(1, int(timeout_seconds)),
    )
    client._DataApi__http_url = normalized_api_url
    return client


def invoke_tushare_api(
    *,
    client: Any,
    api_name: str,
    params: Mapping[str, object] | None = None,
    fields: str | None = None,
    sdk_module: Any | None = None,
) -> Any:
    """Invoke a bound Pro endpoint or ``ts.pro_bar(api=client, ...)``."""

    normalized_api_name = str(api_name or "").strip()
    if not normalized_api_name:
        raise TushareClientConfigurationError("Tushare API name is required.")

    kwargs = dict(params or {})
    if normalized_api_name == "pro_bar":
        # Tushare's pro_bar is a module-level helper and does not accept fields.
        return _sdk_module(sdk_module).pro_bar(api=client, **kwargs)

    if fields:
        kwargs["fields"] = fields
    api_method = getattr(client, normalized_api_name, None)
    if not callable(api_method):
        raise TushareClientConfigurationError(
            f"Unsupported Tushare API endpoint: {normalized_api_name}."
        )
    return api_method(**kwargs)


def redact_tushare_error_message(error: BaseException, *, token: str | None) -> str:
    """Remove the configured token from provider errors before logging/storing."""

    message = str(error)
    normalized_token = str(token or "").strip()
    if normalized_token:
        message = message.replace(normalized_token, "[REDACTED_TOKEN]")
    return message


__all__ = [
    "DEFAULT_TUSHARE_API_URL",
    "TushareClientConfigurationError",
    "create_tushare_client",
    "invoke_tushare_api",
    "redact_tushare_error_message",
]
