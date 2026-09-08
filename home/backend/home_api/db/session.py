from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from home_api.core.settings import get_settings


@lru_cache
def database_engine(database_url: str):
    if not database_url:
        raise ValueError("Identity database is not configured; run the explicit identity bootstrap first.")
    options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options.update(connect_args={"check_same_thread": False}, execution_options={"schema_translate_map": {"identity": None}})
    return create_engine(database_url, **options)


def initialize_schema(database_url: str) -> None:
    """Apply versioned identity migrations. Never called by application startup."""
    engine = database_engine(database_url)
    with engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def get_db():
    try:
        engine = database_engine(get_settings().database_url)
        connection = engine.connect()
    except (ValueError, SQLAlchemyError) as error:
        raise HTTPException(503, "账号数据库尚未配置或暂时不可用。") from error
    with connection, Session(bind=connection) as session:
        yield session
