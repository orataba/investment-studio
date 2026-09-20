from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    and_,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
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
    inception_date: Mapped[date] = mapped_column(Date, nullable=False)
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
    transactions: Mapped[list["TransactionRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    transaction_captures: Mapped[list["TransactionCaptureRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    transaction_capture_batches: Mapped[list["TransactionCaptureBatchModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    derivative_contracts: Mapped[list["DerivativeContractRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
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
    instrument_event_tasks: Mapped[list["PortfolioInstrumentEventTaskModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    analytics_policy_state: Mapped["PortfolioAnalyticsPolicyStateModel | None"] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        uselist=False,
    )
    analytics_scope_policies: Mapped[list["AnalyticsScopePolicyRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    analytics_taxonomy_selections: Mapped[list["AnalyticsTaxonomySelectionRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    taxonomy_configuration_revisions: Mapped[list["TaxonomyConfigurationRevisionModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )


class PortfolioDailySnapshotModel(Base):
    __tablename__ = "portfolio_daily_snapshot"
    __table_args__ = (
        Index("ix_portfolio_daily_snapshot_portfolio_coverage", "portfolio_id", "coverage_state", "as_of_date"),
        Index(
            "ix_portfolio_daily_snapshot_portfolio_valuation_coverage",
            "portfolio_id",
            "valuation_coverage_state",
            "as_of_date",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    coverage_state: Mapped[str] = mapped_column(String, nullable=False)
    valuation_coverage_state: Mapped[str] = mapped_column(String, nullable=False, default="unavailable")
    return_coverage_state: Mapped[str] = mapped_column(String, nullable=False, default="unavailable")
    book_pnl_coverage_state: Mapped[str] = mapped_column(String, nullable=False, default="unavailable")
    attribution_coverage_state: Mapped[str] = mapped_column(String, nullable=False, default="unavailable")
    nav: Mapped[float | None]
    beginning_nav: Mapped[float | None]
    ending_nav: Mapped[float | None]
    daily_twr: Mapped[float | None]
    cumulative_twr: Mapped[float | None]
    drawdown: Mapped[float | None]
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    calculation_state_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON, deferred=True,
    )
    calculated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="daily_snapshots")


class PortfolioDailyHoldingSnapshotModel(Base):
    __tablename__ = "portfolio_daily_holding_snapshot"
    __table_args__ = (
        Index("ix_portfolio_daily_holding_portfolio_date", "portfolio_id", "as_of_date"),
        Index("ix_portfolio_daily_holding_instrument_date", "portfolio_id", "instrument_id", "as_of_date"),
        Index(
            "ix_portfolio_daily_holding_derivative_date",
            "portfolio_id",
            "derivative_contract_id",
            "as_of_date",
        ),
        Index("ix_portfolio_daily_holding_account_date", "portfolio_id", "account_id", "as_of_date"),
        CheckConstraint(
            "(instrument_id IS NOT NULL) <> (derivative_contract_id IS NOT NULL)",
            name="single_asset_reference",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "derivative_contract_id"],
            [
                "derivative_contract_record.portfolio_id",
                "derivative_contract_record.derivative_contract_id",
            ],
            name="fk_daily_holding_derivative_contract_portfolio",
            ondelete="CASCADE",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_record.portfolio_id",
            name="fk_portfolio_holding_snapshot_portfolio",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    position_reference_id: Mapped[str] = mapped_column(String, primary_key=True)
    holding_kind: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str | None] = mapped_column(String)
    derivative_contract_id: Mapped[str | None] = mapped_column(String)
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
    coverage_state: Mapped[str] = mapped_column(String, nullable=False)
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
    source_calculation_inputs_updated_at: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="calculation_state")


class PortfolioWorkspaceReadModel(Base):
    """Latest published page analysis; source facts remain in their owning tables."""

    __tablename__ = "portfolio_workspace_read_model"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True,
    )
    surface: Mapped[str] = mapped_column(String, primary_key=True)
    source_key: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    calculated_at: Mapped[str] = mapped_column(String, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String)


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
    research_pm_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    research_pm_approved_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="instrument_universe")


class AccountRecordModel(Base):
    __tablename__ = "account_record"
    __table_args__ = (
        CheckConstraint(
            "account_category IN ('cash', 'security', 'fcn', 'option')",
            name="account_category",
        ),
        CheckConstraint(
            "(account_type = 'deposit_account' AND account_category = 'cash') OR "
            "(account_type = 'securities_account' "
            "AND account_category IN ('security', 'fcn', 'option'))",
            name="account_type_category",
        ),
        CheckConstraint(
            "(account_category = 'cash' "
            "AND default_settlement_cash_account_id IS NULL "
            "AND cost_basis_method IS NULL) OR "
            "(account_category IN ('security', 'fcn', 'option') "
            "AND default_settlement_cash_account_id IS NOT NULL "
            "AND cost_basis_method IN ('fifo', 'moving_average'))",
            name="account_settlement_contract",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "default_settlement_cash_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_account_default_settlement_cash_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            name="uq_account_record_portfolio_account_id",
        ),
        Index(
            "ix_account_record_portfolio_category_currency",
            "portfolio_id",
            "account_category",
            "currency",
        ),
        Index("ix_account_record_portfolio_name", "portfolio_id", "account_name"),
    )

    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_name: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    account_category: Mapped[str] = mapped_column(String, nullable=False)
    cash_purpose: Mapped[str | None] = mapped_column(String)
    collateral_reference: Mapped[str | None] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    institution: Mapped[str | None] = mapped_column(String)
    default_settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    cost_basis_method: Mapped[str | None] = mapped_column(String)
    opened_at: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="accounts")


class DerivativeContractRecordModel(Base):
    __tablename__ = "derivative_contract_record"
    __table_args__ = (
        CheckConstraint(
            "contract_type IN ('fcn', 'option')",
            name="contract_type",
        ),
        CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="currency",
        ),
        UniqueConstraint(
            "portfolio_id",
            "external_reference",
            name="uq_derivative_contract_external_reference",
        ),
        Index(
            "ix_derivative_contract_portfolio_account_type",
            "portfolio_id",
            "account_id",
            "contract_type",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_derivative_contract_portfolio_account",
            ondelete="RESTRICT",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    derivative_contract_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    contract_name: Mapped[str] = mapped_column(String, nullable=False)
    contract_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(200))
    terms_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    amendments_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="derivative_contracts"
    )


class TransactionRecordModel(Base):
    __tablename__ = "transaction_record"
    __table_args__ = (
        CheckConstraint(
            "position_effective_date IS NULL OR ("
            "transaction_type IN ('buy', 'sell', 'dividend_reinvestment', "
            "'maturity_redemption', 'short_sell', 'buy_to_cover') "
            "AND position_effective_date >= trade_date"
            ")",
            name="position_effective_date",
        ),
        Index(
            "ix_transaction_record_portfolio_trade_sort",
            "portfolio_id",
            "trade_date",
            "trade_at",
            "created_at",
            "transaction_sequence",
        ),
        Index("ix_transaction_record_portfolio_account_trade", "portfolio_id", "account_id", "trade_date", "trade_at"),
        Index(
            "ix_transaction_record_portfolio_counterparty_trade",
            "portfolio_id",
            "counterparty_account_id",
            "trade_date",
            "trade_at",
        ),
        Index("ix_transaction_record_portfolio_type_trade", "portfolio_id", "transaction_type", "trade_date", "trade_at"),
        Index("ix_transaction_record_portfolio_instrument_trade", "portfolio_id", "instrument_id", "trade_date", "trade_at"),
        Index(
            "ix_transaction_record_portfolio_derivative_contract_trade",
            "portfolio_id",
            "derivative_contract_id",
            "trade_date",
            "trade_at",
        ),
        CheckConstraint(
            "NOT (instrument_id IS NOT NULL AND derivative_contract_id IS NOT NULL)",
            name="single_asset_reference",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "derivative_contract_id"],
            [
                "derivative_contract_record.portfolio_id",
                "derivative_contract_record.derivative_contract_id",
            ],
            name="fk_transaction_derivative_contract_portfolio",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_portfolio_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "settlement_cash_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_portfolio_settlement_cash_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["portfolio_id", "counterparty_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_portfolio_counterparty_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "portfolio_id",
            "source_system",
            "external_reference",
            name="uq_transaction_record_portfolio_source_external",
        ),
        CheckConstraint(
            "external_reference IS NULL OR source_system IS NOT NULL",
            name="source_identity",
        ),
        CheckConstraint(
            "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
            "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
            "'option_long_expiry', 'option_long_cash_settlement', "
            "'option_long_exercise', 'option_writer_expiry', "
            "'option_writer_cash_settlement', 'option_writer_assignment')",
            name="lifecycle_event_type",
        ),
        Index(
            "ix_transaction_record_portfolio_position_effective",
            "portfolio_id",
            "position_effective_date",
        ),
        Index(
            "uq_transaction_record_transaction_sequence",
            "transaction_sequence",
            unique=True,
        ),
    )

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    lifecycle_event_type: Mapped[str | None] = mapped_column(String)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_time: Mapped[str] = mapped_column(String, nullable=False)
    trade_at: Mapped[str] = mapped_column(String, nullable=False)
    trade_timezone: Mapped[str] = mapped_column(String, nullable=False)
    trade_time_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    position_effective_date: Mapped[date | None] = mapped_column(Date)
    entitlement_date: Mapped[date | None] = mapped_column(Date)
    acquisition_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    instrument_id: Mapped[str | None] = mapped_column(String)
    instrument_ref_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    asset_deliveries_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    settlement_cashflows_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    lot_selections_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    derivative_contract_id: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[float | None]
    source_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    price: Mapped[float | None]
    source_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    gross_amount: Mapped[float] = mapped_column(nullable=False, default=0.0)
    source_gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    counter_amount: Mapped[float | None]
    source_counter_amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    fx_rate: Mapped[float | None]
    source_fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    fees: Mapped[float] = mapped_column(nullable=False, default=0.0)
    source_fees: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    fee_category: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    taxes: Mapped[float] = mapped_column(nullable=False, default=0.0)
    source_taxes: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    currency: Mapped[str] = mapped_column(String, nullable=False)
    transfer_scope: Mapped[str | None] = mapped_column(String)
    transfer_object_type: Mapped[str | None] = mapped_column(String)
    transfer_group_id: Mapped[str | None] = mapped_column(String)
    counterparty_account_id: Mapped[str | None] = mapped_column(String)
    source_system: Mapped[str | None] = mapped_column(String(100))
    external_reference: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String)
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="transactions")
    derivative_contract: Mapped[DerivativeContractRecordModel | None] = relationship(
        primaryjoin=lambda: and_(
            TransactionRecordModel.portfolio_id
            == DerivativeContractRecordModel.portfolio_id,
            TransactionRecordModel.derivative_contract_id
            == DerivativeContractRecordModel.derivative_contract_id,
        ),
        foreign_keys=lambda: [
            TransactionRecordModel.portfolio_id,
            TransactionRecordModel.derivative_contract_id,
        ],
        viewonly=True,
    )


class OptionDeliveryLinkModel(Base):
    """Strict one-to-one link between an option outcome and its stock delivery."""

    __tablename__ = "option_delivery_link"
    __table_args__ = (
        CheckConstraint(
            "option_transaction_id <> stock_transaction_id",
            name="distinct_transactions",
        ),
        UniqueConstraint(
            "stock_transaction_id",
            name="uq_option_delivery_link_stock_transaction",
        ),
        Index(
            "ix_option_delivery_link_portfolio_underlying",
            "portfolio_id",
            "underlying_instrument_id",
        ),
    )

    option_transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_record.transaction_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    stock_transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_record.transaction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    underlying_instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class TransactionChangeLogModel(Base):
    __tablename__ = "transaction_change_log"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('create', 'update', 'delete')",
            name="ck_transaction_change_log_type",
        ),
        Index(
            "ix_transaction_change_log_portfolio_transaction_changed",
            "portfolio_id",
            "transaction_id",
            "changed_at",
        ),
    )

    change_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    request_idempotency_key: Mapped[str | None] = mapped_column(String)
    changed_at: Mapped[str] = mapped_column(String, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String)
    actor_name: Mapped[str | None] = mapped_column(String)


