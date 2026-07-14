from __future__ import annotations

from sqlalchemy import Date, DateTime, Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.dialects import postgresql

from portfolio_ops_calculation_core.contracts import (
    ASSERT_MANIFEST_BUILDING,
    ASSERT_RUN_OUTPUT_WRITABLE,
    CALCULATION_REGISTRY_SCHEMA,
    CURRENT_PUBLICATION_COMMIT_TRIGGER,
    GUARD_MANIFEST_DEPENDENCY_MUTATION,
    PRODUCER_OUTPUT_FENCE,
    REJECT_TRUNCATE,
    VALIDATE_CURRENT_PUBLICATION_COMMIT,
)
from portfolio_ops_calculation_core.models import CalculationRegistryBase
from portfolio_ops_calculation_core.state import (
    CalculationJobStatus,
    CalculationManifestStatus,
    CalculationRunStatus,
    RecomputeIntentStatus,
)


EXPECTED_COLUMNS = {
    "calculation_scope_generation": {
        "calculation_kind",
        "scope_kind",
        "scope_id",
        "generation",
        "created_at",
        "updated_at",
    },
    "calculation_run": {
        "run_id",
        "calculation_kind",
        "scope_kind",
        "scope_id",
        "requested_as_of",
        "effective_as_of",
        "cutoff_at",
        "timezone",
        "methodology_version",
        "input_schema_version",
        "output_schema_version",
        "captured_generation",
        "dedupe_key",
        "status",
        "status_reason_code",
        "status_reason_context",
        "requested_by",
        "created_at",
        "started_at",
        "completed_at",
        "manifest_id",
        "published_output_hash",
        "superseded_by_run_id",
    },
    "calculation_input_manifest": {
        "manifest_id",
        "run_id",
        "captured_generation",
        "schema_version",
        "status",
        "canonical_manifest_hash",
        "dependency_counts",
        "created_at",
        "sealed_at",
    },
    "calculation_job": {
        "job_id",
        "run_id",
        "dedupe_key",
        "status",
        "attempt",
        "max_attempts",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "fencing_token",
        "failure_code",
        "failure_diagnostic",
        "created_at",
        "updated_at",
        "completed_at",
    },
    "calculation_publication": {
        "publication_id",
        "run_id",
        "manifest_id",
        "calculation_kind",
        "scope_kind",
        "scope_id",
        "output_schema_version",
        "published_fencing_token",
        "canonical_output_hash",
        "published_at",
    },
    "calculation_current_publication": {
        "calculation_kind",
        "scope_kind",
        "scope_id",
        "publication_id",
        "updated_at",
    },
    "calculation_recompute_intent": {
        "intent_id",
        "calculation_kind",
        "scope_kind",
        "scope_id",
        "requested_generation",
        "dedupe_key",
        "reason_code",
        "reason_context",
        "status",
        "run_id",
        "status_reason_code",
        "created_at",
        "updated_at",
        "completed_at",
    },
    "calculation_worker_heartbeat": {
        "worker_id",
        "instance_id",
        "worker_version",
        "supported_calculation_kinds",
        "started_at",
        "heartbeat_at",
        "metadata_json",
    },
}


def _table(name: str):  # type: ignore[no-untyped-def]
    return CalculationRegistryBase.metadata.tables[
        f"{CALCULATION_REGISTRY_SCHEMA}.{name}"
    ]


def test_registry_owns_only_the_eight_lifecycle_tables() -> None:
    assert {
        table.name for table in CalculationRegistryBase.metadata.tables.values()
    } == set(EXPECTED_COLUMNS)
    assert all(
        table.schema == CALCULATION_REGISTRY_SCHEMA
        for table in CalculationRegistryBase.metadata.tables.values()
    )


def test_each_table_has_the_exact_stable_columns() -> None:
    for table_name, expected in EXPECTED_COLUMNS.items():
        assert set(_table(table_name).columns.keys()) == expected


