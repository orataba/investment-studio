from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from portfolio_ops_instrument_core.listing_contract import SUPPORTED_LISTING_EXCHANGES

from platform_app.db.base import Base


class FmpEquityCatalog(Base):
    __tablename__ = "fmp_equity_catalog"
    __table_args__ = (
        CheckConstraint(
            "exchange_code IN ("
            + ", ".join(
                f"'{value}'"
                for value in SUPPORTED_LISTING_EXCHANGES
                if value != "BATS"
            )
            + ")",
            name="exchange_code_contract",
        ),
        CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="currency_contract",
        ),
        Index("ix_fmp_equity_catalog_company_name", "company_name"),
        Index("ix_fmp_equity_catalog_exchange", "exchange_code"),
    )

    fmp_symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange_ticker: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(512), nullable=False)
    exchange_code: Mapped[str] = mapped_column(String(4), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    country: Mapped[str | None] = mapped_column(String(8))
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(256))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
