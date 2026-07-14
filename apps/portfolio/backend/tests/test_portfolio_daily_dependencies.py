from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from portfolio_app.calculations.portfolio_daily.dependencies import (
    PortfolioDailyDependencyKind,
    PortfolioDailyDependencySubscription,
    PortfolioDailyInvalidationReason,
    subscribe_and_invalidate,
)


pytestmark = pytest.mark.no_database


class _ExecutorWithoutTransactionControl:
    def __init__(self, values: list[object]) -> None:
        self.values = deque(values)
        self.calls: list[tuple[Any, dict[str, object]]] = []

    def scalar(self, statement: Any, parameters: dict[str, object]) -> object:
        self.calls.append((statement, parameters))
        return self.values.popleft()


def test_typed_subscription_keys_are_fail_closed() -> None:
    portfolio_id = "portfolio-1"
    assert PortfolioDailyDependencySubscription.portfolio_config(
        portfolio_id
    ).kind is PortfolioDailyDependencyKind.PORTFOLIO_CONFIG
    assert PortfolioDailyDependencySubscription.fx_market(portfolio_id).key == "*"
    with pytest.raises(ValueError, match="portfolio_config"):
        PortfolioDailyDependencySubscription(
            portfolio_id,
            PortfolioDailyDependencyKind.PORTFOLIO_CONFIG,
            "another-portfolio",
        )
    with pytest.raises(ValueError, match="uppercase ISO"):
        PortfolioDailyDependencySubscription.currency(portfolio_id, "usd")


def test_service_hook_uses_caller_executor_without_transaction_control() -> None:
    executor = _ExecutorWithoutTransactionControl([7])
    result = subscribe_and_invalidate(
        executor,  # type: ignore[arg-type]
        PortfolioDailyDependencySubscription.instrument("portfolio-1", "fund-1"),
        PortfolioDailyInvalidationReason(
            "instrument_dependency_added",
            {"source": "portfolio_daily_manifest_capture"},
        ),
    )
    assert result.generation == 7
    assert len(executor.calls) == 1
    parameters = executor.calls[0][1]
    assert parameters["dependency_kind"] == "instrument"
    assert parameters["dependency_key"] == "fund-1"
