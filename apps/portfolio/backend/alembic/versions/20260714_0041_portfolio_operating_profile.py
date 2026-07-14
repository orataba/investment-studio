"""Add the explicit portfolio operating profile boundary.

Revision ID: 20260714_0041
Revises: 20260714_0040
Create Date: 2026-07-14

Every pre-existing portfolio is classified explicitly as ``standard_taxonomy``
before the column is made mandatory.  No database default is installed: all
future portfolio creation paths must state the operating profile as a fact.
"""

from __future__ import annotations

from alembic import op


revision = "20260714_0041"
down_revision = "20260714_0040"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("20260714_0041 requires PostgreSQL")


def upgrade() -> None:
    _require_postgresql()
    op.execute(
        """
        ALTER TABLE portfolio.portfolio_record
            ADD COLUMN operating_profile varchar(32);

        UPDATE portfolio.portfolio_record
        SET operating_profile = 'standard_taxonomy';

        ALTER TABLE portfolio.portfolio_record
            ALTER COLUMN operating_profile SET NOT NULL,
            ADD CONSTRAINT ck_portfolio_record_operating_profile
                CHECK (
                    operating_profile IN (
                        'standard_taxonomy',
                        'external_etf_rotation'
                    )
                );
        """
    )


def downgrade() -> None:
    _require_postgresql()
    op.execute(
        """
        ALTER TABLE portfolio.portfolio_record
            DROP CONSTRAINT ck_portfolio_record_operating_profile,
            DROP COLUMN operating_profile;
        """
    )
