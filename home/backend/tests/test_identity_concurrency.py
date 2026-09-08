"""Account security changes must serialize against login, reset and handover."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, local
from uuid import uuid4

from fastapi import HTTPException, Request, Response
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from home_api.api.routes import auth
from home_api import cli
from home_api.core.settings import Settings
from home_api.db.models import Membership, OneTimeToken, SessionRecord, Team, User
from home_api.db.session import database_engine, initialize_schema
from home_api.services import identity
from home_api.services.auth import hash_password, verify_password


pytestmark = pytest.mark.postgresql_integration
PASSWORD = "test-password-long"
CHANGED_PASSWORD = "changed-password-long"
RESET_PASSWORD = "reset-password-long"


@pytest.fixture
def postgres_identity(monkeypatch):
    target = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not target:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    name = f"studio_identity_concurrency_{uuid4().hex[:8]}"
    target_url = make_url(target)
    admin = create_engine(target_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = target_url.set(database=name).render_as_string(hide_password=False)
    engine = database_engine(url)
    try:
        initialize_schema(url)
        settings = Settings(database_url=url, frontend_url="https://testserver", cors_origins=["https://testserver"])
        for module in (auth, cli, identity):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        with Session(engine) as db:
            encoded = hash_password(PASSWORD)
            owner_id = cli.bootstrap(db, username="owner", display_name="Owner", team_name="Team", password_hash=encoded)["user_id"]
            admin_user = User(username="admin", display_name="Admin", password_hash=encoded)
            member = User(username="member", display_name="Member", password_hash=encoded)
            db.add_all([admin_user, member])
            db.flush()
            db.add_all([
                Membership(team_id="default", user_id=admin_user.id, role="admin"),
                Membership(team_id="default", user_id=member.id, role="member"),
            ])
            reset = identity.one_time_token(db, owner_id, "reset")
            owner_session = identity.new_session(db, owner_id, 3600)
            admin_session = identity.new_session(db, admin_user.id, 3600)
            db.commit()
            yield_data = {
                "engine": engine, "owner_id": owner_id, "member_id": member.id, "admin_id": admin_user.id,
                "reset": reset, "owner_session": owner_session, "admin_session": admin_session,
            }
        yield yield_data
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def request(token=None):
    headers = [(b"origin", b"https://testserver")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def call_route(engine, route):
    with Session(engine) as db:
        try:
            result = route(db)
            return result.status_code if isinstance(result, Response) else 200
        except HTTPException as error:
            return error.status_code


def interleave(engine, first, second, *, held_table="users", waiting_table="users"):
    """Run the second operation while the first owns its real PostgreSQL row lock.

    SQL events coordinate only the overlap. Assertions below check the completed
    operations and stored security state, not which locking helpers they use.
    """
    state = local()
    held = Event()
    waiting = Event()

    def locks(statement, table):
        return "FOR UPDATE" in statement and f"FROM identity.{table} " in statement

    def before_execute(connection, cursor, statement, parameters, context, many):
        if getattr(state, "operation", None) == "second" and locks(statement, waiting_table):
            waiting.set()

    def after_execute(connection, cursor, statement, parameters, context, many):
        if getattr(state, "operation", None) == "first" and locks(statement, held_table) and not held.is_set():
            held.set()
            assert waiting.wait(10), "The competing account operation did not reach its lock."

    def run(label, operation):
        state.operation = label
        return operation()

    event.listen(engine, "before_cursor_execute", before_execute)
    event.listen(engine, "after_cursor_execute", after_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_result = executor.submit(run, "first", first)
            assert held.wait(10), "The first account operation did not acquire its lock."
            second_result = executor.submit(run, "second", second)
            return first_result.result(timeout=20), second_result.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)
        event.remove(engine, "after_cursor_execute", after_execute)


@pytest.mark.parametrize("first_operation", ["password", "activate"])
def test_password_change_and_reset_link_have_one_winner(postgres_identity, first_operation):
    data = postgres_identity
    engine = data["engine"]
    operations = {
        "password": lambda: call_route(engine, lambda db: auth.password(
            auth.PasswordRequest(current_password=PASSWORD, password=CHANGED_PASSWORD),
            request(data["owner_session"]), Response(), db,
        )),
        "activate": lambda: call_route(engine, lambda db: auth.activate(
            auth.ActivateRequest(token=data["reset"], password=RESET_PASSWORD), request(), db,
        )),
    }
    second_operation = "activate" if first_operation == "password" else "password"
    outcomes = interleave(engine, operations[first_operation], operations[second_operation])
    assert outcomes == (204, 400 if first_operation == "password" else 401)
    with Session(engine) as db:
        expected_password = CHANGED_PASSWORD if first_operation == "password" else RESET_PASSWORD
        assert verify_password(expected_password, db.get(User, data["owner_id"]).password_hash)
        assert all(row.consumed_at for row in db.scalars(select(OneTimeToken)))
        assert not db.scalar(select(SessionRecord).where(
            SessionRecord.user_id == data["owner_id"], SessionRecord.revoked_at.is_(None),
        ))


def test_admin_reset_revokes_session_from_overlapping_login(postgres_identity):
    data = postgres_identity
    engine = data["engine"]
    login = lambda: call_route(engine, lambda db: auth.login(
        auth.LoginRequest(username="member", password=PASSWORD), request(), Response(), db,
    ))
    reset = lambda: call_route(engine, lambda db: auth.reset_member(
        data["member_id"], request(data["admin_session"]), db,
    ))
    assert interleave(engine, login, reset) == (200, 200)
    with Session(engine) as db:
        sessions = db.scalars(select(SessionRecord).where(SessionRecord.user_id == data["member_id"])).all()
        assert len(sessions) == 1
        assert sessions[0].revoked_at is not None
        assert db.scalar(select(OneTimeToken).where(
            OneTimeToken.user_id == data["member_id"], OneTimeToken.consumed_at.is_(None),
        )) is not None


def test_owner_handover_and_member_reset_use_the_same_team_lock_order(postgres_identity):
    data = postgres_identity
    engine = data["engine"]
    reset = lambda: call_route(engine, lambda db: auth.reset_member(
        data["owner_id"], request(data["admin_session"]), db,
    ))
    transfer = lambda: call_route(engine, lambda db: auth.transfer(
        auth.TransferRequest(user_id=data["member_id"], password=PASSWORD), request(data["owner_session"]), db,
    ))
    assert interleave(engine, reset, transfer, held_table="teams", waiting_table="teams") == (403, 200)
    with Session(engine) as db:
        assert db.get(Team, "default").owner_user_id == data["member_id"]
        assert db.get(Membership, ("default", data["member_id"])).role == "admin"


def test_concurrent_owner_handovers_cannot_overwrite_the_first_transfer(postgres_identity):
    data = postgres_identity
    engine = data["engine"]
    def transfer(target):
        return call_route(engine, lambda db: auth.transfer(
            auth.TransferRequest(user_id=target, password=PASSWORD), request(data["owner_session"]), db,
        ))
    assert interleave(engine, lambda: transfer(data["member_id"]), lambda: transfer(data["admin_id"]),
                      held_table="teams", waiting_table="teams") == (200, 409)
    with Session(engine) as db:
        assert db.get(Team, "default").owner_user_id == data["member_id"]


def test_operator_recovery_invalidates_an_overlapping_reset_link(postgres_identity, monkeypatch):
    data = postgres_identity
    engine = data["engine"]
    monkeypatch.setattr("sys.argv", ["studio-identity", "recover-user", "--user-id", data["owner_id"], "--reset-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: CHANGED_PASSWORD)
    activate = lambda: call_route(engine, lambda db: auth.activate(
        auth.ActivateRequest(token=data["reset"], password=RESET_PASSWORD), request(), db,
    ))
    assert interleave(engine, cli.main, activate) == (None, 400)
    with Session(engine) as db:
        assert verify_password(CHANGED_PASSWORD, db.get(User, data["owner_id"]).password_hash)
        assert all(row.consumed_at for row in db.scalars(select(OneTimeToken)))
