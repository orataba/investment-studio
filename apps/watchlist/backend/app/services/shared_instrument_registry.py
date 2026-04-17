from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.settings import get_settings


class SharedInstrumentRegistryError(RuntimeError):
    pass


class SharedInstrumentRegistryTransportError(SharedInstrumentRegistryError):
    pass


class SharedInstrumentRegistryHttpError(SharedInstrumentRegistryError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class SharedInstrumentRegistryNotFoundError(SharedInstrumentRegistryHttpError):
    def __init__(self, message: str = "Instrument not found in shared registry.") -> None:
        super().__init__(status_code=404, message=message)


def _fetch_json(path: str) -> dict[str, object]:
    settings = get_settings()
    url = f"{settings.platform_api_url.rstrip('/')}{path}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            raise SharedInstrumentRegistryNotFoundError() from error
        raise SharedInstrumentRegistryHttpError(
            status_code=error.code,
            message=f"Shared instrument registry returned HTTP {error.code}.",
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SharedInstrumentRegistryTransportError(
            "Failed to reach shared instrument registry."
        ) from error

def list_shared_instruments(
    *,
    search: str | None = None,
    asset_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    query_parts: list[str] = []
    if search and search.strip():
        query_parts.append(f"search={quote(search.strip())}")
    if asset_type and asset_type.strip():
        query_parts.append(f"asset_type={quote(asset_type.strip())}")
    if limit is not None:
        query_parts.append(f"limit={int(limit)}")

    path = "/api/instruments"
    if query_parts:
        path = f"{path}?{'&'.join(query_parts)}"

    payload = _fetch_json(path)
    instruments = payload.get("instruments", [])
    if isinstance(instruments, list):
        return [item for item in instruments if isinstance(item, dict)]
    return []


def get_shared_instrument(asset_id: str) -> dict[str, object] | None:
    try:
        return _fetch_json(f"/api/instruments/{quote(asset_id)}")
    except SharedInstrumentRegistryNotFoundError:
        return None


def resolve_shared_instrument(
    *,
    identifier_value: str,
    identifier_type: str | None = None,
) -> dict[str, object] | None:
    normalized_value = identifier_value.strip()
    if not normalized_value:
        return None
    query = f"identifier_value={quote(normalized_value)}"
    if identifier_type and identifier_type.strip():
        query = f"{query}&identifier_type={quote(identifier_type.strip())}"
    try:
        return _fetch_json(f"/api/instruments/resolve?{query}")
    except SharedInstrumentRegistryNotFoundError:
        return None
