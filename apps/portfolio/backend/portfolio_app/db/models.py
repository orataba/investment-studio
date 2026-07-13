from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from portfolio_app.db.base import Base


class PortfolioRecordModel(Base):
    __tablename__ = "portfolio_record"

    portfolio_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_name: Mapped[str] = mapped_column(String, nullable=False)
    base_currency: Mapped[str] = mapped_column(String, nullable=False)
    valuation_timezone: Mapped[str] = mapped_column(String, nullable=False)
    valuation_cutoff_policy: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    nav: Mapped[float | None] = mapped_column(default=0.0)
    day_change_value: Mapped[float | None] = mapped_column(default=0.0)
    day_change_pct: Mapped[float | None] = mapped_column(default=0.0)
    securities_count: Mapped[int] = mapped_column(nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    default_planning_taxonomy_id: Mapped[str | None] = mapped_column(String)
    risk_policy_json: Mapped[dict[str, object] | None] = mapped_column(JSON)

    accounts: Mapped[list["AccountRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["TransactionIdentityRecordModel"]] = relationship(
        back_populates="portfolio",
    )
    taxonomies: Mapped[list["TaxonomyRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    research_settings: Mapped["ResearchSettingsRecordModel | None"] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        uselist=False,
    )
    research_runs: Mapped[list["ResearchRunRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    daily_snapshots: Mapped[list["PortfolioDailySnapshotModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    daily_holding_snapshots: Mapped[list["PortfolioDailyHoldingSnapshotModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    daily_contribution_slices: Mapped[list["PortfolioDailyContributionSliceModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    calculation_state: Mapped["PortfolioCalculationStateModel | None"] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        uselist=False,
    )
    table_view_stores: Mapped[list["PortfolioTableViewStoreModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    instrument_universe: Mapped[list["PortfolioInstrumentUniverseRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )


class PortfolioDailySnapshotModel(Base):
    __tablename__ = "portfolio_daily_snapshot"
    __table_args__ = (
        CheckConstraint(
            "nav_coverage_state IN ('complete', 'partial', 'unavailable')",
            name="ck_portfolio_daily_snapshot_nav_coverage_state",
        ),
        CheckConstraint(
            "book_pnl_coverage_state IN ('complete', 'partial', 'unavailable')",
            name="ck_portfolio_daily_snapshot_book_pnl_coverage_state",
        ),
        Index(
            "ix_portfolio_daily_snapshot_portfolio_nav_coverage",
            "portfolio_id",
            "nav_coverage_state",
            "as_of_date",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav_coverage_state: Mapped[str] = mapped_column(String, nullable=False)
    nav_coverage_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    book_pnl_coverage_state: Mapped[str] = mapped_column(String, nullable=False)
    book_pnl_coverage_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    nav: Mapped[float | None]
    beginning_nav: Mapped[float | None]
    ending_nav: Mapped[float | None]
    daily_twr: Mapped[float | None]
    cumulative_twr: Mapped[float | None]
    drawdown: Mapped[float | None]
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    calculated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="daily_snapshots")


class PortfolioDailyHoldingSnapshotModel(Base):
    __tablename__ = "portfolio_daily_holding_snapshot"
    __table_args__ = (
        Index("ix_portfolio_daily_holding_portfolio_date", "portfolio_id", "as_of_date"),
        Index("ix_portfolio_daily_holding_instrument_date", "portfolio_id", "instrument_id", "as_of_date"),
        Index("ix_portfolio_daily_holding_account_date", "portfolio_id", "account_id", "as_of_date"),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(nullable=False, default=0.0)
    cost_basis: Mapped[float | None]
    cost_basis_base: Mapped[float | None]
    last_price: Mapped[float | None]
    market_value: Mapped[float | None]
    market_value_base: Mapped[float | None]
    portfolio_weight: Mapped[float | None]
    holding_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    calculated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="daily_holding_snapshots")


class PortfolioDailyContributionSliceModel(Base):
    __tablename__ = "portfolio_daily_contribution_slice"
    __table_args__ = (
        CheckConstraint(
            "nav_coverage_state IN ('complete', 'partial', 'unavailable')",
            name="ck_portfolio_daily_contribution_slice_nav_coverage_state",
        ),
        CheckConstraint(
            "book_pnl_coverage_state IN ('complete', 'partial', 'unavailable')",
            name="ck_portfolio_daily_contribution_slice_book_pnl_coverage_state",
        ),
        Index("ix_portfolio_daily_contribution_axis_date", "portfolio_id", "axis", "as_of_date"),
        Index("ix_portfolio_daily_contribution_group_date", "portfolio_id", "axis", "group_key", "as_of_date"),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    axis: Mapped[str] = mapped_column(String, primary_key=True)
    group_key: Mapped[str] = mapped_column(String, primary_key=True)
    group_label: Mapped[str] = mapped_column(String, nullable=False)
    nav_coverage_state: Mapped[str] = mapped_column(String, nullable=False)
    nav_coverage_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    book_pnl_coverage_state: Mapped[str] = mapped_column(String, nullable=False)
    book_pnl_coverage_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    total_pnl: Mapped[float | None]
    daily_contribution: Mapped[float | None]
    slice_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    calculated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="daily_contribution_slices")


class PortfolioCalculationStateModel(Base):
    __tablename__ = "portfolio_calculation_state"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_snapshot_status: Mapped[str] = mapped_column(String, nullable=False, default="stale")
    dirty_from: Mapped[date | None] = mapped_column(Date)
    refreshed_from: Mapped[date | None] = mapped_column(Date)
    refreshed_to: Mapped[date | None] = mapped_column(Date)
    refreshed_at: Mapped[str | None] = mapped_column(String)
    refresh_request_id: Mapped[str | None] = mapped_column(String)
    refresh_started_at: Mapped[str | None] = mapped_column(String)
    refresh_completed_at: Mapped[str | None] = mapped_column(String)
    source_market_data_updated_at: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="calculation_state")


class TransactionIdAllocatorModel(Base):
    """Database-coordinated allocator for the externally visible transaction id."""

    __tablename__ = "transaction_id_allocator"

    allocator_key: Mapped[str] = mapped_column(String, primary_key=True)
    next_value: Mapped[int] = mapped_column(nullable=False, default=1)


class PortfolioTableViewStoreModel(Base):
    __tablename__ = "portfolio_table_view_store"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    view_scope: Mapped[str] = mapped_column(String, primary_key=True)
    store_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="table_view_stores")


class PortfolioInstrumentUniverseRecordModel(Base):
    __tablename__ = "portfolio_instrument_universe_record"
    __table_args__ = (
        Index("ix_portfolio_instrument_universe_state", "portfolio_id", "holding_state", "status"),
        Index("ix_portfolio_instrument_universe_source", "portfolio_id", "source"),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_ref_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String, nullable=False, default="transaction")
    holding_state: Mapped[str] = mapped_column(String, nullable=False, default="not_held")
    first_transaction_date: Mapped[date | None] = mapped_column(Date)
    last_transaction_date: Mapped[date | None] = mapped_column(Date)
    transaction_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="instrument_universe")


class AccountRecordModel(Base):
    __tablename__ = "account_record"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            name="uq_account_record_portfolio_account",
        ),
        Index("ix_account_record_portfolio_type_currency", "portfolio_id", "account_type", "currency"),
        Index("ix_account_record_portfolio_name", "portfolio_id", "account_name"),
    )

    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_name: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    institution: Mapped[str | None] = mapped_column(String)
    default_settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    cost_basis_method: Mapped[str | None] = mapped_column(String)
    allowed_instrument_types_json: Mapped[list[str] | None] = mapped_column(JSON)
    opened_at: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="accounts")


class TransactionIdentityRecordModel(Base):
    __tablename__ = "transaction_identity_record"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            name="uq_transaction_identity_portfolio_transaction",
        ),
        Index(
            "ix_transaction_identity_portfolio_created",
            "portfolio_id",
            "created_at",
            "transaction_id",
        ),
        CheckConstraint(
            "length(trim(transaction_id)) > 0",
            name="transaction_id_nonblank",
        ),
        CheckConstraint(
            "length(trim(created_by)) > 0",
            name="created_by_nonblank",
        ),
    )

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="transactions")
    revisions: Mapped[list["TransactionRevisionRecordModel"]] = relationship(
        back_populates="transaction_identity",
        order_by="TransactionRevisionRecordModel.revision_number",
    )


