from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core import MarketDataPoint
from investment_studio_instrument_core.db_models import (
    FundNavAdjustmentFactor,
    FundNavCurrentProjection,
    FundNavEvent,
    FundNavProjectionRun,
    FundNavReinvestmentEvidence,
    InstrumentMarketData,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
REGISTRY_ROOT = WORKSPACE_ROOT / "shared-data" / "instruments"
FUND_ID = "fund-nav-projection-core"
METHOD = "dividend_reinvestment/v2"
FUND_POLICY = {
    "trading": ["official_nav"],
    "valuation": ["official_nav"],
    "total_return": ["total_return_nav"],
    "chart": ["total_return_nav"],
    "reference": ["official_nav"],
}


@pytest.fixture()
def nav_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> sessionmaker[Session]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fund-nav-projection.db'}"
    monkeypatch.setenv(
        "INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(REGISTRY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REGISTRY_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    shared_store.reset_store(
        factory,
        {
            "registry_name": "Fund NAV projection core tests",
            "instruments": [
                {
                    "instrument_id": FUND_ID,
                    "instrument_name": "Projection Core Fund",
                    "instrument_type": "public_fund",
                    "currency": "CNY",
                    "quote_selection_policy": FUND_POLICY,
                    "identifiers": [
                        {
                            "identifier_type": "internal",
                            "identifier_value": "FUND-NAV-PROJECTION-CORE",
                            "is_primary": True,
                        }
                    ],
                    "market_data": [],
                }
            ],
        },
    )
    return factory


def _event_revision(
    revision_id: str,
    *,
    action_id: str = "cash-action",
    revision_number: int = 1,
    revision_kind: str = "original",
    supersedes: str | None = None,
    effective_date: str = "2026-06-30",
    cash_per_unit: str = "0.10",
    sequence_order: int | None = None,
) -> dict[str, object]:
    return {
        "fund_nav_event_id": revision_id,
        "fund_nav_action_id": action_id,
        "revision_number": revision_number,
        "revision_kind": revision_kind,
        "supersedes_fund_nav_event_id": supersedes,
        "event_type": "cash_distribution",
        "effective_date": effective_date,
        "sequence_order": sequence_order,
        "cash_per_unit": cash_per_unit,
        "evidence_kind": "provider_notice",
        "source": "fund-manager-notice",
        "external_event_id": f"notice:{revision_id}",
        "provenance": {"notice_sha256": revision_id},
        "recorded_by": "pytest",
        "revision_reason": f"record {revision_kind}",
    }


def _evidence_revision(
    evidence_id: str,
    *,
    event_id: str,
    revision_number: int = 1,
    revision_kind: str = "original",
    supersedes: str | None = None,
    reinvestment_nav: str = "1.00",
) -> dict[str, object]:
    return {
        "fund_nav_reinvestment_evidence_id": evidence_id,
        "fund_nav_event_id": event_id,
        "revision_number": revision_number,
        "revision_kind": revision_kind,
        "supersedes_fund_nav_reinvestment_evidence_id": supersedes,
        "reinvestment_nav": reinvestment_nav,
        "evidence_kind": "provider_notice",
        "source": "fund-manager-notice",
        "external_evidence_id": f"reinvestment:{evidence_id}",
        "provenance": {"notice_sha256": evidence_id},
        "recorded_by": "pytest",
        "revision_reason": f"record {revision_kind}",
    }


def _projection_run(
    source_fingerprint: str,
    *,
    status: str,
    kind: str = "event_derived",
    anchor_date: str | None = "2026-06-29",
) -> dict[str, object]:
    evidence: dict[str, object] = {"source_snapshot": source_fingerprint}
    if status == "unavailable":
        evidence["unavailable_reason"] = "reinvestment_nav_not_observed"
        anchor_date = None
    return {
        "source_observation_fingerprint": source_fingerprint,
        "projection_kind": kind,
        "projection_status": status,
        "method_version": METHOD,
        "anchor_date": anchor_date,
        "source_provider": "pytest-projection",
        "evidence": evidence,
        "created_by": "pytest",
    }


def _official_row(
    *,
    as_of_date: str = "2026-06-30",
    value: str = "1.00",
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "nav": value,
        "nav_status": "complete",
        "nav_source_provider": "pytest-unit-nav",
        "nav_lineage": {
            "kind": "provider_explicit",
            "evidence": {"source_field": "unit_nav"},
        },
        "currency": "CNY",
    }


def _derived_row(
    *,
    factor_logical_key: str,
    official_nav: str,
    total_return_nav: str,
    as_of_date: str = "2026-06-30",
    anchor_date: str = "2026-06-29",
) -> dict[str, object]:
    return {
        **_official_row(as_of_date=as_of_date, value=official_nav),
        "nav_with_dividend": total_return_nav,
        "nav_with_dividend_status": "complete",
        "nav_with_dividend_source_provider": "pytest-projection",
        "nav_with_dividend_lineage": {
            "kind": "derived_dividend_reinvestment",
            "method_version": METHOD,
            "anchor_date": anchor_date,
            "evidence": {"factor_logical_key": factor_logical_key},
        },
    }


def _event_factors(
    *,
    event_id: str,
    evidence_id: str,
    factor_level: str,
    anchor_date: str = "2026-06-29",
    event_date: str = "2026-06-30",
) -> list[dict[str, object]]:
    return [
        {
            "factor_logical_key": "anchor",
            "as_of_date": anchor_date,
            "factor_level": "1",
            "factor_kind": "event_derived",
            "evidence_kind": "zero_cash_anchor",
            "method_version": METHOD,
            "anchor_date": anchor_date,
            "source_provider": "pytest-projection",
            "evidence": {"basis": "unit_nav_at_anchor"},
        },
        {
            "factor_logical_key": f"event:{event_id}",
            "previous_factor_logical_key": "anchor",
            "as_of_date": event_date,
            "factor_level": factor_level,
            "factor_kind": "event_derived",
            "fund_nav_event_id": event_id,
            "fund_nav_reinvestment_evidence_id": evidence_id,
            "evidence_kind": "fund_nav_event",
            "method_version": METHOD,
            "anchor_date": anchor_date,
            "source_provider": "pytest-projection",
            "evidence": {"calculation": "previous*(1+cash/reinvestment_nav)"},
        },
    ]


def _publish(
    factory: sessionmaker[Session],
    *,
    rows: list[dict[str, object]],
    run: dict[str, object],
    event_ids: list[str],
    evidence_ids: list[str],
    event_revisions: list[dict[str, object]] | None = None,
    evidence_revisions: list[dict[str, object]] | None = None,
    factors: list[dict[str, object]] | None = None,
    expected_watermark: str | None = None,
) -> dict[str, object]:
    normalized_run = deepcopy(run)
    normalized_evidence = dict(normalized_run.get("evidence", {}))
    complete_unit_dates = sorted(
        str(row["as_of_date"])
        for row in rows
        if row.get("nav") is not None and row.get("nav_status") == "complete"
    )
    published_total_dates = sorted(
        str(row["as_of_date"])
        for row in rows
        if row.get("nav_with_dividend") is not None
        and row.get("nav_with_dividend_status") == "complete"
    )
    normalized_evidence.setdefault(
        "published_total_return_dates",
        published_total_dates,
    )
    normalized_evidence.setdefault(
        "missing_total_return_dates",
        sorted(set(complete_unit_dates) - set(published_total_dates)),
    )
    normalized_run["evidence"] = normalized_evidence
    result = shared_store.publish_fund_nav_history(
        factory,
        instrument_id=FUND_ID,
        rows=rows,
        projection_run=normalized_run,
        current_fund_nav_event_ids=event_ids,
        current_fund_nav_reinvestment_evidence_ids=evidence_ids,
        event_revisions=event_revisions or [],
        reinvestment_evidence_revisions=evidence_revisions or [],
        adjustment_factors=factors or [],
        expected_market_data_updated_at=expected_watermark,
        refresh_status="ready",
        updated_by="pytest",
        message="publish canonical fund NAV",
    )
    assert result is not None
    return result


def test_window_normalized_anchor_cancels_pre_window_history_without_claiming_zero_cash(
    nav_store: sessionmaker[Session],
) -> None:
    historical_event = _event_revision(
        "event-before-window",
        effective_date="2026-06-01",
    )
    published = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="window:2026-06-29",
                official_nav="1.02",
                total_return_nav="1.02",
            )
        ],
        run=_projection_run("0" * 64, status="complete"),
        event_ids=["event-before-window"],
        evidence_ids=[],
        event_revisions=[historical_event],
        factors=[
            {
                "factor_logical_key": "window:2026-06-29",
                "as_of_date": "2026-06-29",
                "factor_level": "1",
                "factor_kind": "event_derived",
                "evidence_kind": "window_normalized_anchor",
                "method_version": METHOD,
                "anchor_date": "2026-06-29",
                "source_provider": "pytest-projection",
                "evidence": {
                    "normalization_scope": "observed_return_window",
                    "starting_cash_balance": "0.50",
                },
            }
        ],
    )

    run_id = published["published_projection_run_id"]
    current_run = next(
        run
        for run in published["record"]["fund_nav_projection_runs"]
        if run["fund_nav_projection_run_id"] == run_id
    )
    assert current_run["anchor_date"] == "2026-06-29"
    factor = next(
        factor
        for factor in published["record"]["fund_nav_adjustment_factors"]
        if factor["fund_nav_projection_run_id"] == run_id
    )
    assert factor["evidence_kind"] == "window_normalized_anchor"
    assert factor["evidence"]["starting_cash_balance"] == "0.50"


