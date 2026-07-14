"""Verified read boundary for a sealed Portfolio Daily input manifest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Mapping, cast
from uuid import UUID

from sqlalchemy import Table, and_, select
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
    SCOPE_KIND,
)
from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.hashing import canonical_manifest_hash
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    DEPENDENCY_NATURAL_KEYS,
    ManifestDependencies,
    read_manifest_dependencies,
)
from portfolio_ops_calculation_core.models import (
    CalculationInputManifest,
    CalculationRun,
)
from portfolio_ops_calculation_core.state import (
    CalculationManifestStatus,
    CalculationRunStatus,
)


class SealedManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SealedPortfolioDailyManifest:
    run_id: UUID
    manifest_id: UUID
    portfolio_id: str
    effective_as_of: date
    cutoff_at: datetime
    captured_generation: int
    canonical_manifest_hash: str
    dependency_counts: Mapping[str, int]
    dependencies: ManifestDependencies


_run = cast(Table, CalculationRun.__table__)
_manifest = cast(Table, CalculationInputManifest.__table__)


def _validated_counts(value: object) -> Mapping[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str)
        or not key
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        for key, count in value.items()
    ):
        raise SealedManifestError("manifest dependency counts are invalid")
    return MappingProxyType(dict(sorted(value.items())))


def load_sealed_portfolio_daily_manifest(
    session: Session,
    *,
    run_id: UUID,
) -> SealedPortfolioDailyManifest:
    """Read and independently re-hash only the immutable typed input tables."""

    row = session.execute(
        select(
            _run.c.run_id,
            _run.c.scope_id,
            _run.c.status.label("run_status"),
            _run.c.effective_as_of,
            _run.c.cutoff_at,
            _run.c.methodology_version,
            _run.c.input_schema_version,
            _run.c.output_schema_version,
            _run.c.captured_generation.label("run_generation"),
            _manifest.c.manifest_id,
            _manifest.c.status.label("manifest_status"),
            _manifest.c.schema_version,
            _manifest.c.captured_generation.label("manifest_generation"),
            _manifest.c.canonical_manifest_hash,
            _manifest.c.dependency_counts,
        )
        .select_from(
            _run.join(
                _manifest,
                and_(
                    _manifest.c.run_id == _run.c.run_id,
                    _manifest.c.manifest_id == _run.c.manifest_id,
                ),
            )
        )
        .where(
            _run.c.run_id == run_id,
            _run.c.calculation_kind == CALCULATION_KIND,
            _run.c.scope_kind == SCOPE_KIND,
        )
    ).mappings().one_or_none()
    if row is None:
        raise SealedManifestError("Portfolio Daily run or manifest was not found")
    run_status = CalculationRunStatus(str(row["run_status"]))
    if run_status is not CalculationRunStatus.RUNNING:
        raise SealedManifestError(
            f"Portfolio Daily manifest may be consumed only by a running job, not {run_status.value}"
        )
    if CalculationManifestStatus(str(row["manifest_status"])) is not CalculationManifestStatus.SEALED:
        raise SealedManifestError("Portfolio Daily input manifest is not sealed")
    if (
        row["methodology_version"] != METHODOLOGY_VERSION
        or row["input_schema_version"] != INPUT_SCHEMA_VERSION
        or row["output_schema_version"] != OUTPUT_SCHEMA_VERSION
        or row["schema_version"] != INPUT_SCHEMA_VERSION
    ):
        raise SealedManifestError("Portfolio Daily run version contract mismatch")
    run_generation = int(row["run_generation"])
    if int(row["manifest_generation"]) != run_generation:
        raise SealedManifestError("Portfolio Daily manifest generation mismatch")

    manifest_id = row["manifest_id"]
    dependencies = read_manifest_dependencies(session, manifest_id=manifest_id)
    digest = canonical_manifest_hash(
        {
            table.name: (
                DEPENDENCY_NATURAL_KEYS[table.name],
                dependencies.rows_by_table[table.name],
            )
            for table in DEPENDENCY_TABLES
        },
        input_schema_version=INPUT_SCHEMA_VERSION,
    )
    stored_hash = str(row["canonical_manifest_hash"])
    if digest != stored_hash:
        raise SealedManifestError(
            "Portfolio Daily sealed manifest hash verification failed"
        )
    counts = _validated_counts(row["dependency_counts"])
    if dict(counts) != dependencies.counts:
        raise SealedManifestError(
            "Portfolio Daily sealed manifest dependency counts do not match rows"
        )
    return SealedPortfolioDailyManifest(
        run_id=row["run_id"],
        manifest_id=manifest_id,
        portfolio_id=str(row["scope_id"]),
        effective_as_of=row["effective_as_of"],
        cutoff_at=row["cutoff_at"],
        captured_generation=run_generation,
        canonical_manifest_hash=digest,
        dependency_counts=counts,
        dependencies=dependencies,
    )


__all__ = [
    "SealedManifestError",
    "SealedPortfolioDailyManifest",
    "load_sealed_portfolio_daily_manifest",
]
