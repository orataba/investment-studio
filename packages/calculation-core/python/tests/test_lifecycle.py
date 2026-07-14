from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from portfolio_ops_calculation_core.lifecycle import (
    CalculationScope,
    CapturingRun,
    InvalidLifecycleArgument,
    ManifestSeal,
    RunRequest,
    build_recompute_intent_dedupe_key,
)


def _request() -> RunRequest:
    return RunRequest(
        scope=CalculationScope("portfolio_daily", "portfolio", "portfolio-1"),
        requested_as_of=date(2026, 7, 14),
        effective_as_of=date(2026, 7, 14),
        timezone_name="Asia/Shanghai",
        methodology_version="portfolio-daily.v1",
        input_schema_version="portfolio-daily-input.v1",
        output_schema_version="portfolio-daily-output.v1",
        captured_generation=7,
        requested_by="fund-manager",
    )


def test_run_dedupe_is_deterministic_and_excludes_requester_identity() -> None:
    request = _request()
    assert request.dedupe_key == replace(request, requested_by="worker").dedupe_key
    first = CapturingRun(
        uuid4(),
        uuid4(),
        request,
        request.dedupe_key,
        datetime(2026, 7, 14, 8, tzinfo=timezone.utc),
    )
    second = replace(
        first,
        cutoff_at=datetime(2026, 7, 14, 9, tzinfo=timezone.utc),
    )
    assert first.dedupe_key == second.dedupe_key
    assert request.dedupe_key != replace(
        request,
        output_schema_version="portfolio-daily-output.v2",
    ).dedupe_key
    assert len(request.dedupe_key) == 64


def test_intent_dedupe_is_scope_and_generation_specific() -> None:
    scope = CalculationScope("portfolio_daily", "portfolio", "portfolio-1")
    assert build_recompute_intent_dedupe_key(scope, 8) == (
        build_recompute_intent_dedupe_key(scope, 8)
    )
    assert build_recompute_intent_dedupe_key(scope, 8) != (
        build_recompute_intent_dedupe_key(scope, 9)
    )


def test_manifest_counts_reject_bool_and_fractional_values() -> None:
    with pytest.raises(InvalidLifecycleArgument):
        ManifestSeal("a" * 64, {"transaction": True})
    with pytest.raises(InvalidLifecycleArgument):
        ManifestSeal("a" * 64, {"transaction": 1.5})  # type: ignore[dict-item]


def test_scope_and_time_contracts_are_strict() -> None:
    with pytest.raises(InvalidLifecycleArgument):
        CalculationScope(" portfolio_daily", "portfolio", "portfolio-1")
    with pytest.raises(InvalidLifecycleArgument):
        replace(_request(), timezone_name="Not/A_Timezone")
