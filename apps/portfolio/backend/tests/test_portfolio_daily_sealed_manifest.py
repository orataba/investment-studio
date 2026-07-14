from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from portfolio_app.calculations.portfolio_daily import sealed_manifest as subject
from portfolio_app.calculations.portfolio_daily.constants import (
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.hashing import canonical_manifest_hash
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    DEPENDENCY_NATURAL_KEYS,
    ManifestDependencies,
)


pytestmark = pytest.mark.no_database


class _Result:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, object]:
        return self.row


class _Session:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def execute(self, statement: object) -> _Result:
        del statement
        return _Result(self.row)


def _fixture() -> tuple[dict[str, object], ManifestDependencies]:
    run_id = uuid4()
    manifest_id = uuid4()
    dependencies = ManifestDependencies(
        {table.name: () for table in DEPENDENCY_TABLES}
    )
    digest = canonical_manifest_hash(
        {
            table.name: (DEPENDENCY_NATURAL_KEYS[table.name], ())
            for table in DEPENDENCY_TABLES
        },
        input_schema_version=INPUT_SCHEMA_VERSION,
    )
    return (
        {
            "run_id": run_id,
            "scope_id": "portfolio-exact",
            "run_status": "running",
            "effective_as_of": date(2026, 7, 14),
            "cutoff_at": datetime(2026, 7, 14, 8, tzinfo=UTC),
            "methodology_version": METHODOLOGY_VERSION,
            "input_schema_version": INPUT_SCHEMA_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "run_generation": 3,
            "manifest_id": manifest_id,
            "manifest_status": "sealed",
            "schema_version": INPUT_SCHEMA_VERSION,
            "manifest_generation": 3,
            "canonical_manifest_hash": digest,
            "dependency_counts": dependencies.counts,
        },
        dependencies,
    )


def test_worker_rehashes_the_sealed_typed_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, dependencies = _fixture()
    monkeypatch.setattr(
        subject,
        "read_manifest_dependencies",
        lambda session, *, manifest_id: dependencies,
    )

    loaded = subject.load_sealed_portfolio_daily_manifest(
        _Session(row),  # type: ignore[arg-type]
        run_id=row["run_id"],  # type: ignore[arg-type]
    )

    assert loaded.manifest_id == row["manifest_id"]
    assert loaded.canonical_manifest_hash == row["canonical_manifest_hash"]
    assert dict(loaded.dependency_counts) == dependencies.counts


def test_worker_rejects_any_manifest_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, dependencies = _fixture()
    row["canonical_manifest_hash"] = "f" * 64
    monkeypatch.setattr(
        subject,
        "read_manifest_dependencies",
        lambda session, *, manifest_id: dependencies,
    )

    with pytest.raises(subject.SealedManifestError, match="hash verification"):
        subject.load_sealed_portfolio_daily_manifest(
            _Session(row),  # type: ignore[arg-type]
            run_id=row["run_id"],  # type: ignore[arg-type]
        )
