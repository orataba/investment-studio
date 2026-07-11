"""Add canonical corporate actions and seed confirmed ETF share splits.

Revision ID: 20260712_0007
Revises: 20260712_0006
"""

from datetime import UTC, date, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260712_0007"
down_revision = "20260712_0006"
branch_labels = None
depends_on = None


corporate_action_event = sa.table(
    "corporate_action_event",
    sa.column("corporate_action_event_id", sa.String()),
    sa.column("instrument_id", sa.String()),
    sa.column("action_type", sa.String()),
    sa.column("announcement_date", sa.Date()),
    sa.column("record_date", sa.Date()),
    sa.column("effective_date", sa.Date()),
    sa.column("payable_date", sa.Date()),
    sa.column("new_units", sa.Text()),
    sa.column("old_units", sa.Text()),
    sa.column("quantity_rounding", sa.String()),
    sa.column("quantity_precision", sa.Integer()),
    sa.column("cost_basis_treatment", sa.String()),
    sa.column("source", sa.String()),
    sa.column("external_event_id", sa.String()),
    sa.column("status", sa.String()),
    sa.column("provenance_json", sa.JSON()),
    sa.column("created_at", sa.String()),
    sa.column("updated_at", sa.String()),
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    op.create_table(
        "corporate_action_event",
        sa.Column("corporate_action_event_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("announcement_date", sa.Date(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("payable_date", sa.Date(), nullable=True),
        sa.Column("new_units", sa.Text(), nullable=False),
        sa.Column("old_units", sa.Text(), nullable=False),
        sa.Column("quantity_rounding", sa.String(), nullable=False),
        sa.Column("quantity_precision", sa.Integer(), nullable=False),
        sa.Column("cost_basis_treatment", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_event_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("corporate_action_event_id"),
        sa.UniqueConstraint(
            "instrument_id",
            "action_type",
            "effective_date",
            name="uq_corporate_action_instrument_type_effective_date",
        ),
    )
    op.create_index(
        "ix_corporate_action_instrument_effective_date",
        "corporate_action_event",
        ["instrument_id", "effective_date"],
        unique=False,
    )

    connection = op.get_bind()
    instrument_exists = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM instrument "
            "WHERE instrument_id = '159516-sz'"
        )
    )
    if not instrument_exists:
        return

    now = _utcnow_iso()
    connection.execute(
        sa.insert(corporate_action_event),
        [
            {
                "corporate_action_event_id": "ca-159516-sz-share-split-2026-03-30",
                "instrument_id": "159516-sz",
                "action_type": "share_split",
                "announcement_date": date(2026, 3, 30),
                "record_date": date(2026, 3, 27),
                "effective_date": date(2026, 3, 30),
                "payable_date": date(2026, 3, 30),
                "new_units": "2",
                "old_units": "1",
                "quantity_rounding": "exact",
                "quantity_precision": 0,
                "cost_basis_treatment": "carry",
                "source": "fund_manager_announcement",
                "external_event_id": "gtfund-828120866184",
                "status": "confirmed",
                "provenance_json": {
                    "issuer": "国泰基金管理有限公司",
                    "announcement_title": "国泰中证半导体材料设备主题交易型开放式指数证券投资基金基金份额拆分结果的公告",
                    "announcement_url": "https://st.gtfund.com/GSGG/2026/03/30/828120866184.PDF",
                    "ratio_definition": "new_units_per_old_unit",
                    "provider_evidence": {
                        "provider": "tushare:fund_adj",
                        "raw_close_return_on_effective_date": -0.486747,
                        "adjusted_close_return_on_effective_date": 0.026506,
                    },
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "corporate_action_event_id": "ca-159516-sz-share-split-2026-07-10",
                "instrument_id": "159516-sz",
                "action_type": "share_split",
                "announcement_date": date(2026, 7, 6),
                "record_date": date(2026, 7, 9),
                "effective_date": date(2026, 7, 10),
                "payable_date": date(2026, 7, 10),
                "new_units": "2",
                "old_units": "1",
                "quantity_rounding": "truncate",
                "quantity_precision": 0,
                "cost_basis_treatment": "carry",
                "source": "distributor_mirror",
                "external_event_id": "cjsc-20260706-39064",
                "status": "confirmed",
                "provenance_json": {
                    "issuer": "国泰基金管理有限公司",
                    "announcement_title": "国泰中证半导体材料设备主题交易型开放式指数证券投资基金基金份额拆分公告",
                    "announcement_url": "https://www.cjsc.com.cn/ueditor/jsp/upload/file/20260706/1783305675637045205.pdf",
                    "notice_url": "https://www.cjsc.com.cn/main/a/20260706/39064.shtml",
                    "mirror_operator": "长江证券股份有限公司",
                    "ratio_definition": "new_units_per_old_unit",
                    "fractional_treatment": "truncate_to_integer_units",
                    "provider_evidence": {
                        "provider": "tushare:fund_adj",
                        "raw_close_return_on_effective_date": -0.534704,
                        "adjusted_close_return_on_effective_date": -0.069897,
                    },
                },
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_corporate_action_instrument_effective_date",
        table_name="corporate_action_event",
    )
    op.drop_table("corporate_action_event")
