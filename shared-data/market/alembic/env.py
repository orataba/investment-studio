"""Migrations for the project's shared market facts and text corpus."""
from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from studio_market.config import MarketSettings


settings = MarketSettings.from_environment()
database_url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

# Portable restores omit ACLs. Restore the configured Regime consumer's public
# read access whenever migrations run, including an already-current schema.
REGIME_READER_GRANTS = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'studio_market_regime_reader') THEN
        GRANT USAGE ON SCHEMA market_data TO studio_market_regime_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA market_data TO studio_market_regime_reader;
        ALTER DEFAULT PRIVILEGES IN SCHEMA market_data
            GRANT SELECT ON TABLES TO studio_market_regime_reader;
    END IF;
END
$$;
"""


def configure(connection=None):
    context.configure(connection=connection, url=database_url, target_metadata=None,
                      version_table="studio_market_alembic_version", version_table_schema="market_data",
                      literal_binds=connection is None)


if context.is_offline_mode():
    configure()
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS market_data")
        context.run_migrations()
        context.execute(REGIME_READER_GRANTS)
else:
    with create_engine(database_url, poolclass=NullPool).connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS market_data"))
        connection.commit()
        configure(connection)
        with context.begin_transaction():
            context.run_migrations()
            context.execute(REGIME_READER_GRANTS)
