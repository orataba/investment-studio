from alembic import context

from home_api.core.settings import get_settings
from home_api.db.models import Base
from home_api.db.session import database_engine


def migrate(connection):
    schema = "identity" if connection.dialect.name == "postgresql" else None
    if schema:
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS identity")
    context.configure(connection=connection, target_metadata=Base.metadata,
                      version_table_schema=schema, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


provided = context.config.attributes.get("connection")
if provided is not None:
    migrate(provided)
else:
    with database_engine(get_settings().database_url).begin() as connection:
        migrate(connection)
