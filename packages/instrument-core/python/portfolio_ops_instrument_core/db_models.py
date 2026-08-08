from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class InstrumentRegistryBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ExactFundNavDecimal(TypeDecorator[Decimal]):
    """Exact decimal text on SQLite and NUMERIC on PostgreSQL."""

    impl = Numeric(38, 18)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric(38, 18))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect):
        if value is None:
            return None
        parsed = Decimal(str(value))
        if dialect.name != "sqlite":
            return parsed
        rendered = format(parsed, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered in {"-0", ""} else rendered

    def process_result_value(self, value: object, dialect: Dialect):
        if value is None:
            return None
        return Decimal(str(value))


class RegistryMetadata(InstrumentRegistryBase):
    __tablename__ = "registry_metadata"

    registry_key: Mapped[str] = mapped_column(String, primary_key=True, default="shared")
    registry_name: Mapped[str] = mapped_column(String, nullable=False)
    market_data_updated_at: Mapped[str | None] = mapped_column(String)


class Instrument(InstrumentRegistryBase):
    __tablename__ = "instrument"
    __table_args__ = (
        CheckConstraint(
            "instrument_type IN ('fund', 'etf', 'index', 'bond', 'equity', "
            "'fcn', 'option', 'cash', 'fx', 'other')",
            name="instrument_type_contract",
        ),
        CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="instrument_currency_contract",
        ),
        CheckConstraint(
            "("
            "instrument_type <> 'option' AND "
            "option_underlying_instrument_id IS NULL AND option_type IS NULL AND "
            "option_expiry_date IS NULL AND option_strike IS NULL AND "
            "option_contract_multiplier IS NULL AND option_settlement_type IS NULL AND "
            "option_contract_currency IS NULL"
            ") OR ("
            "instrument_type = 'option' AND "
            "option_underlying_instrument_id IS NOT NULL AND option_type IS NOT NULL AND "
            "option_type IN ('call', 'put') AND option_expiry_date IS NOT NULL AND "
            "option_strike IS NOT NULL AND option_strike > 0 AND "
            "option_contract_multiplier IS NOT NULL AND option_contract_multiplier > 0 AND "
            "option_settlement_type IS NOT NULL AND "
            "option_settlement_type IN ('physical', 'cash') AND "
            "option_contract_currency IS NOT NULL AND "
            "option_contract_currency = currency AND "
            "option_contract_currency = upper(trim(option_contract_currency)) AND "
            "length(option_contract_currency) BETWEEN 1 AND 8"
            ")",
            name="option_contract_identity",
        ),
        CheckConstraint(
            "option_underlying_instrument_id IS NULL OR option_underlying_instrument_id <> instrument_id",
            name="option_underlying_distinct",
        ),
        CheckConstraint(
            "(instrument_type = 'fcn' AND fcn_contract_json IS NOT NULL AND "
            "lower(trim(CAST(fcn_contract_json AS TEXT))) NOT IN ('null', '{}')) OR "
            "(instrument_type <> 'fcn' AND fcn_contract_json IS NULL)",
            name="fcn_contract_metadata",
        ),
        CheckConstraint(
            "(instrument_type IN ('fcn', 'option') AND "
            "derivative_adjustment_policy_json IS NOT NULL AND "
            "lower(trim(CAST(derivative_adjustment_policy_json AS TEXT))) "
            "NOT IN ('null', '{}')) OR "
            "(instrument_type NOT IN ('fcn', 'option') AND "
            "derivative_adjustment_policy_json IS NULL)",
            name="derivative_adjustment_policy",
        ),
        Index(
            "ix_instrument_option_underlying",
            "option_underlying_instrument_id",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_name: Mapped[str] = mapped_column(String, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    option_underlying_instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
    )
    option_type: Mapped[str | None] = mapped_column(String)
    option_expiry_date: Mapped[date | None] = mapped_column(Date)
    option_strike: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    option_contract_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    option_settlement_type: Mapped[str | None] = mapped_column(String)
    option_contract_currency: Mapped[str | None] = mapped_column(String)
    fcn_contract_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    derivative_adjustment_policy_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    quote_selection_policy_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )
    source_settings_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    refresh_status_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    lifecycle_state_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    market_data_updated_at: Mapped[str | None] = mapped_column(String)

    option_underlying_instrument: Mapped["Instrument | None"] = relationship(
        remote_side="Instrument.instrument_id",
        foreign_keys=[option_underlying_instrument_id],
        uselist=False,
    )

    identifiers: Mapped[list["InstrumentIdentifier"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    broker_identifiers: Mapped[list["InstrumentBrokerIdentifier"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    market_data_points: Mapped[list["InstrumentMarketData"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    price_bars: Mapped[list["InstrumentPriceBar"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    corporate_action_events: Mapped[list["CorporateActionEvent"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    fund_nav_events: Mapped[list["FundNavEvent"]] = relationship(
        back_populates="instrument",
    )
    fund_nav_reinvestment_evidence: Mapped[list["FundNavReinvestmentEvidence"]] = relationship(
        back_populates="instrument",
    )
    fund_nav_projection_runs: Mapped[list["FundNavProjectionRun"]] = relationship(
        back_populates="instrument",
    )
    current_fund_nav_projection: Mapped["FundNavCurrentProjection | None"] = relationship(
        back_populates="instrument",
        uselist=False,
    )
    fund_nav_adjustment_factors: Mapped[list["FundNavAdjustmentFactor"]] = relationship(
        back_populates="instrument",
    )


class InstrumentIdentifier(InstrumentRegistryBase):
    __tablename__ = "instrument_identifier"
    __table_args__ = (
        UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_instrument_identifier_type_value",
        ),
    )

    instrument_identifier_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier_value: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    instrument: Mapped[Instrument] = relationship(back_populates="identifiers")


class InstrumentBrokerIdentifier(InstrumentRegistryBase):
    __tablename__ = "instrument_broker_identifier"
    __table_args__ = (
        CheckConstraint(
            "identifier_type IN ('contract_id', 'symbol', 'product_code')",
            name="broker_identifier_type",
        ),
        UniqueConstraint(
            "broker",
            "identifier_type",
            "identifier_value",
            name="uq_instrument_broker_identifier_identity",
        ),
        Index(
            "ix_instrument_broker_identifier_instrument",
            "instrument_id",
            "broker",
        ),
    )

    instrument_broker_identifier_id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    broker: Mapped[str] = mapped_column(String, nullable=False)
    identifier_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier_value: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    instrument: Mapped[Instrument] = relationship(back_populates="broker_identifiers")


class InstrumentMarketData(InstrumentRegistryBase):
    __tablename__ = "instrument_market_data"
    __table_args__ = (
        CheckConstraint(
            "(metric_family = 'price' AND quote_basis IN "
            "('last', 'close', 'adjusted_close', 'clean_price', 'dirty_price', "
            "'par', 'accrued_interest')) OR "
            "(metric_family = 'nav' AND quote_basis IN "
            "('official_nav', 'total_return_nav')) OR "
            "(metric_family = 'fx' AND quote_basis = 'spot')",
            name="quote_identity_contract",
        ),
        CheckConstraint(
            "(price_unit = 'per_unit' AND price_scale = 1) OR "
            "(price_unit = 'percent_of_par' AND price_scale = 0.01) OR "
            "(price_unit = 'rate' AND price_scale = 1)",
            name="price_unit_scale_contract",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable')",
            name="market_data_status_contract",
        ),
        CheckConstraint(
            "(metric_family <> 'nav' AND nav_lineage_kind IS NULL "
            "AND nav_derivation_method_version IS NULL "
            "AND nav_derivation_anchor_date IS NULL "
            "AND nav_lineage_evidence_json IS NULL "
            "AND fund_nav_adjustment_factor_id IS NULL) OR "
            "(metric_family = 'nav' AND nav_lineage_evidence_json IS NOT NULL AND "
            "length(trim(CAST(nav_lineage_evidence_json AS TEXT))) > 2 AND "
            "lower(trim(CAST(nav_lineage_evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null') AND "
            "((quote_basis = 'official_nav' "
            "AND nav_lineage_kind = 'provider_explicit' "
            "AND nav_derivation_method_version IS NULL "
            "AND nav_derivation_anchor_date IS NULL "
            "AND fund_nav_adjustment_factor_id IS NULL) OR "
            "(quote_basis = 'total_return_nav' "
            "AND nav_lineage_kind = 'provider_explicit' "
            "AND status = 'complete' "
            "AND nav_derivation_method_version IS NULL "
            "AND nav_derivation_anchor_date IS NULL "
            "AND fund_nav_adjustment_factor_id IS NOT NULL) OR "
            "(quote_basis = 'total_return_nav' "
            "AND nav_lineage_kind = 'derived_dividend_reinvestment' "
            "AND status = 'complete' "
            "AND length(trim(nav_derivation_method_version)) > 0 "
            "AND nav_derivation_anchor_date IS NOT NULL "
            "AND fund_nav_adjustment_factor_id IS NOT NULL)))",
            name="nav_lineage_contract",
        ),
        UniqueConstraint(
            "instrument_id",
            "metric_family",
            "quote_basis",
            "as_of_date",
            "currency",
            name="uq_instrument_market_data_instrument_metric_basis_date_currency",
        ),
    )

    instrument_market_data_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_family: Mapped[str] = mapped_column(String, nullable=False)
    quote_basis: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    price_unit: Mapped[str] = mapped_column(String, nullable=False)
    price_scale: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    provider: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    nav_lineage_kind: Mapped[str | None] = mapped_column(String)
    nav_derivation_method_version: Mapped[str | None] = mapped_column(String)
    nav_derivation_anchor_date: Mapped[date | None] = mapped_column(Date)
    nav_lineage_evidence_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    fund_nav_adjustment_factor_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "fund_nav_adjustment_factor.fund_nav_adjustment_factor_id",
            ondelete="RESTRICT",
        )
    )

    instrument: Mapped[Instrument] = relationship(back_populates="market_data_points")


class InstrumentPriceBar(InstrumentRegistryBase):
    """Canonical raw daily OHLCV evidence for exchange-traded instruments."""

    __tablename__ = "instrument_price_bar"
    __table_args__ = (
        CheckConstraint(
            "CAST(open_price AS NUMERIC) > 0 "
            "AND CAST(high_price AS NUMERIC) > 0 "
            "AND CAST(low_price AS NUMERIC) > 0 "
            "AND CAST(close_price AS NUMERIC) > 0 "
            "AND CAST(high_price AS NUMERIC) >= CAST(open_price AS NUMERIC) "
            "AND CAST(high_price AS NUMERIC) >= CAST(close_price AS NUMERIC) "
            "AND CAST(low_price AS NUMERIC) <= CAST(open_price AS NUMERIC) "
            "AND CAST(low_price AS NUMERIC) <= CAST(close_price AS NUMERIC)",
            name="instrument_price_bar_ohlc_contract",
        ),
        CheckConstraint(
            "previous_close IS NULL OR CAST(previous_close AS NUMERIC) > 0",
            name="instrument_price_bar_previous_close_contract",
        ),
        CheckConstraint(
            "volume IS NULL OR CAST(volume AS NUMERIC) >= 0",
            name="instrument_price_bar_volume_contract",
        ),
        CheckConstraint(
            "turnover IS NULL OR CAST(turnover AS NUMERIC) >= 0",
            name="instrument_price_bar_turnover_contract",
        ),
        CheckConstraint(
            "adjustment_factor IS NULL OR CAST(adjustment_factor AS NUMERIC) > 0",
            name="instrument_price_bar_adjustment_factor_contract",
        ),
        CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="instrument_price_bar_currency_contract",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial')",
            name="instrument_price_bar_status_contract",
        ),
        CheckConstraint(
            "length(trim(provider)) > 0 "
            "AND ((volume IS NULL AND volume_unit IS NULL) "
            "OR (volume IS NOT NULL AND length(trim(volume_unit)) > 0)) "
            "AND ((turnover IS NULL AND turnover_unit IS NULL) "
            "OR (turnover IS NOT NULL AND length(trim(turnover_unit)) > 0))",
            name="instrument_price_bar_source_contract",
        ),
        UniqueConstraint(
            "instrument_id",
            "as_of_date",
            name="uq_instrument_price_bar_instrument_date",
        ),
        Index(
            "ix_instrument_price_bar_instrument_date",
            "instrument_id",
            "as_of_date",
        ),
    )

    instrument_price_bar_id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_price: Mapped[str] = mapped_column(Text, nullable=False)
    high_price: Mapped[str] = mapped_column(Text, nullable=False)
    low_price: Mapped[str] = mapped_column(Text, nullable=False)
    close_price: Mapped[str] = mapped_column(Text, nullable=False)
    previous_close: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(Text)
    turnover: Mapped[str | None] = mapped_column(Text)
    adjustment_factor: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    volume_unit: Mapped[str | None] = mapped_column(String)
    turnover_unit: Mapped[str | None] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="price_bars")


class FundNavEvent(InstrumentRegistryBase):
    """Immutable revision of a stable fund action."""

    __tablename__ = "fund_nav_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('cash_distribution', 'unit_split')",
            name="fund_nav_event_type_contract",
        ),
        CheckConstraint(
            "revision_kind IN ('original', 'correction', 'cancellation')",
            name="fund_nav_event_revision_kind_contract",
        ),
        CheckConstraint(
            "evidence_kind IN ('provider_notice', 'manual_verified')",
            name="fund_nav_event_evidence_contract",
        ),
        CheckConstraint(
            "record_date IS NULL OR record_date <= effective_date",
            name="fund_nav_event_date_contract",
        ),
        CheckConstraint(
            "sequence_order IS NULL OR sequence_order >= 1",
            name="fund_nav_event_sequence_contract",
        ),
        CheckConstraint(
            "(revision_kind = 'original' AND revision_number = 1 "
            "AND supersedes_fund_nav_event_id IS NULL) OR "
            "(revision_kind IN ('correction', 'cancellation') "
            "AND revision_number > 1 "
            "AND supersedes_fund_nav_event_id IS NOT NULL)",
            name="fund_nav_event_revision_contract",
        ),
        CheckConstraint(
            "(event_type = 'cash_distribution' "
            "AND CAST(cash_per_unit AS NUMERIC) > 0 AND unit_ratio IS NULL) OR "
            "(event_type = 'unit_split' AND CAST(unit_ratio AS NUMERIC) > 0 "
            "AND CAST(unit_ratio AS NUMERIC) <> 1 "
            "AND cash_per_unit IS NULL)",
            name="fund_nav_event_payload_contract",
        ),
        CheckConstraint(
            "length(trim(fund_nav_event_id)) > 0 "
            "AND length(trim(fund_nav_action_id)) > 0 "
            "AND length(trim(source)) > 0 "
            "AND (external_event_id IS NULL "
            "OR length(trim(external_event_id)) > 0) "
            "AND length(trim(recorded_by)) > 0 "
            "AND length(trim(revision_reason)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(provenance_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(provenance_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name="fund_nav_event_audit_contract",
        ),
        Index(
            "ix_fund_nav_event_instrument_effective_date",
            "instrument_id",
            "effective_date",
        ),
        UniqueConstraint(
            "fund_nav_action_id",
            "revision_number",
            name="uq_fund_nav_event_action_revision",
        ),
        UniqueConstraint(
            "supersedes_fund_nav_event_id",
            name="uq_fund_nav_event_supersedes",
        ),
    )

    fund_nav_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    fund_nav_action_id: Mapped[str] = mapped_column(String, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    supersedes_fund_nav_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("fund_nav_event.fund_nav_event_id", ondelete="RESTRICT")
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    payable_date: Mapped[date | None] = mapped_column(Date)
    sequence_order: Mapped[int | None] = mapped_column(Integer)
    cash_per_unit: Mapped[Decimal | None] = mapped_column(ExactFundNavDecimal())
    unit_ratio: Mapped[Decimal | None] = mapped_column(ExactFundNavDecimal())
    evidence_kind: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String)
    provenance_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String, nullable=False)
    revision_reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="fund_nav_events")


class FundNavReinvestmentEvidence(InstrumentRegistryBase):
    """Immutable reinvestment-price evidence revision for an action revision."""

    __tablename__ = "fund_nav_reinvestment_evidence"
    __table_args__ = (
        CheckConstraint(
            "revision_kind IN ('original', 'correction', 'cancellation') "
            "AND revision_number >= 1 AND ((revision_kind = 'original' "
            "AND revision_number = 1 "
            "AND supersedes_fund_nav_reinvestment_evidence_id IS NULL) OR "
            "(revision_kind IN ('correction', 'cancellation') "
            "AND revision_number > 1 "
            "AND supersedes_fund_nav_reinvestment_evidence_id IS NOT NULL))",
            name="fund_nav_reinvestment_evidence_revision_contract",
        ),
        CheckConstraint(
            "CAST(reinvestment_nav AS NUMERIC) > 0",
            name="fund_nav_reinvestment_evidence_value_contract",
        ),
        CheckConstraint(
            "evidence_kind IN ('provider_notice', 'manual_verified')",
            name="fund_nav_reinvestment_evidence_kind_contract",
        ),
        CheckConstraint(
            "length(trim(fund_nav_reinvestment_evidence_id)) > 0 "
            "AND length(trim(source)) > 0 "
            "AND (external_evidence_id IS NULL "
            "OR length(trim(external_evidence_id)) > 0) "
            "AND length(trim(recorded_by)) > 0 "
            "AND length(trim(revision_reason)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(provenance_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(provenance_json AS TEXT))) NOT IN ('{}', 'null')",
            name="fund_nav_reinvestment_evidence_audit_contract",
        ),
        UniqueConstraint(
            "fund_nav_event_id",
            "revision_number",
            name="uq_fund_nav_reinvestment_evidence_event_revision",
        ),
        UniqueConstraint(
            "supersedes_fund_nav_reinvestment_evidence_id",
            name="uq_fund_nav_reinvestment_evidence_supersedes",
        ),
        Index(
            "ix_fund_nav_reinvestment_evidence_instrument_event",
            "instrument_id",
            "fund_nav_event_id",
        ),
    )

    fund_nav_reinvestment_evidence_id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fund_nav_event_id: Mapped[str] = mapped_column(
        ForeignKey("fund_nav_event.fund_nav_event_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_kind: Mapped[str] = mapped_column(String, nullable=False)
    supersedes_fund_nav_reinvestment_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "fund_nav_reinvestment_evidence.fund_nav_reinvestment_evidence_id",
            ondelete="RESTRICT",
        )
    )
    reinvestment_nav: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(), nullable=False
    )
    evidence_kind: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_evidence_id: Mapped[str | None] = mapped_column(String)
    provenance_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String, nullable=False)
    revision_reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(
        back_populates="fund_nav_reinvestment_evidence"
    )