class TransactionRevisionGroupRecordModel(Base):
    __tablename__ = "transaction_revision_group_record"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "revision_group_id",
            name="uq_transaction_revision_group_portfolio_group",
        ),
        CheckConstraint(
            "source_kind IN ('manual', 'import', 'reconciliation', 'migration', 'system')",
            name="source_kind",
        ),
        CheckConstraint(
            "length(trim(revision_group_id)) > 0",
            name="group_id_nonblank",
        ),
        CheckConstraint("length(trim(change_reason)) > 0", name="change_reason_nonblank"),
        CheckConstraint(
            "actor_type IN ('user', 'service', 'migration')",
            name="actor_type",
        ),
        CheckConstraint("length(trim(actor_id)) > 0", name="actor_id_nonblank"),
        CheckConstraint(
            "length(trim(actor_display_name)) > 0",
            name="actor_display_nonblank",
        ),
        CheckConstraint("length(trim(actor_source)) > 0", name="actor_source_nonblank"),
        CheckConstraint(
            "actor_source IN ('client_asserted', 'authenticated_principal', "
            "'trusted_service', 'migration')",
            name="actor_source",
        ),
        CheckConstraint(
            "((actor_type = 'user' AND actor_source IN "
            "('client_asserted', 'authenticated_principal')) OR "
            "(actor_type = 'service' AND actor_source = 'trusted_service') OR "
            "(actor_type = 'migration' AND actor_source = 'migration'))",
            name="actor_source_type",
        ),
        Index(
            "ix_transaction_revision_group_portfolio_recorded",
            "portfolio_id",
            "recorded_at",
            "revision_group_id",
        ),
        Index(
            "uq_transaction_revision_group_portfolio_idempotency",
            "portfolio_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    revision_group_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_record.portfolio_id",
            name="fk_txn_revision_group_portfolio",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    change_reason: Mapped[str] = mapped_column(String, nullable=False)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_display_name: Mapped[str] = mapped_column(String, nullable=False)
    actor_source: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str | None] = mapped_column(String)
    source_ref: Mapped[str | None] = mapped_column(String)

    revisions: Mapped[list["TransactionRevisionRecordModel"]] = relationship(
        back_populates="revision_group",
        overlaps="revisions",
    )


