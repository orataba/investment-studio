from alembic import context
from sqlalchemy import create_engine, text

from briefing_app.db import Base
from briefing_app.settings import get_settings

settings = get_settings()
url = settings.alembic_database_url or settings.database_url


def offline():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, version_table_schema="briefing")
    with context.begin_transaction():
        context.run_migrations()


def online():
    with create_engine(url).connect() as connection:
        schema = "briefing" if connection.dialect.name == "postgresql" else None
        if schema:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS briefing"))
            connection.execute(text("SET search_path TO briefing, public"))
            connection.commit()
        context.configure(connection=connection, target_metadata=Base.metadata, version_table_schema=schema)
        with context.begin_transaction():
            context.run_migrations()


offline() if context.is_offline_mode() else online()