class TransactionIdempotencyRecordModel(Base):
    __tablename__ = "transaction_idempotency_record"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    transaction_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class TransactionCaptureRecordModel(Base):
    """Original screenshot evidence kept outside the transaction ledger."""

    __tablename__ = "transaction_capture_record"
    __table_args__ = (
        CheckConstraint(
            "byte_size > 0",
            name="size",
        ),
        UniqueConstraint(
            "portfolio_id",
            "content_sha256",
            name="uq_transaction_capture_portfolio_content",
        ),
        Index(
            "ix_transaction_capture_portfolio_created",
            "portfolio_id",
            "created_at",
            "capture_id",
        ),
    )

    capture_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        deferred=True,
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="transaction_captures"
    )
    batch_links: Mapped[list["TransactionCaptureBatchItemModel"]] = relationship(
        back_populates="capture",
        cascade="all, delete-orphan",
    )


class TransactionCaptureBatchModel(Base):
    """A reusable, ordered group of screenshots interpreted as one agent task."""

    __tablename__ = "transaction_capture_batch"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('auto', 'transaction_import', 'portfolio_initialization', "
            "'position_reconciliation')",
            name="purpose",
        ),
        CheckConstraint(
            "status IN ('ready', 'review_required')",
            name="status",
        ),
        CheckConstraint(
            "analysis_run_status IN ('idle', 'queued', 'running', 'succeeded', 'failed')",
            name="analysis_run_status",
        ),
        CheckConstraint(
            "capture_count > 0 AND latest_analysis_revision >= 0 "
            "AND analysis_run_attempt >= 0",
            name="count_and_revision",
        ),
        UniqueConstraint(
            "portfolio_id",
            "content_key",
            name="uq_transaction_capture_batch_portfolio_content",
        ),
        Index(
            "ix_transaction_capture_batch_portfolio_created",
            "portfolio_id",
            "created_at",
            "batch_id",
        ),
    )

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="ready",
        server_default="ready",
    )
    content_key: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_count: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_analysis_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_run_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="idle",
        server_default="idle",
    )
    analysis_run_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_run_started_at: Mapped[str | None] = mapped_column(String)
    analysis_run_completed_at: Mapped[str | None] = mapped_column(String)
    analysis_run_error: Mapped[str | None] = mapped_column(String(1_000))
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="transaction_capture_batches"
    )
    items: Mapped[list["TransactionCaptureBatchItemModel"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="TransactionCaptureBatchItemModel.ordinal",
    )
    analysis_revisions: Mapped[list["TransactionCaptureAnalysisRevisionModel"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class TransactionCaptureBatchItemModel(Base):
    """Ordered evidence membership; one screenshot may support several analyses."""

    __tablename__ = "transaction_capture_batch_item"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ordinal"),
        UniqueConstraint(
            "batch_id",
            "ordinal",
            name="uq_transaction_capture_batch_item_ordinal",
        ),
    )

    batch_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_capture_batch.batch_id", ondelete="CASCADE"),
        primary_key=True,
    )
    capture_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_capture_record.capture_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    batch: Mapped[TransactionCaptureBatchModel] = relationship(back_populates="items")
    capture: Mapped[TransactionCaptureRecordModel] = relationship(back_populates="batch_links")


class TransactionCaptureAnalysisRevisionModel(Base):
    """Immutable structured results produced by a harness or a human reviewer."""

    __tablename__ = "transaction_capture_analysis_revision"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("source IN ('assistant', 'human')", name="source"),
        CheckConstraint(
            "(preview_digest IS NULL AND preview_error_count IS NULL AND preview_json IS NULL) "
            "OR (preview_digest IS NOT NULL AND preview_error_count IS NOT NULL "
            "AND preview_json IS NOT NULL)",
            name="preview_bundle",
        ),
    )

    batch_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_capture_batch.batch_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    harness: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(120))
    harness_session_id: Mapped[str | None] = mapped_column(String(255))
    finish_reason: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    analysis_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    transaction_import_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    preview_digest: Mapped[str | None] = mapped_column(String(64))
    preview_error_count: Mapped[int | None] = mapped_column(Integer)
    preview_json: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    batch: Mapped[TransactionCaptureBatchModel] = relationship(
        back_populates="analysis_revisions"
    )


