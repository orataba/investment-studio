from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import func, select
from studio_identity import current_principal, principal_context

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env, _run_watchlist_upgrade

pytestmark = pytest.mark.postgresql_integration


def test_concurrent_selection_retains_one_explicit_version(postgres_watchlist_env):
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models import InstrumentDetail
    from watchlist_app.db.models.research import InstrumentInvestmentStance
    from watchlist_app.api.contracts import InstrumentResearchNoteInput
    from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
    from watchlist_app.services.research_views import prepare_note_values, select_current_stance
    from watchlist_app.services.research_identity import research_identity
    _run_watchlist_upgrade(postgres_watchlist_env["database_url"])
    iid = postgres_watchlist_env["instrument_id"]
    principal, actor = current_principal(), research_identity()
    factory = get_session_factory()
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_name="Selection fixture", instrument_type="public_fund",
            detail_view_type="public_fund", metadata_json={}))
        session.flush()
        values = prepare_note_values(session, iid, InstrumentResearchNoteInput(note_date=date.today(), title="总体判断",
            body="有条件继续研究", research_context={"background": "既有资料和待验证的机制"}))
        SQLAlchemyInstrumentResearchRepository().create_note(session, instrument_id=iid, note_id="stance-note", values=values,
            updated_by=actor["user_id"], author_user_id=actor["user_id"], team_id=actor["team_id"])
        session.commit()
    ready = Barrier(2)
    def choose():
        with principal_context(principal), factory() as session:
            ready.wait(timeout=10)
            result = select_current_stance(session, iid, "stance-note", 1)
            session.commit()
            return result["selection_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: choose(), range(2)))
    assert first == second
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InstrumentInvestmentStance)) == 1
