from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.schema import Table

from portfolio_app.calculations.portfolio_daily.published_repository import (
    PortfolioDailyCurrentPublicationChanged,
    PortfolioDailyPublishedReadIntegrityError,
    read_current_portfolio_daily_latest_summary,
    read_current_portfolio_daily_publication,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_contribution_output,
    portfolio_daily_holding_output,
    portfolio_daily_snapshot_output,
)
from portfolio_ops_calculation_core.state import CalculationRunStatus


pytestmark = pytest.mark.no_database


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        if len(self._rows) > 1:
            raise AssertionError("scripted one_or_none result has multiple rows")
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _RecordingExecutor:
    def __init__(self, scripted_rows: list[list[dict[str, object]]]) -> None:
        self._scripted_rows = list(scripted_rows)
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(statement)
        if not self._scripted_rows:
            raise AssertionError("unexpected repository statement")
        return _FakeResult(self._scripted_rows.pop(0))


def _metadata_row(
    *,
    publication_id: UUID,
    run_id: UUID,
    manifest_id: UUID,
    token: int,
    portfolio_id: str = "portfolio-exact",
) -> dict[str, object]:
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    zero = Decimal("0")
    return {
        "publication_id": publication_id,
        "run_id": run_id,
        "manifest_id": manifest_id,
        "portfolio_id": portfolio_id,
        "published_fencing_token": token,
        "canonical_output_hash": "a" * 64,
        "publication_output_schema_version": "portfolio-daily-output.v1",
        "published_at": now,
        "current_pointer_updated_at": now,
        "run_status": CalculationRunStatus.PUBLISHED,
        "requested_as_of": date(2026, 7, 14),
        "effective_as_of": date(2026, 7, 14),
        "cutoff_at": now,
        "timezone_name": "Asia/Shanghai",
        "methodology_version": "portfolio-daily.exact.v1",
        "input_schema_version": "portfolio-daily-input.v1",
        "output_schema_version": "portfolio-daily-output.v1",
        "run_published_output_hash": "a" * 64,
        "captured_generation": 3,
        "current_generation": 3,
        "pending_run_id": None,
        "pending_run_generation": None,
        "pending_run_status": None,
        "pending_intent_id": None,
        "pending_intent_generation": None,
        "pending_intent_status": None,
        "requested_by": "test",
        "run_created_at": now,
        "run_started_at": now,
        "run_completed_at": now,
        "manifest_hash": "b" * 64,
        "manifest_sealed_at": now,
        "worker_id": "worker-2",
        "calculated_at": now,
        "output_methodology_version": "portfolio-daily.exact.v1",
        "output_input_schema_version": "portfolio-daily-input.v1",
        "stored_output_schema_version": "portfolio-daily-output.v1",
        "output_canonical_output_hash": "a" * 64,
        "output_range_start": date(2026, 7, 1),
        "output_range_end": date(2026, 7, 14),
        "closure_status": "passed",
        "snapshot_count": 14,
        "measured_nav_count": 14,
        "holding_count": 1,
        "balance_count": 1,
        "lot_count": 0,
        "lot_disposition_count": 0,
        "contribution_count": 0,
        "unavailable_component_count": 0,
        "ledger_balance_residual_exact": zero,
        "nav_bridge_residual_exact": zero,
        "pnl_residual_exact": zero,
        "twr_residual_exact": zero,
        "lot_residual_exact": zero,
        "rounding_adjustment_base": zero,
        "coverage_state": "complete",
        "reason_codes": [],
    }


def _balance_row(
    *,
    run_id: UUID,
    token: int,
    portfolio_id: str = "portfolio-exact",
    local_amount: object = Decimal("123.45000000"),
) -> dict[str, object]:
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    return {
        "run_id": run_id,
        "output_fencing_token": token,
        "worker_id": "worker-2",
        "portfolio_id": portfolio_id,
        "calculated_at": now,
        "as_of_date": date(2026, 7, 14),
        "account_id": "cash-account",
        "component_type": "settled_cash",
        "component_key": "USD",
        "currency": "USD",
        "measured_base_amount": True,
        "local_amount": local_amount,
        "adopted_fx_rate_exact": Decimal("1"),
        "fx_rate_to_base": Decimal("1.000000000000000000"),
        "base_amount_exact": Decimal("123.45000000"),
        "base_amount": Decimal("123.45000000"),
        "base_rounding_adjustment": Decimal("0.00000000"),
        "coverage_state": "complete",
        "reason_codes": [],
    }


