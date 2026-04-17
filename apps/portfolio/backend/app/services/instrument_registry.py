from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.settings import get_settings


class InstrumentRegistryError(RuntimeError):
    pass


def _fetch_platform_payload(path: str) -> dict[str, object]:
    settings = get_settings()
    url = f"{settings.platform_api_url.rstrip('/')}{path}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise InstrumentRegistryError(f"Failed to reach shared platform data at '{path}'.") from error


def _fetch_registry_payload() -> dict[str, object]:
    return _fetch_platform_payload("/api/instruments")


def list_registry_instruments() -> list[dict[str, object]]:
    payload = _fetch_registry_payload()
    instruments = payload.get("instruments", [])
    if isinstance(instruments, list):
        return instruments
    return []


def get_registry_instrument(asset_id: str) -> dict[str, object] | None:
    for item in list_registry_instruments():
        if str(item.get("asset_id") or "") == asset_id:
            return item
    return None


def get_registry_instrument_detail(asset_id: str) -> dict[str, object] | None:
    payload = _fetch_platform_payload(f"/api/instruments/{asset_id}")
    if isinstance(payload, dict) and payload.get("asset_id"):
        return payload
    return None


def get_platform_fx_rates() -> dict[str, object]:
    return _fetch_platform_payload("/api/fx-rates")