def test_uuid_columns_use_native_postgresql_uuid_and_python_uuid_values() -> None:
    expected = {
        "calculation_run": {"run_id", "manifest_id", "superseded_by_run_id"},
        "calculation_input_manifest": {"manifest_id", "run_id"},
        "calculation_job": {"job_id", "run_id"},
        "calculation_publication": {"publication_id", "run_id", "manifest_id"},
        "calculation_current_publication": {"publication_id"},
        "calculation_recompute_intent": {"intent_id", "run_id"},
        "calculation_worker_heartbeat": {"instance_id"},
    }
    actual: dict[str, set[str]] = {}
    for table_name in EXPECTED_COLUMNS:
        uuid_columns = {
            column.name
            for column in _table(table_name).columns
            if isinstance(column.type, PostgreSQLUUID)
        }
        if uuid_columns:
            actual[table_name] = uuid_columns
            assert all(
                _table(table_name).columns[name].type.as_uuid
                for name in uuid_columns
            )
    assert actual == expected


def test_all_timestamps_are_timezone_aware_and_as_of_values_are_dates() -> None:
    for table in CalculationRegistryBase.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.name}.{column.name}"

    run = _table("calculation_run")
    assert type(run.c.requested_as_of.type) is Date
    assert type(run.c.effective_as_of.type) is Date
    assert str(run.c.cutoff_at.server_default.arg) == "transaction_timestamp()"


def test_jsonb_is_limited_to_explicit_extensible_structures() -> None:
    expected = {
        ("calculation_run", "status_reason_context"),
        ("calculation_input_manifest", "dependency_counts"),
        ("calculation_recompute_intent", "reason_context"),
        ("calculation_worker_heartbeat", "supported_calculation_kinds"),
        ("calculation_worker_heartbeat", "metadata_json"),
    }
    actual = {
        (table.name, column.name)
        for table in CalculationRegistryBase.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB)
    }
    assert actual == expected


def test_status_columns_are_closed_non_native_enums() -> None:
    expected = {
        "calculation_run": CalculationRunStatus,
        "calculation_input_manifest": CalculationManifestStatus,
        "calculation_job": CalculationJobStatus,
        "calculation_recompute_intent": RecomputeIntentStatus,
    }
    for table_name, enum_class in expected.items():
        column_type = _table(table_name).c.status.type
        assert isinstance(column_type, SqlEnum)
        assert not column_type.native_enum
        assert column_type.validate_strings
        assert column_type.enum_class is enum_class
        assert column_type.enums == [member.value for member in enum_class]


def test_active_run_and_queue_indexes_are_partial_postgresql_indexes() -> None:
    dialect = postgresql.dialect()
    run_index = next(
        index
        for index in _table("calculation_run").indexes
        if index.name == "uq_calculation_run_active_dedupe"
    )
    assert run_index.unique
    run_ddl = str(CreateIndex(run_index).compile(dialect=dialect))
    assert "WHERE status IN ('capturing', 'queued', 'running', 'succeeded')" in run_ddl

    queue_index = next(
        index
        for index in _table("calculation_job").indexes
        if index.name == "ix_calculation_job_queue_claim"
    )
    queue_ddl = str(CreateIndex(queue_index).compile(dialect=dialect))
    assert "WHERE status IN ('queued', 'retry_wait')" in queue_ddl


def test_composite_foreign_keys_bind_scope_manifest_and_current_pointer() -> None:
    expected_targets = {
        "calculation_run": {
            "calculation_scope_generation",
            "calculation_input_manifest",
            "calculation_run",
        },
        "calculation_publication": {
            "calculation_run",
            "calculation_input_manifest",
        },
        "calculation_current_publication": {
            "calculation_scope_generation",
            "calculation_publication",
        },
    }
    for table_name, expected in expected_targets.items():
        targets = {
            element.column.table.name
            for constraint in _table(table_name).foreign_key_constraints
            for element in constraint.elements
        }
        assert targets == expected


