from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


type DependencyExecutor = Connection | Session


class PortfolioDailyDependencyKind(StrEnum):
    PORTFOLIO_CONFIG = "portfolio_config"
    TRANSACTION = "transaction"
    ACCOUNT = "account"
    TAXONOMY = "taxonomy"
    INSTRUMENT = "instrument"
    CURRENCY = "currency"
    FX_MARKET = "fx_market"


def _trimmed(value: str, *, name: str, maximum: int) -> str:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(
            f"{name} must be a non-empty trimmed string of at most {maximum} characters"
        )
    return value


@dataclass(frozen=True, slots=True)
class PortfolioDailyDependencySubscription:
    """One durable Portfolio Daily scope-to-fact dependency."""

    portfolio_id: str
    kind: PortfolioDailyDependencyKind
    key: str

    def __post_init__(self) -> None:
        _trimmed(self.portfolio_id, name="portfolio_id", maximum=255)
        _trimmed(self.key, name="dependency_key", maximum=255)
        if self.kind is PortfolioDailyDependencyKind.PORTFOLIO_CONFIG:
            if self.key != self.portfolio_id:
                raise ValueError("portfolio_config key must equal portfolio_id")
        elif self.kind is PortfolioDailyDependencyKind.FX_MARKET:
            if self.key != "*":
                raise ValueError("fx_market uses the exact wildcard key '*'")
        elif self.kind is PortfolioDailyDependencyKind.CURRENCY:
            if len(self.key) != 3 or not self.key.isascii() or not self.key.isupper():
                raise ValueError("currency dependency key must be an uppercase ISO code")

    @classmethod
    def portfolio_config(cls, portfolio_id: str) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.PORTFOLIO_CONFIG, portfolio_id)

    @classmethod
    def transaction(
        cls, portfolio_id: str, transaction_id: str
    ) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.TRANSACTION, transaction_id)

    @classmethod
    def account(
        cls, portfolio_id: str, account_id: str
    ) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.ACCOUNT, account_id)

    @classmethod
    def taxonomy(
        cls, portfolio_id: str, taxonomy_id: str
    ) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.TAXONOMY, taxonomy_id)

    @classmethod
    def instrument(
        cls, portfolio_id: str, instrument_id: str
    ) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.INSTRUMENT, instrument_id)

    @classmethod
    def currency(
        cls, portfolio_id: str, currency: str
    ) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.CURRENCY, currency)

    @classmethod
    def fx_market(cls, portfolio_id: str) -> "PortfolioDailyDependencySubscription":
        return cls(portfolio_id, PortfolioDailyDependencyKind.FX_MARKET, "*")


@dataclass(frozen=True, slots=True)
class PortfolioDailyInvalidationReason:
    code: str
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _trimmed(self.code, name="reason_code", maximum=64)
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True, slots=True)
class PortfolioDailyInvalidationResult:
    portfolio_id: str
    generation: int


_SUBSCRIBE_AND_INVALIDATE = text(
    """
    SELECT portfolio.pd_subscribe_dependency_and_invalidate(
        :portfolio_id, :dependency_kind, :dependency_key,
        :reason_code, :reason_context
    ) AS generation
    """
).bindparams(bindparam("reason_context", type_=JSONB))


def subscribe_and_invalidate(
    executor: DependencyExecutor,
    subscription: PortfolioDailyDependencySubscription,
    reason: PortfolioDailyInvalidationReason,
) -> PortfolioDailyInvalidationResult:
    """Subscribe before bumping generation in the caller-owned transaction.

    The database function and every source trigger share one transaction-level
    advisory-lock protocol.  This closes the race between adding a new
    instrument to a portfolio and a concurrent quote/corporate-action revision.
    This function never begins, commits, rolls back, or retries a transaction.
    """

    generation = executor.scalar(
        _SUBSCRIBE_AND_INVALIDATE,
        {
            "portfolio_id": subscription.portfolio_id,
            "dependency_kind": subscription.kind.value,
            "dependency_key": subscription.key,
            "reason_code": reason.code,
            "reason_context": dict(reason.context),
        },
    )
    if generation is None:
        raise RuntimeError("dependency subscription invalidation returned no generation")
    resolved_generation = int(generation)
    if resolved_generation <= 0:
        raise RuntimeError("dependency subscription invalidation returned an invalid generation")
    return PortfolioDailyInvalidationResult(
        portfolio_id=subscription.portfolio_id,
        generation=resolved_generation,
    )


__all__ = [
    "PortfolioDailyDependencyKind",
    "PortfolioDailyDependencySubscription",
    "PortfolioDailyInvalidationReason",
    "PortfolioDailyInvalidationResult",
    "subscribe_and_invalidate",
]