class TransactionRevisionRecordModel(Base):
    __tablename__ = "transaction_revision_record"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            "revision_number",
            name="uq_transaction_revision_portfolio_transaction_number",
        ),
        UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            "revision_number",
            "revision_id",
            name="uq_transaction_revision_chain_target",
        ),
        UniqueConstraint(
            "revision_group_id",
            "transaction_id",
            name="uq_transaction_revision_group_transaction",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "transaction_id"],
            [
                "transaction_identity_record.portfolio_id",
                "transaction_identity_record.transaction_id",
            ],
            name="fk_transaction_revision_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "revision_group_id"],
            [
                "transaction_revision_group_record.portfolio_id",
                "transaction_revision_group_record.revision_group_id",
            ],
            name="fk_transaction_revision_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "portfolio_id",
                "transaction_id",
                "supersedes_revision_number",
                "supersedes_revision_id",
            ],
            [
                "transaction_revision_record.portfolio_id",
                "transaction_revision_record.transaction_id",
                "transaction_revision_record.revision_number",
                "transaction_revision_record.revision_id",
            ],
            name="fk_transaction_revision_predecessor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "settlement_cash_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_settlement_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "counterparty_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_counterparty_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(revision_id)) > 0 AND length(trim(transaction_id)) > 0 "
            "AND length(trim(revision_group_id)) > 0",
            name="identifiers_nonblank",
        ),
        CheckConstraint("revision_number > 0", name="positive_revision_number"),
        CheckConstraint(
            "revision_kind IN ('baseline', 'create', 'amend', 'delete')",
            name="revision_kind",
        ),
        CheckConstraint(
            "is_tombstone = (revision_kind = 'delete')",
            name="tombstone_kind",
        ),
        CheckConstraint(
            "((revision_number = 1 AND revision_kind IN ('baseline', 'create') "
            "AND supersedes_revision_id IS NULL AND supersedes_revision_number IS NULL) "
            "OR (revision_number > 1 AND revision_kind IN ('amend', 'delete') "
            "AND supersedes_revision_id IS NOT NULL "
            "AND supersedes_revision_number = revision_number - 1))",
            name="revision_chain_shape",
        ),
        CheckConstraint(
            "length(payload_hash) = 71 AND payload_hash LIKE 'sha256:%'",
            name="payload_hash_shape",
        ),
        CheckConstraint(
            "payload_schema_version = 'transaction-revision.v1'",
            name="payload_schema_version",
        ),
        CheckConstraint(
            "NOT is_tombstone OR payload_hash = "
            "'sha256:bc5787018813b8e0562f74da4098be5dc135994b08a078955af74b9d11b7ef91'",
            name="tombstone_payload_hash",
        ),
        CheckConstraint(
            "((is_tombstone AND transaction_type IS NULL AND trade_date IS NULL "
            "AND trade_time IS NULL AND trade_at IS NULL AND trade_timezone IS NULL "
            "AND trade_time_is_estimated IS NULL AND settlement_date IS NULL "
            "AND entitlement_date IS NULL AND acquisition_date IS NULL "
            "AND account_id IS NULL AND settlement_cash_account_id IS NULL "
            "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
            "AND quantity IS NULL AND price IS NULL AND gross_amount IS NULL "
            "AND counter_amount IS NULL AND fx_rate IS NULL AND fees IS NULL "
            "AND taxes IS NULL AND currency IS NULL AND transfer_scope IS NULL "
            "AND transfer_object_type IS NULL AND transfer_group_id IS NULL "
            "AND counterparty_account_id IS NULL AND note IS NULL) OR "
            "(NOT is_tombstone AND transaction_type IS NOT NULL "
            "AND trade_date IS NOT NULL AND trade_time IS NOT NULL "
            "AND trade_at IS NOT NULL AND length(trim(trade_timezone)) > 0 "
            "AND trade_time_is_estimated IS NOT NULL AND settlement_date IS NOT NULL "
            "AND account_id IS NOT NULL AND gross_amount IS NOT NULL "
            "AND fees IS NOT NULL AND taxes IS NOT NULL AND currency IS NOT NULL))",
            name="tombstone_payload",
        ),
        CheckConstraint(
            "transaction_type IS NULL OR transaction_type IN "
            "('buy', 'sell', 'dividend', 'dividend_reinvestment', 'coupon', "
            "'interest', 'return_of_capital', 'maturity_redemption', 'fee', "
            "'tax', 'deposit', 'withdrawal', 'fx_conversion', 'transfer_in', "
            "'transfer_out', 'opening_balance')",
            name="transaction_type",
        ),
        CheckConstraint(
            "is_tombstone OR settlement_date >= trade_date",
            name="settlement_not_before_trade",
        ),
        CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR entitlement_date <= trade_date",
            name="entitlement_not_after_trade",
        ),
        CheckConstraint(
            "is_tombstone OR acquisition_date IS NULL OR acquisition_date <= trade_date",
            name="acquisition_not_after_trade",
        ),
        CheckConstraint(
            "is_tombstone OR currency IN ('USD', 'HKD', 'CNY')",
            name="supported_currency",
        ),
        CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="positive_quantity",
        ),
        CheckConstraint("price IS NULL OR price > 0", name="positive_price"),
        CheckConstraint("gross_amount IS NULL OR gross_amount >= 0", name="nonnegative_gross_amount"),
        CheckConstraint("counter_amount IS NULL OR counter_amount > 0", name="positive_counter_amount"),
        CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="positive_fx_rate"),
        CheckConstraint("fees IS NULL OR fees >= 0", name="nonnegative_fees"),
        CheckConstraint("taxes IS NULL OR taxes >= 0", name="nonnegative_taxes"),
        CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR transaction_type IN ('dividend', 'coupon', 'fee', 'tax')",
            name="entitlement_applicability",
        ),
        CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR "
            "transaction_type NOT IN ('fee', 'tax') OR instrument_id IS NOT NULL",
            name="expense_entitlement_instrument",
        ),
        CheckConstraint(
            "is_tombstone OR acquisition_date IS NULL OR "
            "(transaction_type = 'opening_balance' AND instrument_id IS NOT NULL)",
            name="acquisition_applicability",
        ),
        CheckConstraint(
            "is_tombstone OR ((transaction_type = 'fx_conversion' "
            "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
            "AND quantity IS NULL AND price IS NULL "
            "AND settlement_cash_account_id IS NULL "
            "AND counter_amount IS NOT NULL AND fx_rate IS NOT NULL "
            "AND counterparty_account_id IS NOT NULL AND fees = 0 AND taxes = 0) "
            "OR (transaction_type <> 'fx_conversion' AND counter_amount IS NULL "
            "AND fx_rate IS NULL))",
            name="fx_conversion_fields",
        ),
        CheckConstraint(
            "is_tombstone OR ((transaction_type IN ('transfer_in', 'transfer_out') "
            "AND transfer_scope = 'internal_portfolio' "
            "AND transfer_object_type IN ('cash', 'position') "
            "AND settlement_cash_account_id IS NULL "
            "AND transfer_group_id IS NOT NULL AND counterparty_account_id IS NOT NULL "
            "AND fees = 0 AND taxes = 0) "
            "OR (transaction_type NOT IN ('transfer_in', 'transfer_out') "
            "AND transfer_scope IS NULL AND transfer_object_type IS NULL "
            "AND transfer_group_id IS NULL "
            "AND (transaction_type = 'fx_conversion' OR counterparty_account_id IS NULL)))",
            name="transfer_fields",
        ),
        CheckConstraint(
            "is_tombstone OR ("
            "(transaction_type IN ('buy', 'sell') AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NOT NULL AND gross_amount > 0) OR "
            "(transaction_type IN ('dividend', 'coupon', 'return_of_capital') "
            "AND instrument_id IS NOT NULL AND quantity IS NULL AND price IS NULL) OR "
            "(transaction_type = 'dividend_reinvestment' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND gross_amount > 0 "
            "AND settlement_cash_account_id IS NULL AND fees = 0 AND taxes = 0) OR "
            "(transaction_type = 'maturity_redemption' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NULL) OR "
            "(transaction_type IN ('deposit', 'withdrawal', 'interest') "
            "AND instrument_id IS NULL AND quantity IS NULL AND price IS NULL "
            "AND settlement_cash_account_id IS NULL AND fees = 0 AND taxes = 0) OR "
            "(transaction_type = 'fx_conversion' AND gross_amount > 0) OR "
            "(transaction_type IN ('fee', 'tax') AND quantity IS NULL AND price IS NULL "
            "AND fees = 0 AND taxes = 0) OR "
            "(transaction_type IN ('transfer_in', 'transfer_out')) OR "
            "(transaction_type = 'opening_balance' AND settlement_cash_account_id IS NULL "
            "AND fees = 0 AND taxes = 0 AND ((instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND acquisition_date IS NOT NULL) OR "
            "(instrument_id IS NULL AND quantity IS NULL AND price IS NULL "
            "AND acquisition_date IS NULL))))",
            name="transaction_payload_type_shape",
        ),
        CheckConstraint(
            "is_tombstone OR transaction_type NOT IN ('transfer_in', 'transfer_out') OR "
            "((transfer_object_type = 'cash' AND instrument_id IS NULL "
            "AND quantity IS NULL AND price IS NULL AND gross_amount > 0) OR "
            "(transfer_object_type = 'position' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NULL AND gross_amount >= 0))",
            name="transfer_object_shape",
        ),
        CheckConstraint(
            "is_tombstone OR instrument_id IS NULL OR instrument_snapshot_json IS NOT NULL",
            name="instrument_snapshot_required",
        ),
        CheckConstraint(
            "is_tombstone OR instrument_id IS NOT NULL OR instrument_snapshot_json IS NULL",
            name="snapshot_without_instrument",
        ),
        Index(
            "ix_transaction_revision_portfolio_trade",
            "portfolio_id",
            "trade_date",
            "trade_at",
            "transaction_id",
            postgresql_where=text("is_tombstone IS FALSE"),
            sqlite_where=text("is_tombstone = 0"),
        ),
        Index(
            "ix_transaction_revision_portfolio_account_trade",
            "portfolio_id",
            "account_id",
            "trade_date",
            "trade_at",
            postgresql_where=text("is_tombstone IS FALSE"),
            sqlite_where=text("is_tombstone = 0"),
        ),
        Index(
            "ix_transaction_revision_portfolio_type_trade",
            "portfolio_id",
            "transaction_type",
            "trade_date",
            "trade_at",
            postgresql_where=text("is_tombstone IS FALSE"),
            sqlite_where=text("is_tombstone = 0"),
        ),
        Index(
            "ix_transaction_revision_portfolio_instrument_trade",
            "portfolio_id",
            "instrument_id",
            "trade_date",
            "trade_at",
            postgresql_where=text("is_tombstone IS FALSE"),
            sqlite_where=text("is_tombstone = 0"),
        ),
        Index(
            "ix_transaction_revision_portfolio_counterparty_trade",
            "portfolio_id",
            "counterparty_account_id",
            "trade_date",
            "trade_at",
            postgresql_where=text("is_tombstone IS FALSE"),
            sqlite_where=text("is_tombstone = 0"),
        ),
    )

    revision_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String, nullable=False)
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_group_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    is_tombstone: Mapped[bool] = mapped_column(Boolean, nullable=False)
    supersedes_revision_id: Mapped[str | None] = mapped_column(String)
    supersedes_revision_number: Mapped[int | None] = mapped_column(Integer)
    payload_schema_version: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)

    transaction_type: Mapped[str | None] = mapped_column(String)
    trade_date: Mapped[date | None] = mapped_column(Date)
    trade_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trade_timezone: Mapped[str | None] = mapped_column(String)
    trade_time_is_estimated: Mapped[bool | None] = mapped_column(Boolean)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    entitlement_date: Mapped[date | None] = mapped_column(Date)
    acquisition_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[str | None] = mapped_column(String)
    settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    instrument_id: Mapped[str | None] = mapped_column(String)
    instrument_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    counter_amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fees: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    taxes: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    currency: Mapped[str | None] = mapped_column(String(3))
    transfer_scope: Mapped[str | None] = mapped_column(String)
    transfer_object_type: Mapped[str | None] = mapped_column(String)
    transfer_group_id: Mapped[str | None] = mapped_column(String)
    counterparty_account_id: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(String)

    transaction_identity: Mapped[TransactionIdentityRecordModel] = relationship(
        back_populates="revisions",
        overlaps="revisions",
    )
    revision_group: Mapped[TransactionRevisionGroupRecordModel] = relationship(
        back_populates="revisions",
        overlaps="revisions,transaction_identity",
    )


