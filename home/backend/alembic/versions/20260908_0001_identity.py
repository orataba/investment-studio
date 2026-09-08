"""Version the initial identity schema and adopt existing account data in place."""
from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import sqlalchemy as sa

revision = "20260908_0001"
down_revision = None
branch_labels = None
depends_on = None


def baseline_metadata(schema):
    metadata = sa.MetaData(schema=schema)
    sa.Table('audit_events', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('actor_user_id', sa.String(length=36), primary_key=False, nullable=True),
        sa.Column('action', sa.String(length=80), primary_key=False, nullable=False),
        sa.Column('target_id', sa.String(length=128), primary_key=False, nullable=True),
        sa.Column('detail', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    sa.Table('delegations', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False, unique=True),
        sa.Column('parent_kind', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('parent_id', sa.String(length=36), primary_key=False, nullable=False),
        sa.Column('audience', sa.String(length=80), primary_key=False, nullable=False),
        sa.Column('resource_scope', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
    )
    sa.Table('users', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('username', sa.String(length=128), primary_key=False, nullable=False, unique=True),
        sa.Column('display_name', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('password_hash', sa.Text(), primary_key=False, nullable=True),
        sa.Column('active', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('failed_logins', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('totp_secret', sa.Text(), primary_key=False, nullable=True),
        sa.Column('totp_pending_secret', sa.Text(), primary_key=False, nullable=True),
        sa.Column('totp_last_step', sa.Integer(), primary_key=False, nullable=True),
    )
    sa.Table('one_time_tokens', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False, unique=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'users.id'), primary_key=False, nullable=False),
        sa.Column('purpose', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Index('ix_identity_one_time_tokens_user_id', 'user_id'),
    )
    sa.Table('sessions', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False, unique=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'users.id'), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Index('ix_identity_sessions_user_id', 'user_id'),
    )
    sa.Table('teams', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'users.id'), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    sa.Table('service_credentials', metadata,
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False, unique=True),
        sa.Column('service_id', sa.String(length=128), primary_key=False, nullable=False),
        sa.Column('display_name', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('team_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'teams.id'), primary_key=False, nullable=False),
        sa.Column('audiences', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('scopes', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
    )
    sa.Table('team_memberships', metadata,
        sa.Column('team_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'teams.id'), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey((schema + "." if schema else "") + 'users.id'), primary_key=True, nullable=False),
        sa.Column('role', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    return metadata


def upgrade():
    bind = op.get_bind()
    schema = "identity" if bind.dialect.name == "postgresql" else None
    metadata = baseline_metadata(schema)
    # Previous installations already contain these tables and real accounts.
    # Adopt them without rewriting rows, but reject incompatible unversioned schemas.
    metadata.create_all(bind, checkfirst=True)
    def include_name(name, kind, parent_names):
        return name == schema if kind == "schema" else True
    changes = compare_metadata(MigrationContext.configure(bind, opts={
        "include_schemas": True, "include_name": include_name,
        "version_table_schema": schema,
    }), metadata)
    if changes:
        raise RuntimeError("Existing identity schema differs from its migration baseline; reconcile it before adoption.")


def downgrade():
    bind = op.get_bind()
    schema = "identity" if bind.dialect.name == "postgresql" else None
    baseline_metadata(schema).drop_all(bind)
