"""Human-owned team research methods; never an evidence source."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from watchlist_app.db.base import Base


class ResearchGuidance(Base):
    __tablename__ = "research_guidance"
    team_id: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    section_skills: Mapped[dict] = mapped_column(JSON, nullable=False)
    author: Mapped[dict] = mapped_column(JSON, nullable=False)
    versions: Mapped[list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
