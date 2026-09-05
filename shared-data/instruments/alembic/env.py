from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "shared-data" / "instruments" / "python"
instrument_core_path = str(INSTRUMENT_CORE_PYTHON)
if instrument_core_path not in sys.path:
    sys.path.insert(0, instrument_core_path)

from investment_studio_instrument_core.db_models import InstrumentRegistryBase


config = context.config

database_url = (
    os.getenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL")
    or os.getenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL")
)
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)
elif not config.get_main_option("sqlalchemy.url").strip():
    raise RuntimeError(
        "Instrument Registry migration database URL must be explicitly configured."
    )

schema = os.getenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "").strip() or "instrument_data"
if schema != "instrument_data":
    raise RuntimeError("Instrument Data migrations require the canonical 'instrument_data' schema.")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = InstrumentRegistryBase.metadata


def _include_object(object_, name: str | None, type_: str, reflected: bool, compare_to) -> bool:
    del object_, compare_to
    return not (reflected and type_ == "table" and name == "alembic_version")


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
        if connection.dialect.name == "postgresql":
            existing = set(connection.scalars(text(
                "SELECT nspname FROM pg_namespace "
                "WHERE nspname IN ('instrument_registry', 'instrument_data')"
            )))
            if len(existing) == 2:
                raise RuntimeError("Both instrument_registry and instrument_data schemas exist.")
            if not existing:
                connection.execute(text('CREATE SCHEMA "instrument_registry"'))
            connection.commit()
            connection.execute(text('SET search_path TO instrument_data, instrument_registry, public'))
            connection.commit()
            # Unqualified so the version update follows the in-place rename.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=version_table_schema,
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
