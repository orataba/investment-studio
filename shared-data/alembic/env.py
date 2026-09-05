from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from studio_data.core.settings import get_settings
from studio_data.db.base import Base
from studio_data.db import email_models  # noqa: F401
from studio_data.db import equity_models  # noqa: F401
from studio_data.db import etf_models  # noqa: F401


config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.migration_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_schema_comparison() -> bool:
    options = getattr(config, "cmd_opts", None)
    command = getattr(options, "cmd", None)
    command_name = (
        getattr(command[0], "__name__", "")
        if isinstance(command, tuple) and command
        else ""
    )
    return command_name == "check" or bool(getattr(options, "autogenerate", False))


def _include_object(object_, name: str | None, type_: str, reflected: bool, compare_to) -> bool:
    if reflected and type_ == "table" and name == "platform_alembic_version":
        return False
    if reflected and type_ == "foreign_key_constraint" and compare_to is None:
        elements = list(getattr(object_, "elements", []))
        if elements and elements[0].column.table.name == "instrument":
            return False
    return True


def _search_path_fragments(*schemas: str | None) -> list[str]:
    fragments: list[str] = []
    for candidate in (*schemas, "public"):
        if not candidate:
            continue
        normalized = candidate.strip()
        if normalized and normalized not in fragments:
            fragments.append(normalized)
    return fragments


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="platform_alembic_version",
    )

    with context.begin_transaction():
        operations_schema = settings.operations_database_schema
        if operations_schema and context.get_context().dialect.name == "postgresql":
            # Historical revisions build in platform; 0008 renames it in place.
            if context.get_starting_revision_argument() is None:
                context.execute('CREATE SCHEMA IF NOT EXISTS "platform"')
            context.execute(
                "SET search_path TO "
                + ", ".join(
                    f'"{fragment}"' if fragment != "public" else "public"
                    for fragment in _search_path_fragments(
                        operations_schema,
                        "platform",
                        settings.database_schema,
                        "instrument_registry",
                    )
                )
            )
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        operations_schema = settings.operations_database_schema
        if operations_schema and connection.dialect.name == "postgresql":
            existing_schemas = set(
                connection.scalars(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname IN ('platform', 'data_ingestion')"
                    )
                )
            )
            if len(existing_schemas) == 2:
                raise RuntimeError(
                    "Both platform and data_ingestion schemas exist; "
                    "resolve the collision before migrating."
                )
            if not existing_schemas:
                connection.execute(text('CREATE SCHEMA "platform"'))
            current_schema = (
                operations_schema if operations_schema in existing_schemas else "platform"
            )
            connection.commit()
            comparison_schemas = (
                (current_schema,)
                if _is_schema_comparison()
                else (operations_schema, "platform", settings.database_schema, "instrument_registry")
            )
            connection.execute(
                text(
                    "SET search_path TO "
                    + ", ".join(
                        f'"{fragment}"' if fragment != "public" else "public"
                        for fragment in _search_path_fragments(*comparison_schemas)
                    )
                )
            )
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="platform_alembic_version",
            # This app-specific table is unique across the database. Leaving it
            # unqualified lets Alembic update it after ALTER SCHEMA via search_path.
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
