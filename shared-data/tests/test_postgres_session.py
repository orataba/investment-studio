from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from studio_data.db.session import _configure_search_path


@pytest.mark.postgresql_integration
def test_search_path_setup_leaves_checked_out_connections_idle() -> None:
    database_url = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")

    engine = _configure_search_path(
        create_engine(database_url),
        "data_ingestion",
        "instrument_data",
    )
    try:
        for _ in range(2):
            connection = engine.raw_connection()
            try:
                transaction_status = (
                    connection.driver_connection.info.transaction_status
                )
                assert transaction_status.name == "IDLE"
            finally:
                connection.close()
    finally:
        engine.dispose()