class PortfolioInstrumentEventTaskModel(Base):
    """Portfolio/account review projection for one stable Registry action."""

    __tablename__ = "portfolio_instrument_event_task"
    __table_args__ = (
        CheckConstraint(
            "source_event_state IN ('active', 'cancelled')",
            name="source_state",
        ),
        CheckConstraint(
            "source_revision_kind IN ('original', 'correction', 'cancellation')",
            name="revision_kind",
        ),
        CheckConstraint(
            "resolution_status IN ('pending', 'processed', 'not_applicable')",
            name="resolution_status",
        ),
        CheckConstraint(
            "CAST(entitled_quantity AS NUMERIC) >= 0",
            name="entitled_quantity",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="row_version",
        ),
        CheckConstraint(
            "length(trim(event_source)) > 0 "
            "AND length(trim(event_action_id)) > 0 "
            "AND length(trim(current_event_revision_id)) > 0 "
            "AND length(trim(event_type)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0",
            name="identity",
        ),
        UniqueConstraint(
            "portfolio_id",
            "account_id",
            "event_source",
            "event_action_id",
            name="uq_portfolio_instrument_event_task_scope_action",
        ),
        Index(
            "ix_portfolio_instrument_event_task_attention",
            "portfolio_id",
            "source_event_state",
            "resolution_status",
            "effective_date",
        ),
        Index(
            "ix_portfolio_instrument_event_task_instrument",
            "portfolio_id",
            "instrument_id",
            "effective_date",
        ),
    )

    instrument_event_task_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("account_record.account_id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    event_source: Mapped[str] = mapped_column(String, nullable=False)
    event_action_id: Mapped[str] = mapped_column(String, nullable=False)
    current_event_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    source_revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    source_event_state: Mapped[str] = mapped_column(String, nullable=False)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    payable_date: Mapped[date | None] = mapped_column(Date)
    cash_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    unit_ratio: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    reinvestment_nav: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    entitled_quantity: Mapped[Decimal] = mapped_column(
        Numeric(28, 12),
        nullable=False,
        default=Decimal("0"),
    )
    resolution_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
    )
    reviewed_event_revision_id: Mapped[str | None] = mapped_column(String)
    resolution_note: Mapped[str | None] = mapped_column(String)
    resolved_by: Mapped[str | None] = mapped_column(String)
    resolved_at: Mapped[str | None] = mapped_column(String)
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="instrument_event_tasks"
    )


