from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
import pytest

from home_api.db.models import User
from home_api.db.session import database_engine, initialize_schema


def test_adopts_unversioned_identity_without_replacing_accounts(tmp_path):
    url = f"sqlite:///{tmp_path / 'accounts.db'}"
    engine = database_engine(url)
    baseline = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20260908_0001_identity.py"))
    baseline["baseline_metadata"](None).create_all(engine)
    with Session(engine) as session:
        session.add(User(id='existing', username='existing', display_name='Existing owner'))
        session.commit()
    initialize_schema(url)
    initialize_schema(url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == '20260919_0002'
        assert connection.scalar(select(User.display_name).where(User.id == 'existing')) == 'Existing owner'
        columns = {column['name']: column for column in inspect(connection).get_columns('users')}
        assert columns['username']['nullable']
        assert not {'totp_secret', 'totp_pending_secret', 'totp_last_step'} & columns.keys()


def test_rejects_incompatible_existing_identity_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'accounts.db'}"
    engine = database_engine(url)
    baseline = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20260908_0001_identity.py"))
    baseline["baseline_metadata"](None).create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE users DROP COLUMN display_name'))
    with pytest.raises(RuntimeError, match='differs from its migration baseline'):
        initialize_schema(url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() is None
