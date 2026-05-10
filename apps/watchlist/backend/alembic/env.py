from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from watchlist_app.core.settings import get_settings
from watchlist_app.db.base import Base
import watchlist_app.db.models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.migration_database_url)
target_metadata = Base.metadata


def _search_path_fragments(schema: str | None) -> list[str]:
    fragments: list[str] = []
    for candidate in [schema, "instrument_registry", "public"]:
        if not candidate:
            continue
        normalized = candidate.strip()
        if normalized and normalized not in fragments:
            fragments.append(normalized)
    return fragments


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
        if settings.database_schema and connection.dialect.name == "postgresql":
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.database_schema}"'))
            connection.commit()
            connection.execute(
                text(
                    "SET search_path TO "
                    + ", ".join(
                        f'"{fragment}"' if fragment != "public" else "public"
                        for fragment in _search_path_fragments(settings.database_schema)
                    )
                )
            )
            connection.commit()
            version_table_schema = settings.database_schema
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
