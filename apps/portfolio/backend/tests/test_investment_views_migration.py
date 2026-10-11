from alembic import command
import pytest
import sqlalchemy as sa

from portfolio_app.db.models import PortfolioInvestmentViewModel, PortfolioInvestmentViewRevisionModel, PortfolioInvestmentStanceModel
from portfolio_app.db.session import get_engine
from tests.test_migrations import _alembic_config


def test_investment_view_migration_matches_models_and_reverses_empty_tables():
    engine = get_engine()
    config = _alembic_config()
    tables = [PortfolioInvestmentViewModel.__table__, PortfolioInvestmentViewRevisionModel.__table__, PortfolioInvestmentStanceModel.__table__]
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for table in tables:
            assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.columns.keys())
    command.downgrade(config, "20260929_0070")
    assert not set(table.name for table in tables) & set(sa.inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert set(table.name for table in tables) <= set(sa.inspect(engine).get_table_names())


def test_downgrade_requires_backup_when_pm_judgments_exist(raw_client):
    response = raw_client.post("/api/portfolios/investment-studio/investment-views", json={
        "view_date": "2026-10-08", "body": "不可丢弃的人工判断", "context": "当时依据"})
    assert response.status_code == 201
    with pytest.raises(RuntimeError, match="backup"):
        command.downgrade(_alembic_config(), "20260929_0070")
    assert raw_client.get("/api/portfolios/investment-studio/investment-views").json()["notes"][0]["body"] == "不可丢弃的人工判断"