class PortfolioInstrumentEventTaskLinkModel(Base):
    """Current link from an event review task to an existing ledger fact."""

    __tablename__ = "portfolio_instrument_event_task_link"
    __table_args__ = (
        CheckConstraint(
            "link_role IN ('distribution', 'reinvestment', 'reinvestment_purchase')",
            name="role",
        ),
        UniqueConstraint(
            "instrument_event_task_id",
            "transaction_id",
            name="uq_portfolio_instrument_event_task_link_transaction",
        ),
        Index(
            "ix_portfolio_instrument_event_task_link_transaction",
            "transaction_id",
            "instrument_event_task_id",
        ),
    )

    instrument_event_task_link_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_event_task_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_instrument_event_task.instrument_event_task_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transaction_record.transaction_id", ondelete="CASCADE"),
        nullable=False,
    )
    link_role: Mapped[str] = mapped_column(String, nullable=False)
    linked_event_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    linked_by: Mapped[str] = mapped_column(String, nullable=False)
    linked_at: Mapped[str] = mapped_column(String, nullable=False)


class PortfolioInstrumentEventTaskReviewModel(Base):
    """Immutable human review history for a Portfolio event task."""

    __tablename__ = "portfolio_instrument_event_task_review"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('processed', 'not_applicable', 'reopened')",
            name="decision",
        ),
        Index(
            "ix_portfolio_instrument_event_task_review_task_time",
            "instrument_event_task_id",
            "reviewed_at",
        ),
    )

    instrument_event_task_review_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_event_task_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_instrument_event_task.instrument_event_task_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    linked_transaction_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(String, nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String, nullable=False)
    reviewed_at: Mapped[str] = mapped_column(String, nullable=False)