class TransactionCurrentModel(Base):
    """Read-only ORM projection over the latest non-tombstone revision."""

    __tablename__ = "transaction_current"

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String, nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    current_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_group_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    payload_schema_version: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    change_reason: Mapped[str] = mapped_column(String, nullable=False)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_display_name: Mapped[str] = mapped_column(String, nullable=False)
    actor_source: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    trade_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_timezone: Mapped[str] = mapped_column(String, nullable=False)
    trade_time_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    entitlement_date: Mapped[date | None] = mapped_column(Date)
    acquisition_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    instrument_id: Mapped[str | None] = mapped_column(String)
    instrument_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(38, 8), nullable=False)
    counter_amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    fees: Mapped[Decimal] = mapped_column(Numeric(38, 8), nullable=False)
    taxes: Mapped[Decimal] = mapped_column(Numeric(38, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    transfer_scope: Mapped[str | None] = mapped_column(String)
    transfer_object_type: Mapped[str | None] = mapped_column(String)
    transfer_group_id: Mapped[str | None] = mapped_column(String)
    counterparty_account_id: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(String)


class TaxonomyRecordModel(Base):
    __tablename__ = "taxonomy_record"

    taxonomy_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    taxonomy_type: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str | None] = mapped_column(String)
    primary_assignment_scope: Mapped[str] = mapped_column(String, nullable=False)
    planning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    budgeting_level: Mapped[str | None] = mapped_column(String)
    root_default_target_dimension: Mapped[str] = mapped_column(String, nullable=False, default="weight")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    source_template_ref: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="taxonomies")
    nodes: Mapped[list["TaxonomyNodeRecordModel"]] = relationship(
        back_populates="taxonomy",
        cascade="all, delete-orphan",
    )
    assignments: Mapped[list["TaxonomyAssignmentRecordModel"]] = relationship(
        back_populates="taxonomy",
        cascade="all, delete-orphan",
    )
    target_sets: Mapped[list["TargetSetRecordModel"]] = relationship(
        back_populates="taxonomy",
        cascade="all, delete-orphan",
    )