def test_late_reinvestment_evidence_switches_current_run_and_exact_replay_is_noop(
    nav_store: sessionmaker[Session],
) -> None:
    event_v1 = _event_revision("event-v1")
    unavailable = _publish(
        nav_store,
        rows=[_official_row()],
        run=_projection_run("a" * 64, status="unavailable"),
        event_ids=["event-v1"],
        evidence_ids=[],
        event_revisions=[event_v1],
    )
    assert unavailable["changed"] is True
    unavailable_run_id = unavailable["published_projection_run_id"]

    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")
    complete_args = {
        "rows": [
            _derived_row(
                factor_logical_key="event:event-v1",
                official_nav="1.00",
                total_return_nav="1.10",
            )
        ],
        "run": _projection_run("b" * 64, status="complete"),
        "event_ids": ["event-v1"],
        "evidence_ids": ["evidence-v1"],
        "evidence_revisions": [evidence_v1],
        "factors": _event_factors(
            event_id="event-v1",
            evidence_id="evidence-v1",
            factor_level="1.10",
        )[::-1],
        "expected_watermark": unavailable["market_data_updated_at"],
    }
    complete = _publish(nav_store, **complete_args)
    assert complete["changed"] is True
    assert complete["dirty_from"] == "2026-06-30"
    assert complete["published_projection_run_id"] != unavailable_run_id

    replay = _publish(
        nav_store,
        **{
            **complete_args,
            "evidence_revisions": [deepcopy(evidence_v1)],
            "expected_watermark": complete["market_data_updated_at"],
        },
    )
    assert replay["changed"] is False
    assert replay["dirty_from"] is None
    assert replay["market_data_updated_at"] == complete["market_data_updated_at"]

    detail = replay["record"]
    assert detail["current_fund_nav_projection_run_id"] == complete[
        "published_projection_run_id"
    ]
    assert {run["fund_nav_projection_run_id"] for run in detail["fund_nav_projection_runs"]} == {
        unavailable_run_id,
        complete["published_projection_run_id"],
    }
    current_factor = next(
        factor
        for factor in detail["fund_nav_adjustment_factors"]
        if factor["factor_logical_key"] == "event:event-v1"
    )
    total_row = next(
        row
        for row in detail["market_data"]
        if row["quote_basis"] == "total_return_nav"
    )
    assert total_row["nav_lineage"]["evidence"]["factor_record_id"] == current_factor[
        "fund_nav_adjustment_factor_id"
    ]
    assert total_row["nav_lineage"]["evidence"]["factor_level"] == "1.1"

    with pytest.raises(ValueError, match="cannot erase the append-only"):
        shared_store.reset_store(nav_store)


