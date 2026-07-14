"""Canonical Portfolio Daily manifest and financial-output hashing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Final
from uuid import UUID

from portfolio_app.calculations.numeric import (
    CanonicalJsonValue,
    canonical_json_value,
    canonical_sha256_hex,
)


MANIFEST_IDENTITY_COLUMNS: Final[frozenset[str]] = frozenset(
    {"manifest_id", "run_id", "captured_at"}
)
OUTPUT_EXECUTION_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "run_id",
        "output_fencing_token",
        "worker_id",
        "calculated_at",
        "canonical_output_hash",
    }
)


class CanonicalHashContractError(ValueError):
    pass


def _canonical_storage_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalHashContractError(
                "canonical datetime values must be timezone-aware"
            )
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_storage_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_storage_value(item) for item in value]
    if isinstance(value, (str, bool, int, Decimal)) or value is None:
        return value
    if isinstance(value, float):
        raise CanonicalHashContractError(
            "canonical financial hashes must not contain binary floats"
        )
    raise CanonicalHashContractError(
        "unsupported canonical storage value type: " + type(value).__name__
    )


def canonical_storage_json(value: object) -> CanonicalJsonValue:
    """Convert SQL/Python values to JSON-safe, exact canonical primitives."""

    return canonical_json_value(_canonical_storage_value(value))


def _project_row(
    row: Mapping[str, object],
    *,
    excluded_columns: frozenset[str],
) -> dict[str, object]:
    return {
        str(key): _canonical_storage_value(value)
        for key, value in row.items()
        if str(key) not in excluded_columns
    }


def _sort_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    natural_key: Sequence[str],
    excluded_columns: frozenset[str],
) -> list[dict[str, object]]:
    projected = [
        _project_row(row, excluded_columns=excluded_columns) for row in rows
    ]
    missing = [
        column
        for column in natural_key
        if any(column not in row for row in projected)
    ]
    if missing:
        raise CanonicalHashContractError(
            "canonical rows are missing natural-key columns: "
            + ", ".join(sorted(set(missing)))
        )
    return sorted(
        projected,
        key=lambda row: tuple(str(row[column]) for column in natural_key),
    )


def canonical_manifest_hash(
    dependency_rows: Mapping[
        str,
        tuple[Sequence[str], Iterable[Mapping[str, object]]],
    ],
    *,
    input_schema_version: str,
) -> str:
    """Hash immutable typed inputs, independent of run/manifest UUIDs.

    ``knowledge_cutoff_at`` remains in the config row and is intentionally part
    of the digest.  A later knowledge snapshot is a different manifest even if
    every adopted financial value happens to be unchanged.
    """

    dependencies = {
        table_name: _sort_rows(
            rows,
            natural_key=natural_key,
            excluded_columns=MANIFEST_IDENTITY_COLUMNS,
        )
        for table_name, (natural_key, rows) in sorted(dependency_rows.items())
    }
    return canonical_sha256_hex(
        {
            "domain": "portfolio-daily-manifest-hash.v1",
            "input_schema_version": input_schema_version,
            "dependencies": dependencies,
        }
    )


def canonical_financial_output_hash(
    output_rows: Mapping[
        str,
        tuple[Sequence[str], Iterable[Mapping[str, object]]],
    ],
    *,
    methodology_version: str,
    output_schema_version: str,
) -> str:
    """Hash financial results without attempt/worker/timestamp identity."""

    outputs = {
        table_name: _sort_rows(
            rows,
            natural_key=natural_key,
            excluded_columns=OUTPUT_EXECUTION_COLUMNS,
        )
        for table_name, (natural_key, rows) in sorted(output_rows.items())
    }
    return canonical_sha256_hex(
        {
            "domain": "portfolio-daily-financial-output-hash.v1",
            "methodology_version": methodology_version,
            "output_schema_version": output_schema_version,
            "outputs": outputs,
        }
    )


__all__ = [
    "CanonicalHashContractError",
    "MANIFEST_IDENTITY_COLUMNS",
    "OUTPUT_EXECUTION_COLUMNS",
    "canonical_storage_json",
    "canonical_financial_output_hash",
    "canonical_manifest_hash",
]
