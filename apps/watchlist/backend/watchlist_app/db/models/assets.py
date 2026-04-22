from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base
from watchlist_app.db.models.common import TimestampMixin


class AssetDetail(TimestampMixin, Base):
    __tablename__ = "asset_detail"

    asset_id: Mapped[str] = mapped_column(primary_key=True)
    asset_type: Mapped[str] = mapped_column(nullable=False)
    detail_view_type: Mapped[str] = mapped_column(nullable=False)
    asset_name: Mapped[str] = mapped_column(nullable=False)
    primary_identifier_type: Mapped[str | None]
    primary_identifier_value: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