def test_total_return_formula_uses_postgres_half_up_rounding(
    nav_store: sessionmaker[Session],
) -> None:
    result = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="anchor",
                official_nav="1.00000000000000005",
                total_return_nav="1.0000000000000001",
            )
        ],
        run=_projection_run("f" * 64, status="complete"),
        event_ids=[],
        evidence_ids=[],
        factors=[
            {
                "factor_logical_key": "anchor",
                "as_of_date": "2026-06-29",
                "factor_level": "1",
                "factor_kind": "event_derived",
                "evidence_kind": "zero_cash_anchor",
                "method_version": METHOD,
                "anchor_date": "2026-06-29",
                "source_provider": "pytest-projection",
                "evidence": {"basis": "unit_nav_at_anchor"},
            }
        ],
    )

    total_row = next(
        row
        for row in result["record"]["market_data"]
        if row["quote_basis"] == "total_return_nav"
    )
    assert total_row["value"] == "1.0000000000000001"


def test_event_and_evidence_revision_chains_reject_branches_and_keep_audit_history(
    nav_store: sessionmaker[Session],
) -> None:
    original = _event_revision("event-v1")
    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")
    first = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="event:event-v1",
                official_nav="1.00",
                total_return_nav="1.10",
            )
        ],
        run=_projection_run("c" * 64, status="complete"),
        event_ids=["event-v1"],
        evidence_ids=["evidence-v1"],
        event_revisions=[original],
        evidence_revisions=[evidence_v1],
        factors=_event_factors(
            event_id="event-v1",
            evidence_id="evidence-v1",
            factor_level="1.10",
        ),
    )

    correction = _event_revision(
        "event-v2",
        revision_number=2,
        revision_kind="correction",
        supersedes="event-v1",
        cash_per_unit="0.20",
    )
    evidence_v2 = _evidence_revision("evidence-v2", event_id="event-v2")
    corrected = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="event:event-v2",
                official_nav="1.00",
                total_return_nav="1.20",
            )
        ],
        run=_projection_run("d" * 64, status="complete"),
        event_ids=["event-v2"],
        evidence_ids=["evidence-v2"],
        event_revisions=[correction],
        evidence_revisions=[evidence_v2],
        factors=_event_factors(
            event_id="event-v2",
            evidence_id="evidence-v2",
            factor_level="1.20",
        ),
        expected_watermark=first["market_data_updated_at"],
    )
    assert [item["fund_nav_event_id"] for item in corrected["record"]["fund_nav_events"]] == [
        "event-v2"
    ]
    assert [
        item["fund_nav_event_id"]
        for item in corrected["record"]["fund_nav_event_revisions"]
    ] == ["event-v1", "event-v2"]

    branch = _event_revision(
        "event-v2-branch",
        revision_number=2,
        revision_kind="correction",
        supersedes="event-v1",
        cash_per_unit="0.30",
    )
    with pytest.raises(ValueError, match="revision number already belongs|cannot branch"):
        _publish(
            nav_store,
            rows=[_official_row()],
            run=_projection_run("e" * 64, status="unavailable"),
            event_ids=["event-v2"],
            evidence_ids=["evidence-v2"],
            event_revisions=[branch],
            expected_watermark=corrected["market_data_updated_at"],
        )

    evidence_correction = _evidence_revision(
        "evidence-v2-correction",
        event_id="event-v2",
        revision_number=2,
        revision_kind="correction",
        supersedes="evidence-v2",
        reinvestment_nav="0.80",
    )
    with pytest.raises(ValueError, match="factor level"):
        _publish(
            nav_store,
            rows=[
                _derived_row(
                    factor_logical_key="event:event-v2",
                    official_nav="1.00",
                    total_return_nav="1.20",
                )
            ],
            run=_projection_run("f" * 64, status="complete"),
            event_ids=["event-v2"],
            evidence_ids=["evidence-v2-correction"],
            evidence_revisions=[evidence_correction],
            factors=_event_factors(
                event_id="event-v2",
                evidence_id="evidence-v2-correction",
                factor_level="1.20",
            ),
            expected_watermark=corrected["market_data_updated_at"],
        )

    # The invalid projection and its newly appended evidence revision roll back together.
    detail = shared_store.get_instrument(nav_store, FUND_ID)
    assert detail is not None
    assert [
        item["fund_nav_reinvestment_evidence_id"]
        for item in detail["fund_nav_reinvestment_evidence_revisions"]
    ] == ["evidence-v1", "evidence-v2"]
    assert detail["current_fund_nav_projection_run_id"] == corrected[
        "published_projection_run_id"
    ]


