from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql

from portfolio_ops_calculation_core.models import CalculationRegistryBase


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = (
    WORKSPACE_ROOT
    / "infra"
    / "calculation_registry"
    / "alembic"
    / "versions"
    / "20260714_0001_calculation_registry_foundation.py"
)


class _MigrationCapture:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Column[Any]]] = {}
        self.table_items: dict[str, tuple[Any, ...]] = {}
        self.indexes: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.foreign_keys: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.executed_sql: list[str] = []

    def create_table(self, table_name: str, *items: Any, **kwargs: Any) -> None:
        assert kwargs["schema"] == "calculation_registry"
        self.table_items[table_name] = items
        self.tables[table_name] = {
            item.name: item for item in items if isinstance(item, Column)
        }

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.indexes.append((args, kwargs))

    def create_foreign_key(self, *args: Any, **kwargs: Any) -> None:
        self.foreign_keys.append((args, kwargs))

    def execute(self, statement: object) -> None:
        self.executed_sql.append(str(statement))


def _load_migration() -> ModuleType:
    spec = spec_from_file_location("calculation_registry_0001", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compiled_type(column: Column[Any]) -> str:
    return str(column.type.compile(dialect=postgresql.dialect())).upper()


def _server_default(column: Column[Any]) -> str | None:
    if column.server_default is None:
        return None
    return str(column.server_default.arg).strip()


def _normalized_sql(value: object | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())


def _index_expression(value: object) -> str:
    if isinstance(value, str):
        return value
    element = getattr(value, "element", None)
    if element is not None and getattr(element, "name", None):
        return f"{element.name} DESC"
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return _normalized_sql(value) or ""


def test_model_columns_match_foundation_migration_exactly() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_tables()

    model_tables = {
        table.name: table for table in CalculationRegistryBase.metadata.tables.values()
    }
    assert set(capture.tables) == set(model_tables)

    for table_name, model_table in model_tables.items():
        migration_columns = capture.tables[table_name]
        assert set(migration_columns) == set(model_table.columns.keys()), table_name
        for model_column in model_table.columns:
            migration_column = migration_columns[model_column.name]
            assert _compiled_type(model_column) == _compiled_type(migration_column), (
                table_name,
                model_column.name,
            )
            assert model_column.nullable is migration_column.nullable, (
                table_name,
                model_column.name,
            )
            assert _server_default(model_column) == _server_default(migration_column), (
                table_name,
                model_column.name,
            )


def test_model_constraints_match_foundation_migration_exactly() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_tables()

    for table_name, items in capture.table_items.items():
        model_table = CalculationRegistryBase.metadata.tables[
            f"calculation_registry.{table_name}"
        ]

        migration_primary_keys = {
            (constraint.name, tuple(constraint._pending_colargs))
            for constraint in items
            if isinstance(constraint, PrimaryKeyConstraint)
        }
        model_primary_keys = {
            (model_table.primary_key.name, tuple(model_table.primary_key.columns.keys()))
        }
        assert migration_primary_keys == model_primary_keys, table_name

        migration_uniques = {
            (constraint.name, tuple(constraint._pending_colargs))
            for constraint in items
            if isinstance(constraint, UniqueConstraint)
        }
        model_uniques = {
            (constraint.name, tuple(constraint.columns.keys()))
            for constraint in model_table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert migration_uniques == model_uniques, table_name

        migration_checks = {
            (constraint.name, _normalized_sql(constraint.sqltext))
            for constraint in items
            if isinstance(constraint, CheckConstraint)
        }
        model_checks = {
            (constraint.name, _normalized_sql(constraint.sqltext))
            for constraint in model_table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert migration_checks == model_checks, table_name


def test_model_foreign_keys_match_foundation_migration_exactly() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_tables()

    migration_foreign_keys: set[
        tuple[str, str, tuple[str, ...], tuple[str, ...], str | None]
    ] = set()
    for table_name, items in capture.table_items.items():
        for constraint in items:
            if isinstance(constraint, ForeignKeyConstraint):
                migration_foreign_keys.add(
                    (
                        constraint.name or "",
                        table_name,
                        tuple(constraint.column_keys),
                        tuple(
                            element.target_fullname for element in constraint.elements
                        ),
                        constraint.ondelete,
                    )
                )
    for args, kwargs in capture.foreign_keys:
        name, source_table, referent_table, local_columns, remote_columns = args
        referent_schema = kwargs["referent_schema"]
        migration_foreign_keys.add(
            (
                name,
                source_table,
                tuple(local_columns),
                tuple(
                    f"{referent_schema}.{referent_table}.{column}"
                    for column in remote_columns
                ),
                kwargs.get("ondelete"),
            )
        )

    model_foreign_keys = {
        (
            constraint.name or "",
            table.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for table in CalculationRegistryBase.metadata.tables.values()
        for constraint in table.foreign_key_constraints
    }
    assert migration_foreign_keys == model_foreign_keys


def test_model_indexes_match_foundation_migration_exactly() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_tables()

    migration_indexes = {
        (
            args[0],
            args[1],
            tuple(_index_expression(value) for value in args[2]),
            bool(kwargs.get("unique", False)),
            _normalized_sql(kwargs.get("postgresql_where")),
        )
        for args, kwargs in capture.indexes
    }
    model_indexes = {
        (
            index.name,
            table.name,
            tuple(_index_expression(value) for value in index.expressions),
            index.unique,
            _normalized_sql(index.dialect_options["postgresql"].get("where")),
        )
        for table in CalculationRegistryBase.metadata.tables.values()
        for index in table.indexes
    }
    assert migration_indexes == model_indexes


def test_migration_installs_attempt_fence_and_deferred_commit_contracts() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_guard_functions()
    migration._create_triggers()
    sql = _normalized_sql("\n".join(capture.executed_sql)) or ""

    assert (
        "CREATE FUNCTION calculation_registry.assert_run_output_writable( "
        "p_run_id uuid, p_fencing_token bigint, p_lease_owner varchar )"
    ) in sql
    assert "v_job.fencing_token <> NEW.published_fencing_token" in sql
    assert (
        "CREATE FUNCTION calculation_registry.validate_current_publication_commit()"
    ) in sql
    assert (
        "CREATE CONSTRAINT TRIGGER trg_calculation_current_publication_commit"
    ) in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert (
        "EXECUTE FUNCTION calculation_registry.validate_current_publication_commit()"
    ) in sql
    assert "v_state.run_status <> 'published'" in sql
    assert (
        "v_state.job_fencing_token IS DISTINCT FROM "
        "v_state.published_fencing_token"
    ) in sql
    assert (
        "v_state.published_output_hash IS DISTINCT FROM "
        "v_state.canonical_output_hash"
    ) in sql
    assert "NEW.cutoff_at IS DISTINCT FROM transaction_timestamp()" in sql
    assert (
        "v_publication.effective_as_of < v_prior_effective_as_of"
    ) in sql


def test_run_cutoff_default_is_database_transaction_timestamp() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_tables()

    cutoff = capture.tables["calculation_run"]["cutoff_at"]
    assert _server_default(cutoff) == "transaction_timestamp()"


def test_migration_installs_explicit_half_even_numeric_rounding() -> None:
    migration = _load_migration()
    capture = _MigrationCapture()
    migration.op = capture
    migration._create_numeric_functions()
    sql = _normalized_sql("\n".join(capture.executed_sql)) or ""

    assert "CREATE FUNCTION calculation_registry.round_half_even" in sql
    assert (
        "CREATE FUNCTION calculation_registry.round_significant_half_even"
        in sql
    )
    assert "WHEN mod(v_floor, 2) = 0 THEN v_floor" in sql
    assert "* v_quantum" in sql
    assert "IMMUTABLE STRICT PARALLEL SAFE" in sql
