"""Upgrade real PostgreSQL JSON/audit records in an isolated fixture database."""
from copy import deepcopy
import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_guidance
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env, _run_watchlist_upgrade


@pytest.mark.postgresql_integration
@pytest.mark.parametrize('postgres_watchlist_env', ['20261012_0071'], indirect=True)
def test_guidance_upgrade_preserves_custom_history_and_frozen_inputs(postgres_watchlist_env):
    path = Path(__file__).parents[1] / 'alembic/versions/20261014_0072_team_research_guidance.py'
    spec = importlib.util.spec_from_file_location('guidance_pg_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    old = {'role':'research_mandate','revision':2,'instrument_id':postgres_watchlist_env['instrument_id'],
        'mandate':{'guidance':'人工框架','section_skills':{**migration.LEGACY_SECTION_SKILLS,'themes':'人工主题','quantitative':''}},
        'author':{'origin':'user','user_id':'pm'},'versions':[{'version_id':'old:1','guidance':'保留历史'}]}
    frozen = {'research_dossiers':[{'mandate':deepcopy(old['mandate'])}], 'submitted_draft':{'body':'历史输入不变'}}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id='guidance-migration',title='Migration',visibility='team',instrument_ids=[]))
        session.flush()
        for key, kind, context in [('dossier-mandate:pg-guidance','note',old),('frozen-pg-run','analysis',frozen)]:
            session.add(ResearchEntry(entry_id=key,topic_id='guidance-migration',kind=kind,title=key,context_json=context))
        session.commit()
        before_frozen = dict(session.execute(select(ResearchEntry.__table__).where(ResearchEntry.entry_id=='frozen-pg-run')).mappings().one())
        old_row = session.get(ResearchEntry,'dossier-mandate:pg-guidance')
        previous_updated_at = old_row.updated_at.isoformat()
    _run_watchlist_upgrade(postgres_watchlist_env['database_url'], '20261014_0072')
    with get_session_factory()() as session:
        assert session.scalar(text('SELECT version_num FROM watchlist.alembic_version')) == '20261014_0072'
        assert dict(session.execute(select(ResearchEntry.__table__).where(ResearchEntry.entry_id=='frozen-pg-run')).mappings().one()) == before_frozen
        row = session.get(ResearchEntry,'dossier-mandate:pg-guidance')
        value = row.context_json
        assert value['revision']==3 and value['mandate']['section_skills']=={'overview':'','recent_updates':'','themes':'人工主题','quantitative':''}
        assert value['mandate']['guidance']=='人工框架'
        assert value['versions'][:-1] == old['versions']
        assert value['versions'][-1]['section_skills'] == old['mandate']['section_skills']
        assert value['versions'][-1]['updated_at'] == previous_updated_at
        assert value['author']['origin']=='migration' and row.read_context_json['author']==value['author']
        with principal_context(Principal('pm','测试成员','guidance-team')):
            initial = research_guidance.read_guidance(session)
            payload = research_guidance.GuidanceInput(guidance='团队通用',section_skills=initial['section_skills'],expected_revision=0)
            saved = research_guidance.save_guidance(session,payload)
            assert saved['revision']==1 and saved['versions'][0]['revision']==0
            assert research_guidance.freeze_guidance(session,{'research_actor':{'team_id':'guidance-team'}})['guidance']=='团队通用'
    with pytest.raises(RuntimeError, match='不可自动降级'):
        config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
        config.set_main_option('script_location',str(Path(__file__).parents[1] / 'alembic'))
        config.set_main_option('sqlalchemy.url',postgres_watchlist_env['database_url'])
        command.downgrade(config, '20261012_0071')
    with get_session_factory()() as session:
        assert session.scalar(text('SELECT version_num FROM watchlist.alembic_version')) == '20261014_0072'
        assert session.scalar(text('SELECT COUNT(*) FROM watchlist.research_guidance'))==1
