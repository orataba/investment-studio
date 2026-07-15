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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class InstrumentRegistryBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
            "'cash', 'fx', 'other')",
            name="instrument_type_contract",
        ),
        CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="instrument_currency_contract",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_name: Mapped[str] = mapped_column(String, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
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

    identifiers: Mapped[list["InstrumentIdentifier"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    market_data_points: Mapped[list["InstrumentMarketData"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    corporate_action_events: Mapped[list["CorporateActionEvent"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
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


class InstrumentMarketData(InstrumentRegistryBase):
    __tablename__ = "instrument_market_data"
    __table_args__ = (
        CheckConstraint(
            "(metric_family = 'price' AND quote_basis IN "
            "('last', 'close', 'adjusted_close', 'clean_price', 'dirty_price', "
            "'par', 'accrued_interest')) OR "
            "(metric_family = 'nav' AND quote_basis IN "
            "('official_nav', 'total_return_nav', 'cumulative_nav', "
            "'accumulated_nav', 'cum_nav', 'dividend_adjusted_nav', "
            "'reinvested_nav')) OR "
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

    instrument: Mapped[Instrument] = relationship(back_populates="market_data_points")


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