def test_cancellations_preserve_payload_and_remove_only_current_heads(
    nav_store: sessionmaker[Session],
) -> None:
    original = _event_revision("event-v1")
    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")
    seeded = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="event:event-v1",
                official_nav="1.00",
                total_return_nav="1.10",
            )
        ],
        run=_projection_run("1" * 64, status="complete"),
        event_ids=["event-v1"],
        evidence_ids=["evidence-v1"],
        event_revisions=[original],
        evidence_revisions=[evidence_v1],
        factors=_event_factors(
            event_id="event-v1",
            evidence_id="evidence-v1",
            factor_level="1.10",
        ),
    )

    evidence_cancel = _evidence_revision(
        "evidence-v2",
        event_id="event-v1",
        revision_number=2,
        revision_kind="cancellation",
        supersedes="evidence-v1",
        reinvestment_nav="1.00",
    )
    unavailable = _publish(
        nav_store,
        rows=[_official_row()],
        run=_projection_run("2" * 64, status="unavailable"),
        event_ids=["event-v1"],
        evidence_ids=[],
        evidence_revisions=[evidence_cancel],
        expected_watermark=seeded["market_data_updated_at"],
    )
    assert unavailable["record"]["fund_nav_reinvestment_evidence"] == []
    assert len(unavailable["record"]["fund_nav_reinvestment_evidence_revisions"]) == 2

    event_cancel = _event_revision(
        "event-v2",
        revision_number=2,
        revision_kind="cancellation",
        supersedes="event-v1",
        cash_per_unit="0.10",
    )
    cancelled = _publish(
        nav_store,
        rows=[_official_row()],
        run=_projection_run("3" * 64, status="unavailable"),
        event_ids=[],
        evidence_ids=[],
        event_revisions=[event_cancel],
        expected_watermark=unavailable["market_data_updated_at"],
    )
    assert cancelled["record"]["fund_nav_events"] == []
    assert len(cancelled["record"]["fund_nav_event_revisions"]) == 2

    invalid_cancel = _event_revision(
        "event-v3",
        revision_number=3,
        revision_kind="cancellation",
        supersedes="event-v2",
        cash_per_unit="0.11",
    )
    with pytest.raises(ValueError, match="preserve its predecessor payload"):
        _publish(
            nav_store,
            rows=[_official_row()],
            run=_projection_run("4" * 64, status="unavailable"),
            event_ids=[],
            evidence_ids=[],
            event_revisions=[invalid_cancel],
            expected_watermark=cancelled["market_data_updated_at"],
        )


