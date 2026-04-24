from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, JSON, String
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
    nav: Mapped[float] = mapped_column(nullable=False, default=0.0)
    day_change_value: Mapped[float] = mapped_column(nullable=False, default=0.0)
    day_change_pct: Mapped[float] = mapped_column(nullable=False, default=0.0)
    securities_count: Mapped[int] = mapped_column(nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    default_planning_taxonomy_id: Mapped[str | None] = mapped_column(String)

    accounts: Mapped[list["AccountRecordModel"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["TransactionRecordModel"]] = relationship(
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


class AccountRecordModel(Base):
    __tablename__ = "account_record"
    __table_args__ = (
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
    allowed_asset_types_json: Mapped[list[str] | None] = mapped_column(JSON)
    opened_at: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="accounts")


class TransactionRecordModel(Base):
    __tablename__ = "transaction_record"
    __table_args__ = (
        Index(
            "ix_transaction_record_portfolio_trade_sort",
            "portfolio_id",
            "trade_date",
            "trade_at",
            "created_at",
            "transaction_id",
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
        Index("ix_transaction_record_portfolio_asset_trade", "portfolio_id", "asset_id", "trade_date", "trade_at"),
    )

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_time: Mapped[str] = mapped_column(String, nullable=False)
    trade_at: Mapped[str] = mapped_column(String, nullable=False)
    trade_timezone: Mapped[str] = mapped_column(String, nullable=False)
    trade_time_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    entitlement_date: Mapped[date | None] = mapped_column(Date)
    acquisition_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    settlement_cash_account_id: Mapped[str | None] = mapped_column(String)
    asset_id: Mapped[str | None] = mapped_column(String)
    instrument_ref_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    quantity: Mapped[float | None]
    price: Mapped[float | None]
    gross_amount: Mapped[float] = mapped_column(nullable=False, default=0.0)
    counter_amount: Mapped[float | None]
    fx_rate: Mapped[float | None]
    fees: Mapped[float] = mapped_column(nullable=False, default=0.0)
    taxes: Mapped[float] = mapped_column(nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    transfer_scope: Mapped[str | None] = mapped_column(String)
    transfer_object_type: Mapped[str | None] = mapped_column(String)
    transfer_group_id: Mapped[str | None] = mapped_column(String)
    counterparty_account_id: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="transactions")


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
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
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

    assignment_id: Mapped[str] = mapped_column(String, primary_key=True)
    taxonomy_id: Mapped[str] = mapped_column(
        ForeignKey("taxonomy_record.taxonomy_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_scope: Mapped[str] = mapped_column(String, nullable=False)
    target_entity_id: Mapped[str] = mapped_column(String, nullable=False)
    taxonomy_node_id: Mapped[str] = mapped_column(String, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    taxonomy: Mapped[TaxonomyRecordModel] = relationship(back_populates="assignments")


class TargetSetRecordModel(Base):
    __tablename__ = "target_set_record"
    __table_args__ = (
        Index(
            "ix_target_set_record_taxonomy_scope_type_effective",
            "taxonomy_id",
            "comparator_taxonomy_node_id",
            "target_set_type",
            "effective_from",
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
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
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

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        primary_key=True,
    )
    planning_taxonomy_id: Mapped[str | None] = mapped_column(String)
    comparator_taxonomy_node_id: Mapped[str | None] = mapped_column(String)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date | None] = mapped_column(Date)
    lookback_days: Mapped[int] = mapped_column(nullable=False, default=90)
    benchmark_mode: Mapped[str] = mapped_column(String, nullable=False, default="none")
    run_template: Mapped[str] = mapped_column(String, nullable=False, default="taxonomy_backtest")
    target_set_mode: Mapped[str] = mapped_column(String, nullable=False, default="taa_over_saa")
    target_dimension: Mapped[str] = mapped_column(String, nullable=False, default="scope_default")
    capital_mode: Mapped[str] = mapped_column(String, nullable=False, default="unit_notional")
    gross_exposure: Mapped[float | None]
    target_volatility: Mapped[float | None]
    max_gross_exposure: Mapped[float | None]
    frozen_taxonomy_node_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    rebalance_frequency: Mapped[str] = mapped_column(String, nullable=False, default="monthly")
    notes: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="research_settings")


class ResearchRunRecordModel(Base):
    __tablename__ = "research_run_record"
    __table_args__ = (
        Index("ix_research_run_record_portfolio_requested", "portfolio_id", "requested_at", "research_run_id"),
    )

    research_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String, nullable=False, default="taxonomy_backtest")
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    requested_at: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    planning_taxonomy_id: Mapped[str | None] = mapped_column(String)
    lookback_days: Mapped[int] = mapped_column(nullable=False, default=90)
    benchmark_mode: Mapped[str] = mapped_column(String, nullable=False, default="none")
    run_template: Mapped[str] = mapped_column(String, nullable=False, default="taxonomy_backtest")
    requested_by: Mapped[str | None] = mapped_column(String)
    headline: Mapped[str | None] = mapped_column(String)
    detail_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    artifacts_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON)
    request_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(String)

    portfolio: Mapped[PortfolioRecordModel] = relationship(back_populates="research_runs")
