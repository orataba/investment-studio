from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from briefing_app.db import Report
from briefing_app.settings import get_settings


@pytest.mark.parametrize("database_kind", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgresql_integration)])
def test_team_version_migration_preserves_existing_report_and_enforces_team_scope(database_kind, tmp_path, monkeypatch):
    admin = None
    name = "studio_briefing_" + uuid4().hex[:10]
    url = f"sqlite:///{tmp_path / 'reports.db'}"
    if database_kind == "postgresql":
        base = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
        if not base:
            pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured")
        admin = create_engine(make_url(base).set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        url = make_url(base).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_DATABASE_URL", url)
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_ALEMBIC_DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    if database_kind == "postgresql":
        @event.listens_for(engine, "connect")
        def schema(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO briefing, public")
            connection.commit()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    def report(team):
        return Report(team_id=team, report_type="daily", report_date="2026-09-07", version=1,
                      status="completed", cutoff=datetime(2026, 9, 7, tzinfo=timezone.utc),
                      input_json={"edition_role": "publisher"}, result_json={"retained": True})
    try:
        command.upgrade(config, "20260908_0002")
        with Session(engine) as session:
            session.execute(text("INSERT INTO report (report_id, team_id, report_type, report_date, version, status, cutoff, created_at, input_json, result_json) VALUES ('old', 'default', 'daily', '2026-09-07', 1, 'completed', '2026-09-07', '2026-09-07', :snapshot, :result)"),
                            {"snapshot": '{"edition_role":"publisher","sources":[{"source_id":"old-source","source_type":"public_document","title":"Historical source","version_id":"v1"}]}', "result": '{"retained":true,"source_ids":["old-source"]}'})
            for version, state in [(2, "failed"), (3, "running")]:
                session.execute(text("INSERT INTO report (report_id, team_id, report_type, report_date, version, status, cutoff, created_at, input_json) VALUES (:id, 'default', 'daily', '2026-09-07', :version, :state, '2026-09-07', '2026-09-07', :snapshot)"),
                                {"id": state, "version": version, "state": state, "snapshot": '{"edition_role":"preview","sources":[{"source_id":"frozen","source_type":"public_document","version_id":"original"}]}'})
            session.commit()
        command.upgrade(config, "head")
        with Session(engine) as session:
            saved = session.get(Report, "old")
            assert saved.result_json == {"retained": True, "source_ids": ["old-source"]}
            assert saved.edition_role == "publisher"
            assert saved.reading_json["sources"][0]["source_id"] == "old-source"
            for state in ["failed", "running"]:
                historical = session.get(Report, state)
                assert historical.status == state and historical.result_json is None
                assert historical.edition_role == "preview"
                assert historical.input_json["sources"][0]["version_id"] == "original"
                assert session.execute(text("SELECT source_id FROM report_source WHERE report_id=:id"), {"id": state}).scalar() == "frozen"
            session.add(report("second-team"))
            session.commit()
            session.add(report("second-team"))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        engine.dispose()
        get_settings.cache_clear()
        if admin:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            admin.dispose()
