"""Portfolio manager handover must retain an active manager under real row locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from studio_identity import Principal, principal_context

from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


def test_concurrent_self_demotion_preserves_last_manager(postgres_portfolio_env, monkeypatch):
    from portfolio_app.api.routes import portfolio_members
    from portfolio_app.db.models import PortfolioMembershipModel
    from portfolio_app.db.session import get_session_factory
    from portfolio_app.services.portfolio_access import initialize_access
    from portfolio_app.services.portfolio_store import create_portfolio

    principals = [Principal(name, name, "team") for name in ("alice", "bob")]
    portfolio = create_portfolio("Concurrent manager handover", base_currency="USD", inception_date=date(2026, 1, 1))
    portfolio_id = portfolio["portfolio_id"]
    with get_session_factory()() as session:
        initialize_access(session, portfolio_id, principals[0])
        session.add(PortfolioMembershipModel(
            portfolio_id=portfolio_id, user_id="bob", display_name="bob", role="manager",
            granted_by="alice", granted_at="2026-09-08",
        ))
        session.commit()
    monkeypatch.setattr(portfolio_members, "directory", lambda: [
        {"user_id": principal.user_id, "display_name": principal.display_name}
        for principal in principals
    ])
    ready = Barrier(2)

    def demote(principal):
        with principal_context(principal):
            ready.wait(timeout=5)
            try:
                portfolio_members.change_member(portfolio_id, principal.user_id, "viewer")
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(demote, principals))
    assert sorted(outcomes) == [200, 409]
    with get_session_factory()() as session:
        managers = session.scalars(select(PortfolioMembershipModel).where(
            PortfolioMembershipModel.portfolio_id == portfolio_id,
            PortfolioMembershipModel.role == "manager",
        )).all()
        assert len(managers) == 1


def test_concurrent_portfolio_creation_allocates_distinct_namesakes(postgres_portfolio_env):
    from portfolio_app.services.portfolio_store import create_portfolio
    ready = Barrier(2)
    def create(_index):
        ready.wait(timeout=5)
        return create_portfolio('Concurrent namesake', base_currency='USD', inception_date=date(2026, 1, 1))
    with ThreadPoolExecutor(max_workers=2) as executor:
        portfolios = list(executor.map(create, range(2)))
    assert len({row['portfolio_id'] for row in portfolios}) == 2
    assert all(row['portfolio_name'] == 'Concurrent namesake' for row in portfolios)
