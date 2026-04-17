from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


def _configure_search_path(engine: Engine, schema: str | None) -> Engine:
    if not schema or engine.dialect.name != "postgresql":
        return engine

    def _apply_search_path(dbapi_connection) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.close()

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        _apply_search_path(dbapi_connection)

    @event.listens_for(engine, "checkout")
    def _reset_search_path(dbapi_connection, connection_record, connection_proxy) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        del connection_proxy
        _apply_search_path(dbapi_connection)

    return engine


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(settings.database_url, echo=settings.sql_echo)
    return _configure_search_path(engine, settings.database_schema)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_db_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
