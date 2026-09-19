"""Results and errors shared by maintenance commands and downstream delivery.

Inspecting a command's exit status must not initialize identity or HTTP delivery.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DownstreamRequestFailure:
    url: str
    message: str


@dataclass(frozen=True)
class DownstreamRefreshResult:
    request_count: int = 0
    failures: tuple[DownstreamRequestFailure, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.failures


class DownstreamRefreshError(RuntimeError):
    def __init__(self, result: DownstreamRefreshResult) -> None:
        self.result = result
        failed_urls = ", ".join(item.url for item in result.failures)
        super().__init__(
            f"{len(result.failures)} of {result.request_count} downstream refresh requests failed: "
            f"{failed_urls}"
        )
