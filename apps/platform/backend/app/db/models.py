from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RegistryMetadata(Base):
    __tablename__ = "registry_metadata"

    registry_key: Mapped[str] = mapped_column(String, primary_key=True, default="shared")
    registry_name: Mapped[str] = mapped_column(String, nullable=False)


class Instrument(Base):
    __tablename__ = "instrument"

    asset_id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    quote_selection_policy_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
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

    identifiers: Mapped[list["InstrumentIdentifier"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    market_data_points: Mapped[list["InstrumentMarketData"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )


class InstrumentIdentifier(Base):
    __tablename__ = "instrument_identifier"
    __table_args__ = (
        UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_instrument_identifier_type_value",
        ),
    )

    instrument_identifier_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier_value: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    instrument: Mapped[Instrument] = relationship(back_populates="identifiers")


class InstrumentMarketData(Base):
    __tablename__ = "instrument_market_data"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "metric_family",
            "quote_basis",
            "as_of_date",
            "currency",
            name="uq_instrument_market_data_asset_metric_basis_date_currency",
        ),
    )

    instrument_market_data_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_family: Mapped[str] = mapped_column(String, nullable=False)
    quote_basis: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="complete")

    instrument: Mapped[Instrument] = relationship(back_populates="market_data_points")
