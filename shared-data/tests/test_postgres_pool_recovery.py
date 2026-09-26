"""Cross-app runtime pool contract; only terminate connections owned by this test."""
from importlib import import_module
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool


pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("runtime", ["home", "watchlist", "portfolio", "briefing", "data", "numeric", "text"])
def test_runtime_pool_replaces_dead_idle_connection_without_replaying_transaction(runtime, monkeypatch, tmp_path):
    url = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    parsed = make_url(url)
    assert parsed.database and "test" in parsed.database.lower(), "Use an explicit test database."
    admin = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == parsed.database

    factory = None
    if runtime in {"numeric", "text"}:
        from studio_market.config import MarketSettings
        settings = MarketSettings(database_url=url, data_root=tmp_path)
        module = import_module(f"studio_market.{runtime}.store")
        store = getattr(module, "NumericStore" if runtime == "numeric" else "TextStore")(settings)
        engine = store.engine
    else:
        module = import_module({
            "home": "home_api.db.session", "watchlist": "watchlist_app.db.session",
            "portfolio": "portfolio_app.db.session", "briefing": "briefing_app.db",
            "data": "studio_data.db.session",
        }[runtime])
        factory = module.database_engine if runtime == "home" else module.get_engine
        factory.cache_clear()
        monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
            database_url=url, sql_echo=False, database_schema="instrument_data" if runtime == "data" else runtime,
            operations_database_schema="data_ingestion",
        ))
        engine = factory(url) if runtime == "home" else factory()

    def terminate_own_connection(pid):
        with admin.connect() as connection:
            # The saved PID came from this runtime engine's own live connection,
            # never from enumerating or selecting another client's session.
            assert connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    try:
        with engine.connect() as connection:
            original_pid = connection.scalar(text("SELECT pg_backend_pid()"))
            original_path = connection.scalar(text("SHOW search_path"))
        terminate_own_connection(original_pid)
        # Before pool_pre_ping this checkout fails in SET search_path or the
        # first statement. No application-level retry is involved here.
        with engine.connect() as connection:
            assert connection.connection.driver_connection.info.transaction_status.name == "IDLE"
            assert connection.scalar(text("SELECT pg_backend_pid()")) != original_pid
            assert connection.scalar(text("SHOW search_path")) == original_path
            assert connection.scalar(text("SELECT 42")) == 42

        # A transaction that loses its connection still fails. Only a later
        # checkout recovers; writes/queries inside that transaction are not replayed.
        with engine.connect() as connection:
            active_pid = connection.scalar(text("SELECT pg_backend_pid()"))
            terminate_own_connection(active_pid)
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT 43"))
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 44")) == 44
            assert connection.scalar(text("SHOW search_path")) == original_path
    finally:
        engine.dispose()
        admin.dispose()
        if factory is not None:
            factory.cache_clear()
