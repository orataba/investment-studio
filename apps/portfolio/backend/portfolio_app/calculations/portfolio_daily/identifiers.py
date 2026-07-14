"""Stable producer-owned identities for immutable Portfolio Daily rows."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5


PORTFOLIO_DAILY_UUID_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "portfolio-operations-workbench/portfolio-daily/exact-v1",
)


def portfolio_daily_uuid(kind: str, *identity_parts: object) -> UUID:
    if not kind or kind != kind.strip() or "|" in kind:
        raise ValueError("Portfolio Daily identity kind must be canonical")
    parts: list[str] = []
    for value in identity_parts:
        rendered = str(value)
        if not rendered or "|" in rendered:
            raise ValueError("Portfolio Daily identity parts must be canonical")
        parts.append(rendered)
    if not parts:
        raise ValueError("Portfolio Daily identity requires at least one part")
    return uuid5(PORTFOLIO_DAILY_UUID_NAMESPACE, "|".join((kind, *parts)))


__all__ = ["PORTFOLIO_DAILY_UUID_NAMESPACE", "portfolio_daily_uuid"]
