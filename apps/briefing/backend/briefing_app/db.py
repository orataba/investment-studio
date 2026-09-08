from datetime import datetime, UTC
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from briefing_app.settings import get_settings


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "report"
    __table_args__ = (UniqueConstraint("team_id", "report_type", "report_date", "version", name="uq_briefing_report_version"),)

    report_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    team_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(64))
    created_by_service_id: Mapped[str | None] = mapped_column(String(64))
    report_type: Mapped[str] = mapped_column(String(10), index=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    draft_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


@lru_cache
def get_engine():
    engine = create_engine(get_settings().database_url)
    if engine.dialect.name == "postgresql":
        @event.listens_for(engine, "connect")
        def set_schema(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO briefing, public')
            connection.commit()
    return engine


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session():
    with get_session_factory()() as session:
        yield session
