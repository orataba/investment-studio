from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
instrument_core_path = str(INSTRUMENT_CORE_PYTHON)
if instrument_core_path not in sys.path:
    sys.path.insert(0, instrument_core_path)

from portfolio_ops_instrument_core.db_models import InstrumentRegistryBase


config = context.config

database_url = (
    os.getenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL")
    or os.getenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

raw_schema = os.getenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry")
schema = raw_schema.strip() if raw_schema and raw_schema.strip() else None

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = InstrumentRegistryBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        version_table_schema = None
        if schema and connection.dialect.name == "postgresql":
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            connection.commit()
            connection.execute(text(f'SET search_path TO "{schema}", public'))
            connection.commit()
            version_table_schema = schema
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=version_table_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
