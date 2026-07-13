from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PayloadReadModelMixin:
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_freshness_status: Mapped[str] = mapped_column(nullable=False)
    last_recalculated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    market_data_input_watermark_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
