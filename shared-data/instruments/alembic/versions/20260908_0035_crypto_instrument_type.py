"""Register native crypto assets separately from listed securities.

Revision ID: 20260908_0035
Revises: 20260907_0034
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0035"
down_revision = "20260907_0034"
branch_labels = None
depends_on = None


def _replace_type_constraint(include_crypto):
    connection = op.get_bind()
    # SQLite table rebuilds must preserve every contract trigger, including
    # triggers attached to other tables which reference the rebuilt instrument.
    triggers = []
    if connection.dialect.name == "sqlite":
        rows = connection.execute(sa.text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL ORDER BY name"
        )).all()
        triggers = [row.sql for row in rows]
        for row in rows:
            name = '"' + row.name.replace('"', '""') + '"'
            connection.exec_driver_sql(f"DROP TRIGGER {name}")
    with op.batch_alter_table("instrument", recreate="always" if connection.dialect.name == "sqlite" else "auto") as batch:
        batch.drop_constraint("instrument_type_contract", type_="check")
        types = "'public_fund', 'private_fund', 'etf', 'index', 'equity', 'cash', 'fx', 'other'"
        if include_crypto:
            types += ", 'crypto'"
        batch.create_check_constraint("instrument_type_contract",
            f"instrument_type IN ({types})")
    for sql in triggers:
        connection.exec_driver_sql(sql)


def upgrade():
    _replace_type_constraint(True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM instrument WHERE instrument_type = 'crypto' LIMIT 1")).first():
        raise RuntimeError("Registered crypto facts exist; restore the pre-migration backup instead of discarding them.")
    _replace_type_constraint(False)