class TaxonomyNodeRecordModel(Base):
    __tablename__ = "taxonomy_node_record"

    taxonomy_node_id: Mapped[str] = mapped_column(String, primary_key=True)
    taxonomy_id: Mapped[str] = mapped_column(
        ForeignKey("taxonomy_record.taxonomy_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_taxonomy_node_id: Mapped[str | None] = mapped_column(String)
    node_name: Mapped[str] = mapped_column(String, nullable=False)
    node_code: Mapped[str | None] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_target_dimension: Mapped[str] = mapped_column(String, nullable=False, default="weight")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    taxonomy: Mapped[TaxonomyRecordModel] = relationship(back_populates="nodes")


class TaxonomyAssignmentRecordModel(Base):
    __tablename__ = "taxonomy_assignment_record"
    __table_args__ = (
        Index(
            "uq_taxonomy_assignment_target",
            "taxonomy_id",
            "target_scope",
            "target_entity_id",
            unique=True,
        ),
    )

    assignment_id: Mapped[str] = mapped_column(String, primary_key=True)
    taxonomy_id: Mapped[str] = mapped_column(
        ForeignKey("taxonomy_record.taxonomy_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_scope: Mapped[str] = mapped_column(String, nullable=False)
    target_entity_id: Mapped[str] = mapped_column(String, nullable=False)
    taxonomy_node_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    taxonomy: Mapped[TaxonomyRecordModel] = relationship(back_populates="assignments")


class TargetSetRecordModel(Base):
    __tablename__ = "target_set_record"
    __table_args__ = (
        Index(
            "ix_target_set_record_taxonomy_scope_type",
            "taxonomy_id",
            "comparator_taxonomy_node_id",
            "target_set_type",
            "target_set_id",
        ),
        Index(
            "uq_target_set_active_scope",
            "taxonomy_id",
            text("coalesce(comparator_taxonomy_node_id, '')"),
            "target_set_type",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    target_set_id: Mapped[str] = mapped_column(String, primary_key=True)
    taxonomy_id: Mapped[str] = mapped_column(
        ForeignKey("taxonomy_record.taxonomy_id", ondelete="CASCADE"),
        nullable=False,
    )
    comparator_taxonomy_node_id: Mapped[str | None] = mapped_column(String)
    target_set_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    weight_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_budget_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(String)

    taxonomy: Mapped[TaxonomyRecordModel] = relationship(back_populates="target_sets")
    lines: Mapped[list["TargetSetLineRecordModel"]] = relationship(
        back_populates="target_set",
        cascade="all, delete-orphan",
    )


class TargetSetLineRecordModel(Base):
    __tablename__ = "target_set_line_record"
    __table_args__ = (
        Index(
            "ix_target_set_line_record_target_set_node",
            "target_set_id",
            "taxonomy_node_id",
            unique=True,
        ),
        Index(
            "ix_target_set_line_record_target_set_member",
            "target_set_id",
            "target_member_type",
            "target_member_id",
            unique=True,
        ),
    )

    target_line_id: Mapped[str] = mapped_column(String, primary_key=True)
    target_set_id: Mapped[str] = mapped_column(
        ForeignKey("target_set_record.target_set_id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_node_id: Mapped[str | None] = mapped_column(String)
    target_member_type: Mapped[str] = mapped_column(String, nullable=False, default="taxonomy_node")
    target_member_id: Mapped[str] = mapped_column(String, nullable=False)
    target_weight: Mapped[float | None]
    target_risk_share: Mapped[float | None]
    notes: Mapped[str | None] = mapped_column(String)

    target_set: Mapped[TargetSetRecordModel] = relationship(back_populates="lines")


class ResearchSettingsRecordModel(Base):
    __tablename__ = "research_settings_record"
    __table_args__ = (
        CheckConstraint(
            "as_of_mode IN ('dynamic', 'pinned')",
            name="ck_research_settings_as_of_mode",
        ),
        CheckConstraint(
            "lookback_days IN (30, 90, 180, 366, 730)",
            name="ck_research_settings_supported_lookback",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    planning_taxonomy_id: Mapped[str | None] = mapped_column(String)
    comparator_taxonomy_node_id: Mapped[str | None] = mapped_column(String)
    as_of_mode: Mapped[str] = mapped_column(String, nullable=False, default="dynamic")
    as_of_date: Mapped[date | None] = mapped_column(Date)
    lookback_days: Mapped[int] = mapped_column(nullable=False, default=90)
    calculation_frequency: Mapped[str] = mapped_column(String, nullable=False, default="auto")
    missing_return_policy: Mapped[str] = mapped_column(String, nullable=False, default="strict")
    target_dimension: Mapped[str] = mapped_column(String, nullable=False, default="scope_default")
    capital_mode: Mapped[str] = mapped_column(String, nullable=False, default="unit_notional")
    gross_exposure: Mapped[float | None]
    target_volatility: Mapped[float | None]
    max_gross_exposure: Mapped[float | None]
    frozen_taxonomy_node_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    top_sleeve_weight_bounds_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    backtest_rebalance_frequency: Mapped[str] = mapped_column(String, nullable=False, default="1m")
    backtest_benchmark_instrument_id: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="research_settings")


class ResearchRunRecordModel(Base):
    __tablename__ = "research_run_record"
    __table_args__ = (
        Index("ix_research_run_record_portfolio_requested", "portfolio_id", "requested_at", "research_run_id"),
        CheckConstraint(
            "lookback_days IN (30, 90, 180, 366, 730)",
            name="ck_research_run_supported_lookback",
        ),
    )

    research_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String, nullable=False, default="target_weight_solve")
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    requested_at: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    planning_taxonomy_id: Mapped[str | None] = mapped_column(String)
    lookback_days: Mapped[int] = mapped_column(nullable=False, default=90)
    requested_by: Mapped[str | None] = mapped_column(String)
    headline: Mapped[str | None] = mapped_column(String)
    detail_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    artifacts_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    request_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="research_runs")
