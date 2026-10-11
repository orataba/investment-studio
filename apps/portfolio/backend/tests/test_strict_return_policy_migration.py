"""Current settings become strict without rewriting saved research facts."""
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa

from portfolio_app.db.models import PortfolioRecordModel, ResearchSettingsRecordModel, ResearchRunRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from tests.test_migrations import _alembic_config


def exercise_migration(config):
    command.upgrade(config, "20261008_0071")
    with get_session_factory()() as session:
        portfolio = PortfolioRecordModel(
            portfolio_id="strict-migration", portfolio_name="Policy migration", base_currency="USD",
            valuation_timezone="UTC", valuation_cutoff_policy="eod", inception_date=date(2026, 1, 1),
            risk_policy_json={"missing_return_policy": "complete_case_drop", "lookback_days": 180, "contribution_mode": "signed"})
        session.add(portfolio)
        session.flush()
        session.add(ResearchSettingsRecordModel(portfolio_id=portfolio.portfolio_id,
                                               missing_return_policy="complete_case_drop"))
        session.add(ResearchRunRecordModel(research_run_id="historical-drop", portfolio_id=portfolio.portfolio_id,
                    request_payload_json={"missing_return_policy": "complete_case_drop", "solver_version": "prior"},
                    detail_json={"missing_return_policy": "complete_case_drop", "dropped_return_rows": [{"date": "2026-01-05"}]}))
        session.commit()
    command.upgrade(config, "head")
    with get_session_factory()() as session:
        assert session.get(PortfolioRecordModel, "strict-migration").risk_policy_json == {
            "missing_return_policy": "strict", "lookback_days": 180, "contribution_mode": "signed"}
        assert session.get(ResearchSettingsRecordModel, "strict-migration").missing_return_policy == "strict"
        historical = session.get(ResearchRunRecordModel, "historical-drop")
        assert historical.request_payload_json == {"missing_return_policy": "complete_case_drop", "solver_version": "prior"}
        assert historical.detail_json == {"missing_return_policy": "complete_case_drop", "dropped_return_rows": [{"date": "2026-01-05"}]}
    for model, field, value in [(PortfolioRecordModel, "risk_policy_json", {"missing_return_policy": "complete_case_drop"}),
                                (ResearchSettingsRecordModel, "missing_return_policy", "complete_case_drop")]:
        with pytest.raises(sa.exc.IntegrityError), get_session_factory()() as session:
            setattr(session.get(model, "strict-migration"), field, value)
            session.commit()
    command.downgrade(config, "20261008_0071")
    command.upgrade(config, "head")
    with get_session_factory()() as session:
        assert session.get(PortfolioRecordModel, "strict-migration").risk_policy_json["missing_return_policy"] == "strict"


@pytest.mark.migration_base_revision("20261008_0071")
def test_strict_return_policy_migration_preserves_history_and_other_choices():
    exercise_migration(_alembic_config())
