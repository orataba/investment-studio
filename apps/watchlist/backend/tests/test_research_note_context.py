from copy import deepcopy
from datetime import date
import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from pydantic import ValidationError
import sqlalchemy as sa

from watchlist_app.api.contracts import InstrumentResearchNoteInput, InstrumentResearchNoteUpsertRequest
from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260908_0055_research_note_context.py"
    spec = importlib.util.spec_from_file_location("research_note_context_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("location", ["note", "context", "request"])
def test_note_input_cannot_supply_owner_or_internal_attribution(location):
    note = {"note_date": "2026-09-08", "title": "PM判断"}
    request = {"note": note}
    if location == "note":
        note["author_user_id"] = "another-pm"
    elif location == "request":
        request["author_user_id"] = "another-pm"
    else:
        note["research_context"] = {"source_run_id": "invented-run", "author_role": "pm"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        InstrumentResearchNoteUpsertRequest.model_validate(request)


def test_note_context_and_owner_survive_updates_and_immutable_revisions(client):
    from watchlist_app.db.session import get_session_factory

    wid = client.post("/api/watchlists", json={"name": "PM context"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["sxv264"]}).status_code == 200
    original_context = {
        "theme_id": "credit-confidence",
        "relationship": "initial",
        "background": "当时长债收益率抬升",
        "horizon": "中期",
        "verification": "美元与实际利率的变化",
        "source_ids": ["original-evidence"],
        "source_run_id": "original-conversation",
        "recorded_via": "assistant",
        "author_role": "pm",
    }
    repo = SQLAlchemyInstrumentResearchRepository()
    values = InstrumentResearchNoteInput(note_date=date(2026, 9, 8), title="信用担忧可能支持黄金").model_dump()
    values["research_context"] = deepcopy(original_context)
    values["author_user_id"] = "forged-owner"
    with get_session_factory()() as session:
        record = repo.create_note(
            session, instrument_id="sxv264", note_id="pm-original", values=values,
            updated_by="pm-one", author_user_id="pm-one",
        )
        session.commit()
        assert record.author_user_id == "pm-one"
        values["research_context"]["source_ids"].append("later-evidence")
        assert record.research_context == original_context
        # An ordinary editor omits structured data; none must not erase it either.
        update = InstrumentResearchNoteInput(note_date=date(2026, 9, 8), title="补充期限后的判断").model_dump()
        update["author_user_id"] = "another-pm"
        repo.update_note(session, record=record, values=update, updated_by="pm-one")
        session.commit()
        assert record.author_user_id == "pm-one"
        assert record.research_context == original_context
        update.pop("research_context")
        update["title"] = "进一步澄清"
        repo.update_note(session, record=record, values=update, updated_by="pm-one")
        session.commit()
        assert record.research_context == original_context
        revised_context = deepcopy(original_context)
        revised_context["outcome"] = "方向相符，原因仍需核实"
        update["research_context"] = revised_context
        repo.update_note(session, record=record, values=update, updated_by="pm-one")
        session.commit()
        revisions = {r.revision_number: r for r in repo.list_note_revisions(session, "sxv264")}
        assert len(revisions) == 4
        assert revisions[1].research_context == original_context
        assert revisions[3].research_context == original_context
        assert revisions[4].research_context == revised_context
        assert {r.author_user_id for r in revisions.values()} == {"pm-one"}
        repo.delete_note(session, record, deleted_by="pm-one")
        session.commit()
        deleted = repo.list_note_revisions(session, "sxv264")[0]
        assert deleted.change_type == "delete"
        assert deleted.author_user_id == "pm-one" and deleted.research_context == revised_context


def test_note_context_migration_preserves_legacy_rows_and_reverses_when_unused():
    migration = _migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        for table in migration.TABLES:
            connection.exec_driver_sql(f"CREATE TABLE {table} (note_id TEXT PRIMARY KEY, author TEXT NOT NULL)")
            connection.execute(sa.text(f"INSERT INTO {table} VALUES ('legacy', 'Old PM')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        for table in migration.TABLES:
            row = connection.execute(sa.text(f"SELECT * FROM {table}")).mappings().one()
            assert row["author"] == "Old PM" and row["owner_user_id"] is None
            assert row["research_context"] == "{}"
        migration.downgrade()
        for table in migration.TABLES:
            assert {c["name"] for c in sa.inspect(connection).get_columns(table)} == {"note_id", "author"}
            assert connection.execute(sa.text(f"SELECT author FROM {table}")).scalar_one() == "Old PM"


@pytest.mark.parametrize("table", ["instrument_research_note", "instrument_research_note_revision"])
@pytest.mark.parametrize("recorded", [{"owner_user_id": "pm-one"}, {"research_context": {"outcome": "尚未证实机制"}}])
def test_note_context_migration_refuses_to_erase_recorded_identity_or_research(table, recorded):
    migration = _migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        for table_name in migration.TABLES:
            connection.exec_driver_sql(f"CREATE TABLE {table_name} (note_id TEXT PRIMARY KEY)")
            connection.execute(sa.text(f"INSERT INTO {table_name} VALUES ('one')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        notes = sa.Table(table, sa.MetaData(), autoload_with=connection)
        connection.execute(notes.update().values(**recorded))
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            migration.downgrade()
        for table_name in migration.TABLES:
            assert {"owner_user_id", "research_context"} <= {c["name"] for c in sa.inspect(connection).get_columns(table_name)}


def test_note_context_migration_upgrade_compiles_for_postgresql():
    migration = _migration()
    sql = StringIO()
    migration.op = Operations(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": sql}))
    migration.upgrade()
    output = sql.getvalue()
    assert output.count("ADD COLUMN owner_user_id TEXT") == 2
    assert output.count("ADD COLUMN research_context JSON DEFAULT '{}' NOT NULL") == 2
