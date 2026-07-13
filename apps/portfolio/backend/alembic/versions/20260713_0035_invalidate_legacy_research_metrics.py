"""Invalidate unversioned Research backtest metrics.

Revision ID: 20260713_0035
Revises: 20260713_0034

Legacy Research runs annualized sub-year returns and calculated Sharpe from
that geometric return.  Those values cannot be relabelled as the new
history-gated, arithmetic-mean risk methodology.  Preserve the run inputs and
backtest paths, but remove the superseded metrics and the unversioned run's
artifact references.  Users can rerun the retained request under the current
methodology.
"""

from __future__ import annotations

from copy import deepcopy
import json

from alembic import op
import sqlalchemy as sa


revision = "20260713_0035"
down_revision = "20260713_0034"
branch_labels = None
depends_on = None


_INVALIDATION_REASON = "legacy_research_backtest_metrics_invalidated_v2"


def _json_value(value: object) -> object:
    if not isinstance(value, str):
        return deepcopy(value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _append_reason(container: dict[str, object]) -> None:
    warnings = [
        str(item)
        for item in list(container.get("warnings") or [])
        if str(item).strip()
    ]
    if _INVALIDATION_REASON not in warnings:
        warnings.append(_INVALIDATION_REASON)
    container["warnings"] = warnings


def _unversioned_metrics(value: object) -> bool:
    return isinstance(value, dict) and not str(
        value.get("method_version") or ""
    ).strip()


def _invalidate_detail(value: object) -> tuple[object, bool]:
    detail = _json_value(value)
    if not isinstance(detail, dict):
        return detail, False

    changed = False
    backtest = detail.get("backtest")
    if isinstance(backtest, dict) and _unversioned_metrics(backtest.get("metrics")):
        backtest["metrics"] = None
        _append_reason(backtest)
        changed = True

    benchmark = detail.get("backtest_benchmark")
    if isinstance(benchmark, dict) and _unversioned_metrics(benchmark.get("metrics")):
        benchmark["metrics"] = None
        _append_reason(benchmark)
        changed = True

    if _unversioned_metrics(detail.get("backtest_relative_metrics")):
        detail["backtest_relative_metrics"] = None
        if isinstance(backtest, dict):
            _append_reason(backtest)
        changed = True
    return detail, changed


def upgrade() -> None:
    connection = op.get_bind()
    research_runs = sa.table(
        "research_run_record",
        sa.column("research_run_id", sa.String()),
        sa.column("detail_json", sa.JSON()),
        sa.column("artifacts_json", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(
            research_runs.c.research_run_id,
            research_runs.c.detail_json,
            research_runs.c.artifacts_json,
        )
    ).mappings()
    for row in rows:
        detail, changed = _invalidate_detail(row["detail_json"])
        if not changed:
            continue
        connection.execute(
            research_runs.update()
            .where(
                research_runs.c.research_run_id == row["research_run_id"]
            )
            .values(
                detail_json=detail,
                artifacts_json=[],
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0035 is intentionally irreversible: superseded Research "
        "backtest metrics cannot be reconstructed safely."
    )