class TaxonomyRecordModel(Base):
    __tablename__ = "taxonomy_record"
    __table_args__ = (
        CheckConstraint(
            "primary_assignment_scope = 'instrument'",
            name="ck_taxonomy_record_security_scope",
        ),
    )

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
        CheckConstraint(
            "target_scope = 'instrument'",
            name="ck_taxonomy_assignment_security_scope",
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


class PortfolioAnalyticsPolicyStateModel(Base):
    __tablename__ = "portfolio_analytics_policy_state"
    __table_args__ = (
        CheckConstraint(
            "current_version >= 0",
            name="ck_analytics_policy_state_version",
        ),
    )

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="analytics_policy_state"
    )


class AnalyticsScopePolicyRecordModel(Base):
    __tablename__ = "analytics_scope_policy_record"
    __table_args__ = (
        CheckConstraint(
            "performance_scope IN ('ordinary', 'derivative_lifecycle', "
            "'operational_only', 'unallocated')",
            name="ck_analytics_scope_policy_performance_scope",
        ),
        CheckConstraint(
            "valuation_basis IN ('market', 'fair_value', 'carrying', 'event', "
            "'obligation', 'cash', 'unknown')",
            name="ck_analytics_scope_policy_valuation_basis",
        ),
        UniqueConstraint(
            "portfolio_id",
            "policy_version",
            name="uq_analytics_scope_policy_portfolio_version",
        ),
        Index(
            "ix_analytics_scope_policy_resolve",
            "portfolio_id",
            "taxonomy_id",
            "taxonomy_node_id",
            "superseded_by_policy_id",
        ),
        Index(
            "uq_analytics_scope_policy_current",
            "portfolio_id",
            "taxonomy_id",
            "taxonomy_node_id",
            unique=True,
            postgresql_where=text("superseded_by_policy_id IS NULL"),
            sqlite_where=text("superseded_by_policy_id IS NULL"),
        ),
    )

    analytics_scope_policy_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    taxonomy_node_id: Mapped[str] = mapped_column(String, nullable=False)
    risk_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    risk_budget_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    performance_scope: Mapped[str] = mapped_column(String, nullable=False)
    valuation_basis: Mapped[str] = mapped_column(String, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_by_policy_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="analytics_scope_policies"
    )