def _output_row(
    table: Table,
    *,
    run_id: UUID,
    token: int,
    portfolio_id: str = "portfolio-exact",
    **overrides: object,
) -> dict[str, object]:
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    values: dict[str, object] = {}
    for column in table.c:
        if column.nullable:
            values[column.name] = None
            continue
        python_type = column.type.python_type
        if python_type is bool:
            value: object = False
        elif python_type is int:
            value = 0
        elif python_type is Decimal:
            value = Decimal("0")
        elif python_type is str:
            value = "test"
        elif python_type is date:
            value = date(2026, 7, 14)
        elif python_type is datetime:
            value = now
        elif python_type is list:
            value = []
        elif python_type is UUID:
            value = uuid4()
        else:  # pragma: no cover - fails loudly if the table contract expands.
            raise AssertionError(
                f"unsupported output test type {python_type!r} for {column.name}"
            )
        values[column.name] = value
    values.update(
        {
            "run_id": run_id,
            "output_fencing_token": token,
            "worker_id": "worker-2",
            "portfolio_id": portfolio_id,
            "calculated_at": now,
            **overrides,
        }
    )
    return values


def _confirmation(
    *, publication_id: UUID, run_id: UUID, token: int
) -> dict[str, object]:
    return {
        "publication_id": publication_id,
        "run_id": run_id,
        "published_fencing_token": token,
    }


def _script(
    metadata: dict[str, object],
    *,
    snapshots: list[dict[str, object]] | None = None,
    balances: list[dict[str, object]] | None = None,
    contributions: list[dict[str, object]] | None = None,
    confirmation: dict[str, object] | None = None,
) -> list[list[dict[str, object]]]:
    # metadata; snapshot; holding; balance; lot; disposition; contribution;
    # final pointer confirmation.
    return [
        [metadata],
        snapshots or [],
        [],
        balances or [],
        [],
        [],
        contributions or [],
        [
            confirmation
            or _confirmation(
                publication_id=metadata["publication_id"],  # type: ignore[arg-type]
                run_id=metadata["run_id"],  # type: ignore[arg-type]
                token=metadata["published_fencing_token"],  # type: ignore[arg-type]
            )
        ],
    ]


def test_current_read_uses_pointer_run_and_fencing_token_for_every_output() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    manifest_id = uuid4()
    token = 7
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=manifest_id,
        token=token,
    )
    executor = _RecordingExecutor(
        _script(
            metadata,
            balances=[_balance_row(run_id=run_id, token=token)],
        )
    )

    result = read_current_portfolio_daily_publication(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
        range_start=date(2026, 7, 10),
        range_end=date(2026, 7, 14),
    )

    assert result is not None
    assert result.metadata.publication_id == publication_id
    assert result.metadata.run_id == run_id
    assert result.metadata.published_fencing_token == token
    assert result.metadata.reason_codes == ()
    assert len(result.balances) == 1
    assert result.balances[0].local_amount == Decimal("123.45000000")
    assert result.balances[0].reason_codes == ()

    dialect = postgresql.dialect()
    output_statements = executor.statements[1:7]
    assert len(output_statements) == 6
    for statement in output_statements:
        compiled = statement.compile(dialect=dialect)
        sql = str(compiled)
        assert "calculation_registry.calculation_current_publication" in sql
        assert "calculation_registry.calculation_publication" in sql
        assert "published_fencing_token" in sql
        assert "calculation_current_publication.publication_id =" in sql
        assert "calculation_publication.run_id =" in sql
        assert (
            "output_fencing_token = "
            "calculation_registry.calculation_publication.published_fencing_token"
            in sql
        )
        assert publication_id in compiled.params.values()
        assert run_id in compiled.params.values()
        assert token in compiled.params.values()


def test_latest_summary_counts_distinct_instruments_only_on_latest_date() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 23
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    snapshot = _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
    )
    holdings = [
        _output_row(
            portfolio_daily_holding_output,
            run_id=run_id,
            token=token,
            account_id="account-a",
            instrument_id="instrument-a",
        ),
        _output_row(
            portfolio_daily_holding_output,
            run_id=run_id,
            token=token,
            account_id="account-b",
            instrument_id="instrument-a",
        ),
        _output_row(
            portfolio_daily_holding_output,
            run_id=run_id,
            token=token,
            account_id="account-a",
            instrument_id="instrument-b",
        ),
    ]
    executor = _RecordingExecutor(
        [
            [metadata],
            [snapshot],
            holdings,
            [_confirmation(publication_id=publication_id, run_id=run_id, token=token)],
        ]
    )

    result = read_current_portfolio_daily_latest_summary(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
    )

    assert result is not None
    assert result.instrument_count == 2
    assert result.metadata.holding_count == 1
    assert len(executor.statements) == 4