class FundNavProjectionRun(InstrumentRegistryBase):
    """Immutable set of projection inputs; old runs remain auditable."""

    __tablename__ = "fund_nav_projection_run"
    __table_args__ = (
        CheckConstraint(
            "fund_nav_projection_run_id = "
            "'fund-nav-projection-' || input_fingerprint "
            "AND length(input_fingerprint) = 64 "
            "AND input_fingerprint = lower(input_fingerprint) "
            "AND length(source_observation_fingerprint) = 64 "
            "AND source_observation_fingerprint = lower(source_observation_fingerprint) "
            "AND projection_kind IN ('provider_explicit', 'event_derived', "
            "'hybrid_reanchored') "
            "AND projection_status IN ('complete', 'partial', 'unavailable') "
            "AND ((projection_status IN ('complete', 'partial') AND anchor_date IS NOT NULL) "
            "OR (projection_status = 'unavailable' AND anchor_date IS NULL "
            "AND projection_kind <> 'hybrid_reanchored')) "
            "AND length(trim(method_version)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND length(trim(created_by)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(CAST(evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(evidence_json AS TEXT))) NOT IN ('{}', 'null')",
            name="fund_nav_projection_run_audit_contract",
        ),
        UniqueConstraint(
            "instrument_id",
            "input_fingerprint",
            name="uq_fund_nav_projection_run_input",
        ),
    )

    fund_nav_projection_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_kind: Mapped[str] = mapped_column(String, nullable=False)
    projection_status: Mapped[str] = mapped_column(String, nullable=False)
    method_version: Mapped[str] = mapped_column(String, nullable=False)
    anchor_date: Mapped[date | None] = mapped_column(Date)
    source_provider: Mapped[str] = mapped_column(String, nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="fund_nav_projection_runs")
    event_revisions: Mapped[list["FundNavProjectionRunEvent"]] = relationship(
        back_populates="projection_run",
    )
    reinvestment_evidence_revisions: Mapped[
        list["FundNavProjectionRunReinvestmentEvidence"]
    ] = relationship(
        back_populates="projection_run",
    )
    adjustment_factors: Mapped[list["FundNavAdjustmentFactor"]] = relationship(
        back_populates="projection_run",
    )


