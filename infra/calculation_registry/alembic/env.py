from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
INFRA_PYTHON = WORKSPACE_ROOT / "infra" / "python"
CALCULATION_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "calculation-core" / "python"
infra_python_path = str(INFRA_PYTHON)
if infra_python_path not in sys.path:
    sys.path.insert(0, infra_python_path)
calculation_core_path = str(CALCULATION_CORE_PYTHON)
if calculation_core_path not in sys.path:
    sys.path.insert(0, calculation_core_path)

from portfolio_ops_infra import (  # noqa: E402
    require_expected_postgresql_database,
    verify_postgresql_connection_database,
)
from portfolio_ops_calculation_core.models import (  # noqa: E402
    CalculationRegistryBase,
)


SCHEMA = "calculation_registry"
config = context.config

database_url = (
    os.getenv("PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL")
    or os.getenv("PORTFOLIO_OPS_CALCULATION_REGISTRY_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
if not database_url:
    raise RuntimeError("calculation registry migration requires a database URL")
if make_url(database_url).get_backend_name() != "postgresql":
    raise RuntimeError(
        "calculation registry migrations support PostgreSQL only; "
        "SQLite compatibility is intentionally not provided"
    )
config.set_main_option("sqlalchemy.url", database_url)

configured_schema = str(
    os.getenv("PORTFOLIO_OPS_CALCULATION_REGISTRY_SCHEMA") or SCHEMA
).strip()
if configured_schema != SCHEMA:
    raise RuntimeError(
        "calculation registry schema is fixed as 'calculation_registry'; "
        f"received {configured_schema!r}"
    )

expected_database = require_expected_postgresql_database(
    database_url,
    component="calculation registry",
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = CalculationRegistryBase.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    context.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        verify_postgresql_connection_database(
            connection,
            expected_database=expected_database,
            component="calculation registry",
        )
        if connection.dialect.name != "postgresql":
            raise RuntimeError("calculation registry migrations require PostgreSQL")
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            version_table_schema=SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