def test_same_day_actions_require_unique_explicit_sequence_order(
    nav_store: sessionmaker[Session],
) -> None:
    first = _event_revision("event-a", action_id="action-a")
    second = _event_revision("event-b", action_id="action-b")
    with pytest.raises(ValueError, match="unique explicit sequence_order"):
        _publish(
            nav_store,
            rows=[_official_row()],
            run=_projection_run("5" * 64, status="unavailable"),
            event_ids=["event-a", "event-b"],
            evidence_ids=[],
            event_revisions=[first, second],
        )

    detail = shared_store.get_instrument(nav_store, FUND_ID)
    assert detail is not None
    assert detail["fund_nav_event_revisions"] == []


def test_provider_total_return_requires_same_date_factor_and_exact_formula(
    nav_store: sessionmaker[Session],
) -> None:
    factor = {
        "factor_logical_key": "provider:2026-06-30",
        "as_of_date": "2026-06-30",
        "factor_level": "1.25",
        "factor_kind": "provider_implied",
        "evidence_kind": "provider_total_return",
        "method_version": METHOD,
        "anchor_date": "2026-06-30",
        "source_provider": "fund-manager-sheet",
        "evidence": {"fields": ["unit_nav", "adjusted_nav"]},
    }
    row = {
        **_official_row(value="1.20"),
        "nav_with_dividend": "1.50",
        "nav_with_dividend_status": "complete",
        "nav_with_dividend_source_provider": "fund-manager-sheet",
        "nav_with_dividend_lineage": {
            "kind": "provider_explicit",
            "evidence": {"factor_logical_key": "provider:2026-06-30"},
        },
    }
    published = _publish(
        nav_store,
        rows=[row],
        run=_projection_run(
            "6" * 64,
            status="complete",
            kind="provider_explicit",
            anchor_date="2026-06-30",
        ),
        event_ids=[],
        evidence_ids=[],
        factors=[factor],
    )
    total = next(
        item
        for item in published["record"]["market_data"]
        if item["quote_basis"] == "total_return_nav"
    )
    assert total["value"] == "1.50"
    assert total["nav_lineage"]["evidence"]["factor_level"] == "1.25"

    with pytest.raises(ValueError, match="must equal official_nav"):
        _publish(
            nav_store,
            rows=[{**row, "nav_with_dividend": "1.49"}],
            run=_projection_run(
                "7" * 64,
                status="complete",
                kind="provider_explicit",
                anchor_date="2026-06-30",
            ),
            event_ids=[],
            evidence_ids=[],
            factors=[factor],
            expected_watermark=published["market_data_updated_at"],
        )


