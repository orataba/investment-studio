from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, select
from studio_identity import Principal, principal_context

from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
from portfolio_app.db.models import ConcentrationPolicyRevisionModel, PortfolioRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services.concentration_settings import save_concentration_settings
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env

pytestmark = pytest.mark.postgresql_integration


def test_concurrent_first_policy_save_has_one_winner_without_lost_edits(postgres_portfolio_env):
    checks = inspect(get_engine()).get_check_constraints("concentration_policy_revision", schema="portfolio")
    assert {item["name"] for item in checks} == {"ck_concentration_policy_revision_positive_revision"}
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id="limits", portfolio_name="Limits", base_currency="USD", valuation_timezone="UTC", valuation_cutoff_policy="close", inception_date=date(2026, 1, 1)))
        session.commit()
    barrier = Barrier(2)
    def save(limit):
        request = ConcentrationSettingsUpdate(expected_revision=0, effective_from=date(2026, 9, 8), rules=[{"rule_id": "s", "scope": "security", "limit_weight": limit}])
        with principal_context(Principal("pm", "PM", "default")):
            barrier.wait(timeout=10)
            try:
                return save_concentration_settings("limits", request)["revision"]
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [0.1, 0.2])) == [1, 409]
    with get_session_factory()() as session:
        revisions = list(session.scalars(select(ConcentrationPolicyRevisionModel)))
        assert len(revisions) == 1
        assert revisions[0].settings_json["rules"][0]["limit_weight"] in {0.1, 0.2}
