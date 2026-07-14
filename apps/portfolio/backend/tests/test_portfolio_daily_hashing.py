from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from portfolio_app.calculations.portfolio_daily.hashing import (
    CanonicalHashContractError,
    canonical_financial_output_hash,
    canonical_manifest_hash,
)


pytestmark = pytest.mark.no_database


def _manifest_rows(*, cutoff_hour: int = 8):
    return {
        "portfolio_daily_config_input": (
            ("portfolio_id",),
            [
                {
                    "manifest_id": uuid4(),
                    "run_id": uuid4(),
                    "captured_at": datetime.now(UTC),
                    "portfolio_id": "portfolio-a",
                    "knowledge_cutoff_at": datetime(
                        2026, 7, 14, cutoff_hour, tzinfo=UTC
                    ),
                    "base_currency": "USD",
                }
            ],
        ),
        "portfolio_daily_transaction_input": (
            ("transaction_id",),
            [
                {
                    "manifest_id": uuid4(),
                    "run_id": uuid4(),
                    "captured_at": datetime.now(UTC),
                    "transaction_id": "txn-2",
                    "gross_amount": Decimal("0.10"),
                },
                {
                    "manifest_id": uuid4(),
                    "run_id": uuid4(),
                    "captured_at": datetime.now(UTC),
                    "transaction_id": "txn-1",
                    "gross_amount": Decimal("0.20"),
                },
            ],
        ),
    }


def test_manifest_hash_is_retry_and_row_order_stable_but_cutoff_sensitive() -> None:
    first = _manifest_rows()
    second = _manifest_rows()
    second["portfolio_daily_transaction_input"] = (
        ("transaction_id",),
        list(reversed(list(second["portfolio_daily_transaction_input"][1]))),
    )

    first_hash = canonical_manifest_hash(
        first,
        input_schema_version="portfolio-daily-input.v1",
    )
    assert first_hash == canonical_manifest_hash(
        second,
        input_schema_version="portfolio-daily-input.v1",
    )
    assert first_hash != canonical_manifest_hash(
        _manifest_rows(cutoff_hour=9),
        input_schema_version="portfolio-daily-input.v1",
    )


def test_manifest_hash_binds_quote_and_fx_ingestion_evidence() -> None:
    observed_at = datetime(2026, 7, 14, 8, tzinfo=UTC)

    def rows(
        *,
        quote_state: str = "observed",
        quote_ingested_at: datetime = observed_at,
        fx_state: str = "observed",
        fx_ingested_at: datetime = observed_at,
    ):
        return {
            "portfolio_daily_quote_candidate": (
                ("quote_window_id", "candidate_rank"),
                [
                    {
                        "manifest_id": uuid4(),
                        "run_id": uuid4(),
                        "captured_at": datetime.now(UTC),
                        "quote_window_id": "quote-window-a",
                        "candidate_rank": 1,
                        "revision_id": "quote-revision-a",
                        "ingested_at": quote_ingested_at,
                        "ingestion_time_state": quote_state,
                        "payload_hash": "sha256:" + ("a" * 64),
                    }
                ],
            ),
            "portfolio_daily_fx_leg": (
                ("fx_path_id", "leg_order"),
                [
                    {
                        "manifest_id": uuid4(),
                        "run_id": uuid4(),
                        "captured_at": datetime.now(UTC),
                        "fx_path_id": "fx-path-a",
                        "leg_order": 1,
                        "revision_id": "fx-revision-a",
                        "ingested_at": fx_ingested_at,
                        "ingestion_time_state": fx_state,
                        "payload_hash": "sha256:" + ("b" * 64),
                    }
                ],
            ),
        }

    def digest(**changes) -> str:
        return canonical_manifest_hash(
            rows(**changes),
            input_schema_version="portfolio-daily-input.v1",
        )

    baseline = digest()
    one_second_later = datetime(2026, 7, 14, 8, 0, 1, tzinfo=UTC)
    assert baseline != digest(quote_state="legacy_series_upper_bound")
    assert baseline != digest(quote_ingested_at=one_second_later)
    assert baseline != digest(fx_state="legacy_instrument_upper_bound")
    assert baseline != digest(fx_ingested_at=one_second_later)


def test_output_hash_excludes_attempt_identity_but_not_financial_values() -> None:
    def rows(token: int, nav: str):
        return {
            "portfolio_daily_snapshot_output": (
                ("portfolio_id", "as_of_date"),
                [
                    {
                        "run_id": uuid4(),
                        "output_fencing_token": token,
                        "worker_id": f"worker-{token}",
                        "calculated_at": datetime.now(UTC),
                        "portfolio_id": "portfolio-a",
                        "as_of_date": "2026-07-14",
                        "closing_nav": Decimal(nav),
                    }
                ],
            )
        }

    first = canonical_financial_output_hash(
        rows(1, "100.00"),
        methodology_version="portfolio-daily.exact.v1",
        output_schema_version="portfolio-daily-output.v1",
    )
    assert first == canonical_financial_output_hash(
        rows(2, "100.0"),
        methodology_version="portfolio-daily.exact.v1",
        output_schema_version="portfolio-daily-output.v1",
    )
    assert first != canonical_financial_output_hash(
        rows(2, "100.01"),
        methodology_version="portfolio-daily.exact.v1",
        output_schema_version="portfolio-daily-output.v1",
    )


def test_hashing_fails_closed_when_natural_key_is_missing() -> None:
    with pytest.raises(CanonicalHashContractError, match="transaction_id"):
        canonical_manifest_hash(
            {"transactions": (("transaction_id",), [{}])},
            input_schema_version="portfolio-daily-input.v1",
        )