def test_provider_factor_can_extend_to_later_unit_nav_until_the_next_action(
    nav_store: sessionmaker[Session],
) -> None:
    factor = {
        "factor_logical_key": "provider:2026-06-30",
        "as_of_date": "2026-06-30",
        "factor_level": "1.25",
        "factor_kind": "provider_implied",
        "evidence_kind": "provider_total_return",
        "method_version": METHOD,
        "anchor_date": "2026-06-30",
        "source_provider": "fund-manager-sheet",
        "evidence": {"fields": ["unit_nav", "adjusted_nav"]},
    }
    provider_row = {
        **_official_row(as_of_date="2026-06-30", value="1.20"),
        "nav_with_dividend": "1.50",
        "nav_with_dividend_status": "complete",
        "nav_with_dividend_source_provider": "fund-manager-sheet",
        "nav_with_dividend_lineage": {
            "kind": "provider_explicit",
            "evidence": {"factor_logical_key": "provider:2026-06-30"},
        },
    }
    carried_row = _derived_row(
        factor_logical_key="provider:2026-06-30",
        official_nav="1.24",
        total_return_nav="1.55",
        as_of_date="2026-07-01",
        anchor_date="2026-06-30",
    )

    published = _publish(
        nav_store,
        rows=[provider_row, carried_row],
        run=_projection_run(
            "8" * 64,
            status="complete",
            kind="provider_explicit",
            anchor_date="2026-06-30",
        ),
        event_ids=[],
        evidence_ids=[],
        factors=[factor],
    )

    total_rows = [
        row
        for row in published["record"]["market_data"]
        if row["quote_basis"] == "total_return_nav"
    ]
    assert [row["value"] for row in total_rows] == ["1.50", "1.55"]
    assert total_rows[-1]["nav_lineage"]["kind"] == (
        "derived_dividend_reinvestment"
    )
    assert total_rows[-1]["nav_lineage"]["evidence"]["factor_level"] == (
        "1.25"
    )

    uncovered_event = _event_revision(
        "event-after-provider-anchor",
        action_id="action-after-provider-anchor",
        effective_date="2026-07-01",
    )
    with pytest.raises(
        ValueError,
        match="must cover every current action",
    ):
        _publish(
            nav_store,
            rows=[provider_row, carried_row],
            run=_projection_run(
                "9" * 64,
                status="complete",
                kind="provider_explicit",
                anchor_date="2026-06-30",
            ),
            event_ids=["event-after-provider-anchor"],
            evidence_ids=[],
            event_revisions=[uncovered_event],
            factors=[factor],
            expected_watermark=published["market_data_updated_at"],
        )


def test_partial_projection_preserves_the_provable_segment_before_a_break(
    nav_store: sessionmaker[Session],
) -> None:
    first_event = _event_revision("event-v1")
    first_evidence = _evidence_revision("evidence-v1", event_id="event-v1")
    unprovable_event = _event_revision(
        "event-v2",
        action_id="cash-action-2",
        effective_date="2026-07-01",
    )

    published = _publish(
        nav_store,
        rows=[
            _derived_row(
                factor_logical_key="anchor",
                official_nav="1.00",
                total_return_nav="1.00",
                as_of_date="2026-06-29",
            ),
            _derived_row(
                factor_logical_key="event:event-v1",
                official_nav="1.00",
                total_return_nav="1.10",
            ),
            _official_row(as_of_date="2026-07-01"),
        ],
        run=_projection_run("a1" * 32, status="partial"),
        event_ids=["event-v1", "event-v2"],
        evidence_ids=["evidence-v1"],
        event_revisions=[first_event, unprovable_event],
        evidence_revisions=[first_evidence],
        factors=_event_factors(
            event_id="event-v1",
            evidence_id="evidence-v1",
            factor_level="1.10",
        ),
    )

    run = published["record"]["fund_nav_projection_runs"][0]
    assert run["projection_status"] == "partial"
    assert run["evidence"]["published_total_return_dates"] == [
        "2026-06-29",
        "2026-06-30",
    ]
    assert run["evidence"]["missing_total_return_dates"] == ["2026-07-01"]
    total_dates = {
        row["as_of_date"]
        for row in published["record"]["market_data"]
        if row["quote_basis"] == "total_return_nav"
    }
    assert total_dates == {"2026-06-29", "2026-06-30"}


def test_hybrid_projection_reanchors_from_provider_then_applies_later_event(
    nav_store: sessionmaker[Session],
) -> None:
    event_v1 = _event_revision(
        "event-v1",
        effective_date="2026-07-03",
    )
    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")
    factors = [
        {
            "factor_logical_key": "provider:2026-07-02",
            "as_of_date": "2026-07-02",
            "factor_level": "1.10",
            "factor_kind": "provider_implied",
            "evidence_kind": "provider_total_return",
            "method_version": METHOD,
            "anchor_date": "2026-07-02",
            "source_provider": "fund-manager-sheet",
            "evidence": {"fields": ["unit_nav", "adjusted_nav"]},
        },
        {
            "factor_logical_key": "event:event-v1",
            "previous_factor_logical_key": "provider:2026-07-02",
            "as_of_date": "2026-07-03",
            "factor_level": "1.21",
            "factor_kind": "event_derived",
            "fund_nav_event_id": "event-v1",
            "fund_nav_reinvestment_evidence_id": "evidence-v1",
            "evidence_kind": "fund_nav_event",
            "method_version": METHOD,
            "anchor_date": "2026-07-02",
            "source_provider": "pytest-projection",
            "evidence": {"calculation": "previous*(1+cash/reinvestment_nav)"},
        },
    ]
    provider_row = {
        **_official_row(as_of_date="2026-07-02"),
        "nav_with_dividend": "1.10",
        "nav_with_dividend_status": "complete",
        "nav_with_dividend_source_provider": "fund-manager-sheet",
        "nav_with_dividend_lineage": {
            "kind": "provider_explicit",
            "evidence": {"factor_logical_key": "provider:2026-07-02"},
        },
    }
    derived_row = _derived_row(
        factor_logical_key="event:event-v1",
        official_nav="1.00",
        total_return_nav="1.21",
        as_of_date="2026-07-03",
        anchor_date="2026-07-02",
    )

    published = _publish(
        nav_store,
        rows=[
            _official_row(as_of_date="2026-07-01"),
            provider_row,
            derived_row,
        ],
        run=_projection_run(
            "b1" * 32,
            status="partial",
            kind="hybrid_reanchored",
            anchor_date="2026-07-02",
        ),
        event_ids=["event-v1"],
        evidence_ids=["evidence-v1"],
        event_revisions=[event_v1],
        evidence_revisions=[evidence_v1],
        factors=factors,
    )

    current_run = published["record"]["fund_nav_projection_runs"][0]
    assert current_run["projection_kind"] == "hybrid_reanchored"
    assert current_run["projection_status"] == "partial"
    assert current_run["anchor_date"] == "2026-07-02"
    assert {
        factor["factor_kind"]
        for factor in published["record"]["fund_nav_adjustment_factors"]
    } == {"provider_implied", "event_derived"}