class AnalyticsTaxonomySelectionRecordModel(Base):
    __tablename__ = "analytics_taxonomy_selection_record"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "selection_version",
            name="uq_analytics_taxonomy_selection_portfolio_version",
        ),
        Index(
            "ix_analytics_taxonomy_selection_resolve",
            "portfolio_id",
            "superseded_by_selection_id",
        ),
        Index(
            "uq_analytics_taxonomy_selection_current",
            "portfolio_id",
            unique=True,
            postgresql_where=text("superseded_by_selection_id IS NULL"),
            sqlite_where=text("superseded_by_selection_id IS NULL"),
        ),
    )

    analytics_taxonomy_selection_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_id: Mapped[str | None] = mapped_column(String)
    selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_by_selection_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="analytics_taxonomy_selections"
    )


class TaxonomyConfigurationRevisionModel(Base):
    __tablename__ = "taxonomy_configuration_revision"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "configuration_version",
            name="uq_taxonomy_configuration_revision_portfolio_version",
        ),
        Index(
            "ix_taxonomy_configuration_revision_resolve",
            "portfolio_id",
            "taxonomy_id",
            "superseded_by_revision_id",
        ),
        Index(
            "uq_taxonomy_configuration_revision_current",
            "portfolio_id",
            "taxonomy_id",
            unique=True,
            postgresql_where=text("superseded_by_revision_id IS NULL"),
            sqlite_where=text("superseded_by_revision_id IS NULL"),
        ),
    )

    taxonomy_configuration_revision_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    superseded_by_revision_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    portfolio: Mapped[PortfolioRecordModel] = relationship(
        back_populates="taxonomy_configuration_revisions"
    )


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
        CheckConstraint(
            "target_member_type IN ('taxonomy_node', 'instrument', 'cash_bucket', 'derivative_bucket')",
            name="ck_target_set_line_member_type",
        ),
        CheckConstraint(
            "target_member_type NOT IN ('cash_bucket', 'derivative_bucket') OR target_risk_share IS NULL",
            name="ck_target_set_line_non_risk_member_risk_null",
        ),
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
        CheckConstraint(
            "calculation_frequency = 'daily'",
            name="calculation_frequency",
        ),
        CheckConstraint(
            "backtest_cash_yield_annual >= -1 AND "
            "backtest_commission_bps >= 0 AND backtest_tax_bps >= 0 AND "
            "backtest_slippage_bps >= 0 AND backtest_implementation_delay_days >= 0",
            name="research_backtest_execution_costs",
        ),
        CheckConstraint(
            "backtest_walk_forward_training_months > 0 AND "
            "backtest_walk_forward_test_months > 0",
            name="research_backtest_walk_forward_windows",
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
    calculation_frequency: Mapped[str] = mapped_column(String, nullable=False, default="daily")
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
    backtest_cash_yield_annual: Mapped[float] = mapped_column(
        nullable=False,
        default=0.02,
        server_default="0.02",
    )
    backtest_commission_bps: Mapped[float] = mapped_column(
        nullable=False,
        default=2.0,
        server_default="2",
    )
    backtest_tax_bps: Mapped[float] = mapped_column(
        nullable=False,
        default=10.0,
        server_default="10",
    )
    backtest_slippage_bps: Mapped[float] = mapped_column(
        nullable=False,
        default=5.0,
        server_default="5",
    )
    backtest_implementation_delay_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
    )
    backtest_robustness_scenarios_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    backtest_walk_forward_training_months: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="24",
    )
    backtest_walk_forward_test_months: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="6",
    )
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


class PortfolioAccessStateModel(Base):
    __tablename__ = "portfolio_access_state"
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False, index=True)


class PortfolioMembershipModel(Base):
    __tablename__ = "portfolio_membership"
    __table_args__ = (CheckConstraint("role IN ('manager', 'editor', 'viewer')", name="ck_portfolio_membership_role"),)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    granted_by: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[str] = mapped_column(String, nullable=False)


class PortfolioAccessAuditModel(Base):
    __tablename__ = "portfolio_access_audit"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Retained after a portfolio is deleted, unlike its membership rows.
    portfolio_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String)
    actor_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)


class PortfolioUserPreferenceModel(Base):
    __tablename__ = "portfolio_user_preference"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    preference_key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ConcentrationPolicyRevisionModel(Base):
    __tablename__ = "concentration_policy_revision"
    __table_args__ = (
        CheckConstraint("revision > 0", name="positive_revision"),
        Index("ix_concentration_policy_effective", "portfolio_id", "effective_from", "revision"),
    )
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