def test_current_read_preserves_unmeasured_flow_nulls_and_coverage_evidence() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 8
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    snapshot = _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        base_currency="CNY",
        measured_external_flows=False,
        external_flow_in=None,
        external_flow_out=None,
        flow_coverage_state="unavailable",
        flow_reason_codes=["fx_path_unavailable"],
    )
    contribution = _output_row(
        portfolio_daily_contribution_output,
        run_id=run_id,
        token=token,
        axis="taxonomy",
        group_key="instrument-1",
        group_label="Instrument One",
        measured=False,
        external_flow_in=None,
        external_flow_out=None,
        internal_flow_in=None,
        internal_flow_out=None,
        coverage_state="unavailable",
        reason_codes=["instrument_group_inputs_unavailable"],
    )
    executor = _RecordingExecutor(
        _script(
            metadata,
            snapshots=[snapshot],
            contributions=[contribution],
        )
    )

    result = read_current_portfolio_daily_publication(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
    )

    assert result is not None
    assert len(result.snapshots) == 1
    read_snapshot = result.snapshots[0]
    assert read_snapshot.measured_external_flows is False
    assert read_snapshot.external_flow_in is None
    assert read_snapshot.external_flow_out is None
    assert read_snapshot.flow_coverage_state == "unavailable"
    assert read_snapshot.flow_reason_codes == ("fx_path_unavailable",)
    assert len(result.contributions) == 1
    read_contribution = result.contributions[0]
    assert read_contribution.external_flow_in is None
    assert read_contribution.external_flow_out is None
    assert read_contribution.internal_flow_in is None
    assert read_contribution.internal_flow_out is None
    assert read_contribution.reason_codes == (
        "instrument_group_inputs_unavailable",
    )


def test_current_read_preserves_bounded_method50_numeric() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 19
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    cumulative_method50 = Decimal(
        "0.033333333333333333333333333333333333333333333333333"
    )
    wealth_method50 = Decimal(
        "1.0333333333333333333333333333333333333333333333333"
    )
    snapshot = _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        cumulative_twr_method50=cumulative_method50,
        cumulative_twr_published=Decimal("0.033333333333333333"),
        wealth_index_method50=wealth_method50,
        wealth_index_published=Decimal("1.033333333333333333"),
        peak_wealth_index_method50=wealth_method50,
        peak_wealth_index_published=Decimal("1.033333333333333333"),
        drawdown_method50=Decimal("0"),
        drawdown_published=Decimal("0"),
    )
    executor = _RecordingExecutor(_script(metadata, snapshots=[snapshot]))

    result = read_current_portfolio_daily_publication(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
    )

    assert result is not None
    read_snapshot = result.snapshots[0]
    assert read_snapshot.cumulative_twr_method50 == cumulative_method50
    assert read_snapshot.wealth_index_method50 == wealth_method50
    assert read_snapshot.peak_wealth_index_method50 == wealth_method50


def test_current_read_rejects_non_decimal_method_storage() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 20
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    snapshot = _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        wealth_index_method50="1.0",
    )
    executor = _RecordingExecutor(_script(metadata, snapshots=[snapshot]))

    with pytest.raises(
        PortfolioDailyPublishedReadIntegrityError,
        match="must be returned as Decimal",
    ):
        read_current_portfolio_daily_publication(
            executor,  # type: ignore[arg-type]
            portfolio_id="portfolio-exact",
            range_start=date(2026, 7, 14),
            range_end=date(2026, 7, 14),
        )


def test_current_read_rejects_pointer_switch_instead_of_mixing_attempts() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=2,
    )
    replacement_publication_id = uuid4()
    executor = _RecordingExecutor(
        _script(
            metadata,
            confirmation=_confirmation(
                publication_id=replacement_publication_id,
                run_id=uuid4(),
                token=1,
            ),
        )
    )

    with pytest.raises(PortfolioDailyCurrentPublicationChanged) as raised:
        read_current_portfolio_daily_publication(
            executor,  # type: ignore[arg-type]
            portfolio_id="portfolio-exact",
            range_start=date(2026, 7, 14),
            range_end=date(2026, 7, 14),
        )

    assert raised.value.expected_publication_id == publication_id
    assert raised.value.actual_publication_id == replacement_publication_id


