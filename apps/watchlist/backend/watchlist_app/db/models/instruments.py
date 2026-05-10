from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base
from watchlist_app.db.models.common import TimestampMixin


class InstrumentDetail(TimestampMixin, Base):
    __tablename__ = "instrument_detail"

    instrument_id: Mapped[str] = mapped_column(primary_key=True)
    instrument_type: Mapped[str] = mapped_column(nullable=False)
    detail_view_type: Mapped[str] = mapped_column(nullable=False)
    instrument_name: Mapped[str] = mapped_column(nullable=False)
    primary_identifier_type: Mapped[str | None]
    primary_identifier_value: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
