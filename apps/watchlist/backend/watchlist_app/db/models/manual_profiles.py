from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class InstrumentManualProfile(Base):
    __tablename__ = "instrument_manual_profile"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    people_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    strategy_payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    price_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    documents_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    nav_settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str | None]