def test_published_numeric_rows_never_accept_float_coercion() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=4,
    )
    executor = _RecordingExecutor(
        _script(
            metadata,
            balances=[
                _balance_row(run_id=run_id, token=4, local_amount=123.45)
            ],
        )
    )

    with pytest.raises(
        PortfolioDailyPublishedReadIntegrityError,
        match="local_amount must be returned as Decimal",
    ):
        read_current_portfolio_daily_publication(
            executor,  # type: ignore[arg-type]
            portfolio_id="portfolio-exact",
            range_start=date(2026, 7, 14),
            range_end=date(2026, 7, 14),
        )


def test_no_current_publication_is_none_without_fallback_materialization() -> None:
    executor = _RecordingExecutor([[]])

    assert (
        read_current_portfolio_daily_publication(
            executor,  # type: ignore[arg-type]
            portfolio_id="portfolio-exact",
            range_start=date(2026, 7, 1),
            range_end=date(2026, 7, 14),
        )
        is None
    )
    assert len(executor.statements) == 1


def test_selective_read_executes_only_requested_output_table_and_confirmation() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    token = 41
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=token,
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [_balance_row(run_id=run_id, token=token)],
            [_confirmation(publication_id=publication_id, run_id=run_id, token=token)],
        ]
    )

    result = read_current_portfolio_daily_publication(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
        tables=("balances",),
    )

    assert result is not None
    assert len(result.balances) == 1
    assert result.snapshots == ()
    assert result.holdings == ()
    assert result.lots == ()
    assert len(executor.statements) == 3
    output_sql = str(executor.statements[1].compile(dialect=postgresql.dialect()))
    assert "portfolio_daily_balance_output" in output_sql
    assert "portfolio_daily_snapshot_output" not in output_sql
    assert "portfolio_daily_holding_output" not in output_sql


def test_metadata_exposes_newer_generation_run_and_intent_without_mixing_outputs() -> None:
    publication_id = uuid4()
    run_id = uuid4()
    pending_run_id = uuid4()
    pending_intent_id = uuid4()
    metadata = _metadata_row(
        publication_id=publication_id,
        run_id=run_id,
        manifest_id=uuid4(),
        token=42,
    )
    metadata.update(
        {
            "current_generation": 5,
            "pending_run_id": pending_run_id,
            "pending_run_generation": 5,
            "pending_run_status": CalculationRunStatus.RUNNING,
            "pending_intent_id": pending_intent_id,
            "pending_intent_generation": 5,
            "pending_intent_status": "pending",
        }
    )
    executor = _RecordingExecutor(
        [
            [metadata],
            [_confirmation(publication_id=publication_id, run_id=run_id, token=42)],
        ]
    )

    result = read_current_portfolio_daily_publication(
        executor,  # type: ignore[arg-type]
        portfolio_id="portfolio-exact",
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
        tables=(),
    )

    assert result is not None
    assert result.metadata.stale is True
    assert result.metadata.pending is True
    assert result.metadata.current_generation == 5
    assert result.metadata.pending_generation == 5
    assert result.metadata.pending_run_id == pending_run_id
    assert result.metadata.pending_run_status is CalculationRunStatus.RUNNING
    assert result.metadata.pending_intent_id == pending_intent_id
    assert result.metadata.pending_intent_status == "pending"


@pytest.mark.parametrize(
    ("portfolio_id", "range_start", "range_end", "error"),
    [
        (" portfolio-exact", date(2026, 7, 1), date(2026, 7, 2), ValueError),
        ("", date(2026, 7, 1), date(2026, 7, 2), ValueError),
        ("portfolio-exact", date(2026, 7, 2), date(2026, 7, 1), ValueError),
        (
            "portfolio-exact",
            datetime(2026, 7, 1, tzinfo=UTC),
            date(2026, 7, 2),
            TypeError,
        ),
    ],
)
def test_invalid_read_identity_is_rejected_before_sql(
    portfolio_id: str,
    range_start: date,
    range_end: date,
    error: type[Exception],
) -> None:
    executor = _RecordingExecutor([])
    with pytest.raises(error):
        read_current_portfolio_daily_publication(
            executor,  # type: ignore[arg-type]
            portfolio_id=portfolio_id,
            range_start=range_start,
            range_end=range_end,
        )
    assert not executor.statements