def test_derived_factor_ancestry_cannot_skip_a_current_action(
    nav_store: sessionmaker[Session],
) -> None:
    first_event = _event_revision("event-v1")
    second_event = _event_revision(
        "event-v2",
        action_id="cash-action-2",
        effective_date="2026-07-01",
    )
    first_evidence = _evidence_revision("evidence-v1", event_id="event-v1")
    second_evidence = _evidence_revision("evidence-v2", event_id="event-v2")
    skipped_chain = [
        _event_factors(
            event_id="event-v2",
            evidence_id="evidence-v2",
            factor_level="1.10",
            event_date="2026-07-01",
        )[0],
        _event_factors(
            event_id="event-v2",
            evidence_id="evidence-v2",
            factor_level="1.10",
            event_date="2026-07-01",
        )[1],
    ]

    with pytest.raises(
        IntegrityError,
        match="immutable fund NAV projection contract violation",
    ):
        _publish(
            nav_store,
            rows=[
                _derived_row(
                    factor_logical_key="event:event-v2",
                    official_nav="1.00",
                    total_return_nav="1.10",
                    as_of_date="2026-07-01",
                )
            ],
            run=_projection_run("c1" * 32, status="complete"),
            event_ids=["event-v1", "event-v2"],
            evidence_ids=["evidence-v1", "evidence-v2"],
            event_revisions=[first_event, second_event],
            evidence_revisions=[first_evidence, second_evidence],
            factors=skipped_chain,
        )


def test_projection_rejects_unused_factors_and_false_evidence_dates(
    nav_store: sessionmaker[Session],
) -> None:
    event_v1 = _event_revision("event-v1")
    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")
    with pytest.raises(ValueError, match="Every factor in the current projection"):
        _publish(
            nav_store,
            rows=[
                _derived_row(
                    factor_logical_key="anchor",
                    official_nav="1.00",
                    total_return_nav="1.00",
                    as_of_date="2026-06-29",
                ),
                _official_row(),
            ],
            run=_projection_run("d1" * 32, status="partial"),
            event_ids=["event-v1"],
            evidence_ids=["evidence-v1"],
            event_revisions=[event_v1],
            evidence_revisions=[evidence_v1],
            factors=_event_factors(
                event_id="event-v1",
                evidence_id="evidence-v1",
                factor_level="1.10",
            ),
        )

    provider_factor = {
        "factor_logical_key": "provider:2026-06-30",
        "as_of_date": "2026-06-30",
        "factor_level": "1.25",
        "factor_kind": "provider_implied",
        "evidence_kind": "provider_total_return",
        "method_version": METHOD,
        "anchor_date": "2026-06-30",
        "source_provider": "fund-manager-sheet",
        "evidence": {"fields": ["unit_nav", "adjusted_nav"]},
    }
    provider_row = {
        **_official_row(value="1.20"),
        "nav_with_dividend": "1.50",
        "nav_with_dividend_status": "complete",
        "nav_with_dividend_source_provider": "fund-manager-sheet",
        "nav_with_dividend_lineage": {
            "kind": "provider_explicit",
            "evidence": {"factor_logical_key": "provider:2026-06-30"},
        },
    }
    false_evidence_run = _projection_run(
        "e1" * 32,
        status="complete",
        kind="provider_explicit",
        anchor_date="2026-06-30",
    )
    false_evidence_run["evidence"] = {
        "published_total_return_dates": ["2026-06-29"],
        "missing_total_return_dates": [],
    }
    with pytest.raises(ValueError, match="published_total_return_dates"):
        _publish(
            nav_store,
            rows=[provider_row],
            run=false_evidence_run,
            event_ids=[],
            evidence_ids=[],
            factors=[provider_factor],
        )


