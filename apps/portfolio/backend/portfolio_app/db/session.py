from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from portfolio_app.core.settings import get_settings


def _search_path_fragments(schema: str | None) -> list[str]:
    fragments: list[str] = []
    for candidate in [schema, "shared_asset", "public"]:
        if not candidate:
            continue
        normalized = candidate.strip()
        if normalized and normalized not in fragments:
            fragments.append(normalized)
    return fragments


def _configure_search_path(engine: Engine, schema: str | None) -> Engine:
    if not schema or engine.dialect.name != "postgresql":
        return engine

    search_path = ", ".join(f'"{fragment}"' if fragment != "public" else "public" for fragment in _search_path_fragments(schema))

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {search_path}")
        finally:
            cursor.close()
        dbapi_connection.commit()

    @event.listens_for(engine, "checkout")
    def _reset_search_path(dbapi_connection, connection_record, connection_proxy) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        del connection_proxy
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {search_path}")
        finally:
            cursor.close()
        dbapi_connection.commit()

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
