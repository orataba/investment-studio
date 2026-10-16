from datetime import datetime, UTC
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event, inspect
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
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    edition_role: Mapped[str] = mapped_column(String(16), default="preview")
    reading_json: Mapped[dict] = mapped_column(JSON, default=dict)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    draft_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class ReportSource(Base):
    __tablename__ = "report_source"
    __table_args__ = (Index("ix_briefing_source_identity", "report_id", "source_id"),)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.report_id", ondelete="CASCADE"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32))
    window_scope: Mapped[str] = mapped_column(String(32))
    completeness: Mapped[str] = mapped_column(String(32))
    search_text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    payload_json: Mapped[dict] = mapped_column(JSON)


@event.listens_for(Report, "before_insert")
@event.listens_for(Report, "before_update")
def _prepare_read_projection(_mapper, _connection, report):
    from briefing_app.read_projection import reading_projection, source_projection_rows
    state = inspect(report)
    input_changed = state.pending or state.attrs.input_json.history.has_changes()
    if not input_changed and not state.attrs.result_json.history.has_changes():
        return
    snapshot = report.input_json or {}
    report.source_count = snapshot.get("source_count", 0)
    report.edition_role = snapshot.get("edition_role", "preview")
    report.reading_json = reading_projection(snapshot, report.result_json)
    previous = state.attrs.input_json.history.deleted
    # Usage receipts replace input_json but do not change frozen source rows.
    if input_changed and (state.pending or not previous or (previous[0] or {}).get("sources", []) != snapshot.get("sources", [])):
        report.report_id = report.report_id or uuid4().hex
        report._source_projection_rows = list(source_projection_rows(report.report_id, snapshot))


@event.listens_for(Report, "after_insert")
@event.listens_for(Report, "after_update")
def _save_source_projection(_mapper, connection, report):
    rows = report.__dict__.pop("_source_projection_rows", None)
    if rows is None:
        return
    connection.execute(ReportSource.__table__.delete().where(ReportSource.report_id == report.report_id))
    if rows:
        connection.execute(ReportSource.__table__.insert(), rows)


@lru_cache
def get_engine():
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
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