def test_failed_publication_rolls_back_every_append_and_pointer_change(
    nav_store: sessionmaker[Session],
) -> None:
    initial = _publish(
        nav_store,
        rows=[_official_row()],
        run=_projection_run("8" * 64, status="unavailable"),
        event_ids=[],
        evidence_ids=[],
    )
    original_run_id = initial["published_projection_run_id"]
    event_v1 = _event_revision("event-v1")
    evidence_v1 = _evidence_revision("evidence-v1", event_id="event-v1")

    with pytest.raises(ValueError, match="must equal official_nav"):
        _publish(
            nav_store,
            rows=[
                _derived_row(
                    factor_logical_key="event:event-v1",
                    official_nav="1.00",
                    total_return_nav="1.09",
                )
            ],
            run=_projection_run("9" * 64, status="complete"),
            event_ids=["event-v1"],
            evidence_ids=["evidence-v1"],
            event_revisions=[event_v1],
            evidence_revisions=[evidence_v1],
            factors=_event_factors(
                event_id="event-v1",
                evidence_id="evidence-v1",
                factor_level="1.10",
            ),
            expected_watermark=initial["market_data_updated_at"],
        )

    with nav_store() as session:
        assert session.scalars(select(FundNavEvent)).all() == []
        assert session.scalars(select(FundNavReinvestmentEvidence)).all() == []
        assert session.scalars(select(FundNavAdjustmentFactor)).all() == []
        assert len(session.scalars(select(FundNavProjectionRun)).all()) == 1
        pointer = session.get(FundNavCurrentProjection, FUND_ID)
        assert pointer is not None
        assert pointer.fund_nav_projection_run_id == original_run_id
        rows = session.scalars(select(InstrumentMarketData)).all()
        assert [(row.quote_basis, row.value) for row in rows] == [
            ("official_nav", "1.00")
        ]


def test_reset_store_refuses_to_invent_a_projection_factor(
    nav_store: sessionmaker[Session],
) -> None:
    with pytest.raises(ValueError, match="cannot seed total_return_nav"):
        shared_store.reset_store(
            nav_store,
            {
                "instruments": [
                    {
                        "instrument_id": FUND_ID,
                        "instrument_name": "Projection Core Fund",
                        "instrument_type": "public_fund",
                        "currency": "CNY",
                        "quote_selection_policy": FUND_POLICY,
                        "market_data": [
                            {
                                "metric_family": "nav",
                                "quote_basis": "total_return_nav",
                                "as_of_date": "2026-06-30",
                                "value": "1.10",
                                "currency": "CNY",
                                "provider": "fixture",
                                "status": "complete",
                                "price_unit": "per_unit",
                                "price_scale": "1",
                                "nav_lineage": {
                                    "kind": "provider_explicit",
                                    "evidence": {
                                        "factor_logical_key": "missing-factor"
                                    },
                                },
                            }
                        ],
                    }
                ]
            },
        )


def test_unavailable_total_return_is_row_absence_not_a_placeholder_value() -> None:
    with pytest.raises(ValueError, match="represented by row absence"):
        MarketDataPoint(
            instrument_id=FUND_ID,
            metric_family="nav",
            quote_basis="total_return_nav",
            as_of_date=date(2026, 6, 30),
            value=Decimal("1.10"),
            currency="CNY",
            price_unit="per_unit",
            price_scale=Decimal("1"),
            status="unavailable",
            nav_lineage={
                "kind": "provider_explicit",
                "evidence": {"unavailable_reason": "missing_reinvestment_nav"},
            },
        )


def test_summaries_are_bounded_and_do_not_hydrate_projection_history(
    nav_store: sessionmaker[Session],
) -> None:
    _publish(
        nav_store,
        rows=[_official_row()],
        run=_projection_run("0" * 64, status="unavailable"),
        event_ids=[],
        evidence_ids=[],
    )
    summaries = shared_store.get_instrument_summaries(
        nav_store,
        [FUND_ID, "missing-fund", FUND_ID],
    )
    assert list(summaries) == [FUND_ID, "missing-fund"]
    assert summaries["missing-fund"] is None
    summary = summaries[FUND_ID]
    assert summary is not None
    assert "fund_nav_projection_runs" not in summary
    assert "fund_nav_adjustment_factors" not in summary
    assert "corporate_actions" not in summary
    assert [item["quote_basis"] for item in summary["latest_market_data"]] == [
        "official_nav"
    ]
