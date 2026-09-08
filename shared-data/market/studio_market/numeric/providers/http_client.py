from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from curl_cffi import requests as curl_requests


MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class PublicHttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicResponse:
    endpoint: str
    params: dict[str, str]
    received_at: datetime
    body: bytes


def get_public_bytes(
    endpoint: str,
    params: Mapping[str, object] | None = None,
    *,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
) -> PublicResponse:
    safe_params = {
        str(key): str(value)
        for key, value in (params or {}).items()
        if value is not None
    }
    for attempt in range(1, max_attempts + 1):
        try:
            response = curl_requests.get(
                endpoint,
                params=safe_params,
                headers={
                    "Accept": "text/csv,application/octet-stream;q=0.9,*/*;q=0.1",
                    "User-Agent": "investment-studio-market/0.1 personal-research",
                },
                impersonate="chrome",
                timeout=timeout_seconds,
            )
        except (curl_requests.exceptions.RequestException, TimeoutError) as exc:
            if attempt == max_attempts:
                raise PublicHttpError(
                    f"public download failed after {attempt} attempts: "
                    f"{type(exc).__name__}"
                ) from None
            _wait(attempt)
            continue

        status = response.status_code
        try:
            if status == 429 or 500 <= status <= 599:
                if attempt == max_attempts:
                    raise PublicHttpError(
                        f"public download failed with HTTP status {status}"
                    )
                _wait(attempt)
                continue
            if status < 200 or status >= 300:
                raise PublicHttpError(
                    f"public download failed with HTTP status {status}"
                )
            body = response.content
        finally:
            response.close()

        if not body:
            raise PublicHttpError("public download returned an empty response")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PublicHttpError("public download exceeded the 64 MB safety bound")
        return PublicResponse(
            endpoint=endpoint,
            params=safe_params,
            received_at=datetime.now(timezone.utc),
            body=body,
        )

    raise AssertionError("unreachable")


def _wait(attempt: int) -> None:
    delay = min(8.0, float(2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
    time.sleep(delay)