class FundNavProjectionRunEvent(InstrumentRegistryBase):
    __tablename__ = "fund_nav_projection_run_event"

    fund_nav_projection_run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "fund_nav_projection_run.fund_nav_projection_run_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    fund_nav_event_id: Mapped[str] = mapped_column(
        ForeignKey("fund_nav_event.fund_nav_event_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    projection_run: Mapped[FundNavProjectionRun] = relationship(
        back_populates="event_revisions"
    )


class FundNavProjectionRunReinvestmentEvidence(InstrumentRegistryBase):
    __tablename__ = "fund_nav_projection_run_reinvestment_evidence"

    fund_nav_projection_run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "fund_nav_projection_run.fund_nav_projection_run_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    fund_nav_reinvestment_evidence_id: Mapped[str] = mapped_column(
        ForeignKey(
            "fund_nav_reinvestment_evidence.fund_nav_reinvestment_evidence_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )

    projection_run: Mapped[FundNavProjectionRun] = relationship(
        back_populates="reinvestment_evidence_revisions"
    )


class FundNavCurrentProjection(InstrumentRegistryBase):
    """The one mutable pointer in the otherwise append-only projection ledger."""

    __tablename__ = "fund_nav_current_projection"
    __table_args__ = (
        UniqueConstraint(
            "fund_nav_projection_run_id",
            name="uq_fund_nav_current_projection_run",
        ),
        CheckConstraint(
            "length(trim(updated_at)) > 0 AND length(trim(updated_by)) > 0",
            name="fund_nav_current_projection_audit_contract",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    fund_nav_projection_run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "fund_nav_projection_run.fund_nav_projection_run_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(
        back_populates="current_fund_nav_projection"
    )
    projection_run: Mapped[FundNavProjectionRun] = relationship()


class FundNavAdjustmentFactor(InstrumentRegistryBase):
    """Immutable cumulative factor level belonging to one projection run."""

    __tablename__ = "fund_nav_adjustment_factor"
    __table_args__ = (
        CheckConstraint(
            "CAST(factor_level AS NUMERIC) > 0",
            name="fund_nav_adjustment_factor_value_contract",
        ),
        CheckConstraint(
            "factor_kind IN ('provider_implied', 'event_derived')",
            name="fund_nav_adjustment_factor_kind_contract",
        ),
        CheckConstraint(
            "evidence_kind IN ('provider_total_return', 'fund_nav_event', "
            "'provider_cash_cumulative', 'zero_cash_anchor', "
            "'window_normalized_anchor')",
            name="fund_nav_adjustment_factor_evidence_contract",
        ),
        CheckConstraint(
            "length(trim(method_version)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND anchor_date <= as_of_date",
            name="fund_nav_adjustment_factor_lineage_contract",
        ),
        CheckConstraint(
            "(factor_kind = 'provider_implied' AND fund_nav_event_id IS NULL "
            "AND fund_nav_reinvestment_evidence_id IS NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NULL "
            "AND evidence_kind IN "
            "('provider_total_return', 'provider_cash_cumulative') "
            "AND anchor_date = as_of_date) OR "
            "(factor_kind = 'event_derived' AND ("
            "(evidence_kind IN ('zero_cash_anchor', "
            "'window_normalized_anchor') "
            "AND fund_nav_event_id IS NULL "
            "AND CAST(factor_level AS NUMERIC) = 1 "
            "AND fund_nav_reinvestment_evidence_id IS NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NULL "
            "AND anchor_date = as_of_date) OR "
            "(fund_nav_event_id IS NOT NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NOT NULL "
            "AND evidence_kind = 'fund_nav_event')))",
            name="fund_nav_adjustment_factor_source_contract",
        ),
        CheckConstraint(
            "length(trim(fund_nav_adjustment_factor_id)) > 0 "
            "AND length(trim(factor_logical_key)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name="fund_nav_adjustment_factor_audit_contract",
        ),
        Index(
            "ix_fund_nav_adjustment_factor_instrument_date",
            "instrument_id",
            "as_of_date",
        ),
        Index(
            "uq_fund_nav_adjustment_factor_run_anchor",
            "fund_nav_projection_run_id",
            unique=True,
            sqlite_where=text(
                "evidence_kind IN ('zero_cash_anchor', "
                "'window_normalized_anchor')"
            ),
            postgresql_where=text(
                "evidence_kind IN ('zero_cash_anchor', "
                "'window_normalized_anchor')"
            ),
        ),
        Index(
            "uq_fund_nav_adjustment_factor_provider_run_date",
            "fund_nav_projection_run_id",
            "as_of_date",
            unique=True,
            sqlite_where=text("factor_kind = 'provider_implied'"),
            postgresql_where=text("factor_kind = 'provider_implied'"),
        ),
        UniqueConstraint(
            "fund_nav_projection_run_id",
            "factor_logical_key",
            name="uq_fund_nav_adjustment_factor_run_logical_key",
        ),
        UniqueConstraint(
            "fund_nav_projection_run_id",
            "fund_nav_event_id",
            name="uq_fund_nav_adjustment_factor_run_event",
        ),
        UniqueConstraint(
            "fund_nav_projection_run_id",
            "previous_fund_nav_adjustment_factor_id",
            name="uq_fund_nav_adjustment_factor_run_previous",
        ),
    )

    fund_nav_adjustment_factor_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    factor_logical_key: Mapped[str] = mapped_column(String, nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fund_nav_projection_run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "fund_nav_projection_run.fund_nav_projection_run_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_level: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(), nullable=False
    )
    factor_kind: Mapped[str] = mapped_column(String, nullable=False)
    fund_nav_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("fund_nav_event.fund_nav_event_id", ondelete="RESTRICT")
    )
    fund_nav_reinvestment_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "fund_nav_reinvestment_evidence.fund_nav_reinvestment_evidence_id",
            ondelete="RESTRICT",
        )
    )
    previous_fund_nav_adjustment_factor_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "fund_nav_adjustment_factor.fund_nav_adjustment_factor_id",
            ondelete="RESTRICT",
        )
    )
    evidence_kind: Mapped[str] = mapped_column(String, nullable=False)
    method_version: Mapped[str] = mapped_column(String, nullable=False)
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_provider: Mapped[str] = mapped_column(String, nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(
        back_populates="fund_nav_adjustment_factors"
    )
    projection_run: Mapped[FundNavProjectionRun] = relationship(
        back_populates="adjustment_factors"
    )
    fund_nav_event: Mapped[FundNavEvent | None] = relationship()
    reinvestment_evidence: Mapped[FundNavReinvestmentEvidence | None] = relationship()


class CorporateActionEvent(InstrumentRegistryBase):
    """Canonical security-master event that changes the units in circulation.

    Ratios are stored as decimal text so the ledger can apply the exact
    provider/issuer ratio without a binary floating-point round trip.  A
    ``share_split`` uses ``new_units / old_units`` (for example 2 / 1 for a
    two-for-one split and 1 / 2 for a reverse split).
    """

    __tablename__ = "corporate_action_event"
    __table_args__ = (
        Index(
            "ix_corporate_action_instrument_effective_date",
            "instrument_id",
            "effective_date",
        ),
        UniqueConstraint(
            "instrument_id",
            "action_type",
            "effective_date",
            name="uq_corporate_action_instrument_type_effective_date",
        ),
    )

    corporate_action_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    payable_date: Mapped[date | None] = mapped_column(Date)
    new_units: Mapped[str] = mapped_column(Text, nullable=False)
    old_units: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_rounding: Mapped[str] = mapped_column(String, nullable=False, default="exact")
    quantity_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_basis_treatment: Mapped[str] = mapped_column(String, nullable=False, default="carry")
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    provenance_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="corporate_action_events")