def test_postgresql_ddl_contains_lifecycle_and_hash_guards() -> None:
    dialect = postgresql.dialect()
    run_ddl = str(CreateTable(_table("calculation_run")).compile(dialect=dialect))
    assert "capturing" in run_ddl
    assert "published_output_hash" in run_ddl
    assert "^[0-9a-f]{64}$" in run_ddl
    assert "status = 'failed' AND completed_at IS NOT NULL" in run_ddl
    assert "status_reason_code IS NOT NULL" in run_ddl
    assert "superseded_by_run_id IS NOT NULL" in run_ddl

    manifest_ddl = str(
        CreateTable(_table("calculation_input_manifest")).compile(dialect=dialect)
    )
    assert "status = 'building'" in manifest_ddl
    assert "status = 'sealed'" in manifest_ddl
    assert "jsonb_typeof(dependency_counts) = 'object'" in manifest_ddl

    job_ddl = str(CreateTable(_table("calculation_job")).compile(dialect=dialect))
    assert "lease_expires_at > heartbeat_at" in job_ddl
    assert "attempt <= max_attempts" in job_ddl
    assert "status = 'leased' AND attempt >= 1 AND fencing_token = attempt" in job_ddl
    assert "status = 'retry_wait' AND attempt >= 1 AND fencing_token = attempt" in job_ddl
    assert "status = 'succeeded' AND attempt >= 1 AND fencing_token = attempt" in job_ddl
    assert "status = 'failed' AND attempt >= 1 AND fencing_token = attempt" in job_ddl

    intent_ddl = str(
        CreateTable(_table("calculation_recompute_intent")).compile(dialect=dialect)
    )
    assert "status = 'materialized' AND run_id IS NOT NULL" in intent_ddl
    assert "status IN ('superseded', 'failed') AND status_reason_code IS NOT NULL" in intent_ddl

    publication_ddl = str(
        CreateTable(_table("calculation_publication")).compile(dialect=dialect)
    )
    assert "published_fencing_token BIGINT NOT NULL" in publication_ddl
    assert "published_fencing_token > 0" in publication_ddl


def test_database_function_contracts_are_schema_qualified_and_typed() -> None:
    assert ASSERT_MANIFEST_BUILDING.qualified_name == (
        "calculation_registry.assert_manifest_building"
    )
    assert ASSERT_MANIFEST_BUILDING.argument_types == ("uuid",)
    assert GUARD_MANIFEST_DEPENDENCY_MUTATION.return_type == "trigger"
    assert ASSERT_RUN_OUTPUT_WRITABLE.argument_types == (
        "uuid",
        "bigint",
        "varchar",
    )
    assert tuple(
        argument.name for argument in ASSERT_RUN_OUTPUT_WRITABLE.arguments
    ) == ("p_run_id", "p_fencing_token", "p_lease_owner")
    assert ASSERT_RUN_OUTPUT_WRITABLE.return_type == "void"
    assert REJECT_TRUNCATE.qualified_name == "calculation_registry.reject_truncate"
    assert REJECT_TRUNCATE.return_type == "trigger"
    assert VALIDATE_CURRENT_PUBLICATION_COMMIT.qualified_name == (
        "calculation_registry.validate_current_publication_commit"
    )
    assert VALIDATE_CURRENT_PUBLICATION_COMMIT.return_type == "trigger"
    assert CURRENT_PUBLICATION_COMMIT_TRIGGER == (
        "trg_calculation_current_publication_commit"
    )


def test_producer_output_fence_is_part_of_identity_but_not_financial_hash() -> None:
    assert PRODUCER_OUTPUT_FENCE.output_column_name == "output_fencing_token"
    assert PRODUCER_OUTPUT_FENCE.output_database_type == "bigint"
    assert PRODUCER_OUTPUT_FENCE.publication_column_name == "published_fencing_token"
    assert PRODUCER_OUTPUT_FENCE.required_identity_columns == (
        "run_id",
        "output_fencing_token",
    )
    assert PRODUCER_OUTPUT_FENCE.canonical_hash_excluded_columns == frozenset(
        {"output_fencing_token"}
    )
    assert PRODUCER_OUTPUT_FENCE.write_guard is ASSERT_RUN_OUTPUT_WRITABLE
