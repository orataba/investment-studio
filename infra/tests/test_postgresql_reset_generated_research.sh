#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
"${PYTHON_BIN:-$ROOT/.venv/bin/python}" - <<'PY'
import getpass
import importlib.util
import os
from pathlib import Path
import subprocess
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

spec = importlib.util.spec_from_file_location('reset', Path('infra/scripts/reset_generated_research.py'))
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)
configured = os.environ.get('INVESTMENT_STUDIO_RESEARCH_RESET_TEST_DATABASE_URL')
created = None
settings = dict(host=os.environ.get('INVESTMENT_STUDIO_TEST_DB_HOST', '127.0.0.1'),
                port=os.environ.get('INVESTMENT_STUDIO_TEST_DB_PORT', '5432'),
                user=os.environ.get('INVESTMENT_STUDIO_TEST_DB_USER', getpass.getuser()), dbname='postgres')
if os.environ.get('INVESTMENT_STUDIO_TEST_DB_PASSWORD'):
    settings['password'] = os.environ['INVESTMENT_STUDIO_TEST_DB_PASSWORD']
try:
    if configured:
        dsn = configured.replace('postgresql+psycopg://', 'postgresql://')
        if not conninfo_to_dict(dsn)['dbname'].startswith('investment_studio_') or 'test' not in conninfo_to_dict(dsn)['dbname']:
            raise RuntimeError('Reset integration requires a dedicated investment_studio_*test* database')
    else:
        created = 'investment_studio_reset_test_' + uuid.uuid4().hex[:10]
        with psycopg.connect(**settings, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(created)))
        dsn = psycopg.conninfo.make_conninfo(**{**settings, 'dbname': created})
        url = f"postgresql+psycopg://{settings['user']}@{settings['host']}:{settings['port']}/{created}"
        env = dict(os.environ)
        if 'password' in settings:
            env['PGPASSWORD'] = settings['password']
        for module in ('HOME', 'INSTRUMENT_DATA', 'DATA', 'PORTFOLIO', 'WATCHLIST', 'MARKET', 'BRIEFING'):
            for suffix in ('DATABASE_URL', 'ALEMBIC_DATABASE_URL'):
                env[f'INVESTMENT_STUDIO_{module}_{suffix}'] = url
        subprocess.run(['infra/scripts/migrate_all.sh'], env=env, check=True, stdout=subprocess.DEVNULL)
    with psycopg.connect(dsn) as connection:
        connection.execute("SET LOCAL statement_timeout='30s'")
        connection.execute("""INSERT INTO watchlist.research_topic(topic_id,title,question,instrument_ids,status,conclusion)
            VALUES ('reset-test-topic','Question','Human question','[]','active','Human conclusion')""")
        for topic_id in ('instrument-events:reset-old', 'dossier:reset-material', 'reset-custom-empty'):
            connection.execute("""INSERT INTO watchlist.research_topic(topic_id,title,question,instrument_ids,status,conclusion)
                VALUES (%s,'Topic','','[]','active','')""", (topic_id,))
        for entry_id, kind in [('reset-test-run','analysis'),('reset-test-original','analysis'),
                               ('reset-test-transitive','analysis'),('reset-test-material','evidence'),
                               ('reset-test-topic-material','evidence'),('reset-test-note','note')]:
            connection.execute("""INSERT INTO watchlist.research_entry
                (entry_id,topic_id,kind,title,body,source,context_json,status,read_context_json,access_context_json)
                VALUES (%s,'reset-test-topic',%s,'Retained title','Content','Source','{}','completed','{}','{}')""", (entry_id,kind))
        connection.execute("UPDATE watchlist.research_entry SET topic_id='dossier:reset-material' WHERE entry_id='reset-test-topic-material'")
        from psycopg.types.json import Json, Jsonb
        # PostgreSQL JSON preserves escaped NUL in old human source text; JSONB
        # rejects it. Reset must not clean or force-cast those manual originals.
        manual_theme = {'role': 'research_theme', 'origin': 'user', 'updated_by_role': 'researcher',
                        'synthesis': 'Old AI', 'versions': [{'updated_by_role': 'user', 'question': 'Exact\\u0000literal\x00text'}]}
        connection.execute("""INSERT INTO watchlist.research_entry
            (entry_id,topic_id,kind,title,body,source,context_json,status,read_context_json,access_context_json)
            VALUES ('reset-test-theme','reset-test-topic','note','Theme','','',%s,'completed','{}','{}')""", (Json(manual_theme),))
        human_note = {'source_ids': ['original'], 'sources': [{'source_id': 'original',
            'retained_reference': {'run_id': 'reset-test-original', 'path': ['computed_metrics', '*']}}]}
        original = {'computed_metrics': [{'source_id': 'original', 'retained_reference': {
            'run_id': 'reset-test-transitive', 'path': ['computed_metrics', '*']}}]}
        transitive = {'computed_metrics': [{'source_id': 'original', 'data': {'weights': [0.4, 0.6]}}]}
        for entry_id, context in [('reset-test-note', human_note), ('reset-test-original', original),
                                   ('reset-test-transitive', transitive)]:
            connection.execute('UPDATE watchlist.research_entry SET context_json=%s WHERE entry_id=%s', (Jsonb(context),entry_id))
        connection.execute("""INSERT INTO instrument_data.instrument
            (instrument_id,instrument_name,instrument_type,currency,quote_selection_policy_json,
             source_settings_json,refresh_status_json,lifecycle_state_json,exchange_code)
            VALUES ('reset-test-asset','Protected asset','equity','USD','{"total_return":["adjusted_close"]}','{}','{}','{}','XNAS')""")
        connection.execute("""INSERT INTO watchlist.instrument_detail
            (instrument_id,instrument_type,detail_view_type,instrument_name,is_active,metadata_json)
            VALUES ('reset-test-asset','equity','equity','Protected asset',true,'{}')""")
        event_reference = {'source_id': 'calculation', 'instrument_id': 'reset-test-asset',
            'retained_reference': {'run_id': 'reset-test-run', 'path': ['computed_metrics', '*']}}
        event_evidence = {'follow_up_pinned': True, 'assessment': 'Manual\x00opinion', 'sources': [event_reference]}
        event_history = [{'recorded_by_role': 'user', 'snapshot': {'sources': [event_reference]}}]
        connection.execute("""INSERT INTO watchlist.risk_case
            (case_id,instrument_id,signal,title,body,severity,trigger_active,status,evidence_json,history_json)
            VALUES ('reset-test-event','reset-test-asset','sector:event','Human event','Manual body','attention',true,'open',%s,%s)""",
            (Json(event_evidence), Json(event_history)))
        connection.execute("""INSERT INTO watchlist.research_execution(run_id,attempt,status,queued_at)
            VALUES ('reset-test-run',1,'finished',now())""")
        event_original = {'source_id': 'calculation', 'source_type': 'computed_metric',
            'instrument_id': 'reset-test-asset', 'as_of': '2026-10-01', 'data': {'values': [0.1,0.9]}}
        connection.execute("""INSERT INTO watchlist.research_run_evidence(run_id,source_id,attempt,source_json,index_json,recorded_at)
            VALUES ('reset-test-run','calculation',1,%s,'{}',now())""", (Jsonb(event_original),))
        connection.execute("INSERT INTO watchlist.research_risk_read_state VALUES ('reset-test-run',1,'[]',NULL)")
        connection.execute("""INSERT INTO portfolio.portfolio_record
            (portfolio_id,portfolio_name,base_currency,valuation_timezone,valuation_cutoff_policy,securities_count,sort_order,inception_date)
            VALUES ('reset-test-portfolio','Protected portfolio','USD','Asia/Shanghai','market_close',0,0,'2026-01-01')""")
        connection.execute("""INSERT INTO portfolio.research_run_record(research_run_id,portfolio_id,job_type,status,lookback_days)
            VALUES ('reset-test-calculation','reset-test-portfolio','target_weight_solve','completed',90)""")
        facts = reset.fact_tables(connection)
        before = reset.fingerprint(connection,facts)
        plan = reset.load_plan(connection)
        assert 'reset-test-run' in plan['analysis_ids']
        assert set(plan['retained_analysis_ids']) == {'reset-test-original', 'reset-test-transitive'}
        reset.apply_plan(connection,plan)
        assert reset.fingerprint(connection,facts) == before
        assert connection.execute("SELECT topic_id FROM watchlist.research_topic WHERE topic_id='instrument-events:reset-old'").fetchone() is None
        assert connection.execute("SELECT topic_id FROM watchlist.research_topic WHERE topic_id='reset-custom-empty'").fetchone() is not None
        assert connection.execute("SELECT topic_id FROM watchlist.research_topic WHERE topic_id='dossier:reset-material'").fetchone() is not None
        event = connection.execute("SELECT title,body,evidence_json,history_json FROM watchlist.risk_case WHERE case_id='reset-test-event'").fetchone()
        assert event[:2] == ('Human event','Manual body')
        assert event[2] == {**event_evidence, 'sources': [event_original]}
        assert event[3] == [{'recorded_by_role': 'user', 'snapshot': {'sources': [event_original]}}]
        assert connection.execute("SELECT context_json FROM watchlist.research_entry WHERE entry_id='reset-test-theme'").fetchone()[0] == reset.reset_theme(manual_theme)
        assert connection.execute("SELECT kind FROM watchlist.research_entry WHERE topic_id='reset-test-topic' ORDER BY kind").fetchall() == [('analysis',),('analysis',),('evidence',),('note',),('note',)]
        for entry_id, context in [('reset-test-note', human_note), ('reset-test-original', original),
                                   ('reset-test-transitive', transitive)]:
            assert connection.execute('SELECT context_json FROM watchlist.research_entry WHERE entry_id=%s', (entry_id,)).fetchone()[0] == context
        for name in ('research_execution','research_run_evidence','research_risk_read_state'):
            assert connection.execute(sql.SQL('SELECT count(*) FROM watchlist.{} WHERE run_id=%s').format(sql.Identifier(name)),('reset-test-run',)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM portfolio.research_run_record WHERE research_run_id='reset-test-calculation'").fetchone()[0] == 0
        second = reset.load_plan(connection)
        assert second['analysis_ids'] == second['theme_ids'] == second['case_ids'] == second['portfolio_runs'] == []
        assert second['case_updates'] == {}
        assert second['empty_topic_ids'] == []
        reset.apply_plan(connection,second)
        assert reset.fingerprint(connection,facts) == before
        connection.rollback()
    print('PostgreSQL research reset preserved protected facts, materials and notes; FK children removed; second reset had no work.')
finally:
    if created:
        with psycopg.connect(**settings, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(created)))
PY
