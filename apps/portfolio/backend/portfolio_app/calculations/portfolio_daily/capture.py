"""One-transaction Portfolio Daily manifest capture and queue transition."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.capture_common import (
    CommonCaptureResult,
    ManifestCaptureError,
    PortfolioDailyCapturePolicy,
    capture_common_dependencies,
)
from portfolio_app.calculations.portfolio_daily.capture_fx import (
    capture_fx_dependencies,
)
from portfolio_app.calculations.portfolio_daily.capture_quotes import (
    capture_quote_dependencies,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    DEPENDENCY_TABLES,
    portfolio_daily_prior_publication,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    ManifestDependencies,
    build_manifest_seal,
    insert_manifest_dependencies,
)
from portfolio_ops_calculation_core import (
    CapturingRun,
    LifecycleRepository,
    ManifestSeal,
    QueuedJob,
)


@dataclass(frozen=True, slots=True)
class CapturedPortfolioDailyRun:
    capturing: CapturingRun
    seal: ManifestSeal
    queued_job: QueuedJob


def _assert_capture_transaction(
    session: Session,
    *,
    capturing: CapturingRun,
) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise ManifestCaptureError(
            "Portfolio Daily manifest capture requires PostgreSQL"
        )
    isolation = str(
        session.execute(text("SHOW transaction_isolation")).scalar_one()
    ).lower()
    if isolation != "repeatable read":
        raise ManifestCaptureError(
            "Portfolio Daily manifest capture requires REPEATABLE READ"
        )
    transaction_time = session.execute(
        text("SELECT transaction_timestamp()")
    ).scalar_one()
    if transaction_time != capturing.cutoff_at:
        raise ManifestCaptureError(
            "run knowledge cutoff differs from the capture transaction timestamp"
        )


def _merge_dependency_rows(
    *captures: dict[str, list[dict[str, object]]],
) -> ManifestDependencies:
    rows_by_table: dict[str, list[dict[str, object]]] = {}
    for capture in captures:
        overlap = set(rows_by_table).intersection(capture)
        if overlap:
            raise ManifestCaptureError(
                "dependency capture table ownership overlaps: "
                + ", ".join(sorted(overlap))
            )
        rows_by_table.update(capture)
    rows_by_table[portfolio_daily_prior_publication.name] = []
    expected = {table.name for table in DEPENDENCY_TABLES}
    if set(rows_by_table) != expected:
        raise ManifestCaptureError(
            "dependency capture is incomplete: missing="
            f"{sorted(expected - set(rows_by_table))}, unexpected="
            f"{sorted(set(rows_by_table) - expected)}"
        )
    return ManifestDependencies(rows_by_table)


def capture_portfolio_daily_dependencies(
    session: Session,
    *,
    capturing: CapturingRun,
    policy: PortfolioDailyCapturePolicy,
) -> tuple[CommonCaptureResult, ManifestDependencies]:
    """Freeze every typed dependency without committing the caller's UoW."""

    _assert_capture_transaction(session, capturing=capturing)
    common = capture_common_dependencies(
        session,
        capturing=capturing,
        policy=policy,
    )
    quote_rows = capture_quote_dependencies(session, common=common)
    fx_rows = capture_fx_dependencies(session, common=common)
    dependencies = _merge_dependency_rows(
        dict(common.rows_by_table),
        quote_rows,
        fx_rows,
    )
    return common, dependencies


def persist_seal_and_queue_portfolio_daily(
    session: Session,
    *,
    capturing: CapturingRun,
    policy: PortfolioDailyCapturePolicy,
    max_attempts: int,
) -> CapturedPortfolioDailyRun:
    """Capture, seal, and queue inside the caller's one open transaction."""

    _, dependencies = capture_portfolio_daily_dependencies(
        session,
        capturing=capturing,
        policy=policy,
    )
    insert_manifest_dependencies(
        session,
        manifest_id=capturing.manifest_id,
        run_id=capturing.run_id,
        dependencies=dependencies,
    )
    seal = build_manifest_seal(session, manifest_id=capturing.manifest_id)
    queued_job = LifecycleRepository.seal_manifest_and_queue_run(
        session,
        capturing,
        seal,
        max_attempts=max_attempts,
    )
    return CapturedPortfolioDailyRun(
        capturing=capturing,
        seal=seal,
        queued_job=queued_job,
    )


__all__ = [
    "CapturedPortfolioDailyRun",
    "capture_portfolio_daily_dependencies",
    "persist_seal_and_queue_portfolio_daily",
]
