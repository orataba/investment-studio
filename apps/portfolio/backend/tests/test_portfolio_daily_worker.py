from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError

from portfolio_app.calculations.portfolio_daily.output_repository import (
    PortfolioDailyOutputError,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    ValuationReasonCode,
)
from portfolio_app.calculations.portfolio_daily.valuation_engine import (
    ValuationClosureError,
)
from portfolio_app.calculations.portfolio_daily.worker import (
    _classify_attempt_failure,
    _registration,
    _retryable_database_error,
)


pytestmark = pytest.mark.no_database


class _OriginalDatabaseError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _database_error(sqlstate: str) -> DBAPIError:
    return DBAPIError(
        "statement",
        {},
        _OriginalDatabaseError(sqlstate),
        False,
    )


@pytest.mark.parametrize("sqlstate", ("23503", "23514", "23502", "22003"))
def test_data_and_integrity_sqlstates_are_deterministic(sqlstate: str) -> None:
    error = _database_error(sqlstate)

    failure = _classify_attempt_failure(error)

    assert failure.deterministic
    assert failure.code in {
        "database_data_contract_failed",
        "database_integrity_contract_failed",
    }
    assert not _retryable_database_error(error)


@pytest.mark.parametrize("sqlstate", ("40001", "40P01"))
def test_serialization_and_deadlock_sqlstates_remain_transient(
    sqlstate: str,
) -> None:
    error = _database_error(sqlstate)

    failure = _classify_attempt_failure(error)

    assert not failure.deterministic
    assert failure.code == "database_transient_failure"
    assert _retryable_database_error(error)


def test_known_nontransient_database_failure_is_not_blindly_retried() -> None:
    error = _database_error("42P01")

    failure = _classify_attempt_failure(error)

    assert failure.deterministic
    assert failure.code == "database_nontransient_failure"
    assert not _retryable_database_error(error)


def test_valuation_closure_preserves_its_typed_reason_code() -> None:
    error = ValuationClosureError(
        as_of_date=date(2026, 7, 14),
        kind="book_pnl",
        residual=Decimal("0.000000000000000001"),
    )

    failure = _classify_attempt_failure(error)

    assert failure.deterministic
    assert failure.code == ValuationReasonCode.BOOK_PNL_CLOSURE_FAILED.value


def test_output_repository_contract_failure_is_not_retried() -> None:
    failure = _classify_attempt_failure(
        PortfolioDailyOutputError("output row contract failed")
    )

    assert failure.deterministic
    assert failure.code == "output_persistence_contract_failed"


def test_long_operator_prefix_never_truncates_unique_instance_suffix() -> None:
    first = _registration("operator-" * 40)
    second = _registration("operator-" * 40)

    assert len(first.worker_id) <= 255
    assert len(first.worker_id.rsplit(":", 1)[-1]) == 12
    assert first.worker_id != second.worker_id
