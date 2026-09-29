#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export SYNC_TEST_ADMIN_URL="postgresql://${INVESTMENT_STUDIO_TEST_DB_USER:-$(id -un)}@${INVESTMENT_STUDIO_TEST_DB_HOST:-127.0.0.1}:${INVESTMENT_STUDIO_TEST_DB_PORT:-5432}/postgres"
"${PYTHON_BIN:-$ROOT/.venv/bin/python}" - <<'PY'
import os,sys,tempfile
from pathlib import Path
import psycopg
from psycopg import sql
sys.path.insert(0,str(Path.cwd()/'infra/scripts'))
import sync_cloud_to_local as sync
admin=os.environ['SYNC_TEST_ADMIN_URL']
prefix='studio_sync_test_'+str(os.getpid())
config={'admin_url':admin,'database_url':sync.database_url(admin,prefix),'files':{}}
state=Path(tempfile.mkdtemp(prefix='studio-sync-test-'))
directory=state/'snapshot';directory.mkdir()
for name in ('market','documents','research'):
 dest=state/(name+'-active');dest.mkdir();(dest/'value').write_text('old')
 staged=directory/name;staged.mkdir();(staged/'value').write_text('new')
 config['files'][name]={'local':str(dest),'remote':'/unused'}
sync.control_services=lambda action,state: None
sync.healthcheck=lambda cfg: (_ for _ in ()).throw(RuntimeError('simulated health failure'))
with psycopg.connect(admin,autocommit=True) as c:
 for db in (prefix,prefix+'_incoming'):
  c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db)))
try:
 for db,value in [(prefix,'old'),(prefix+'_incoming','new')]:
  with psycopg.connect(sync.database_url(admin,db)) as c:
   c.execute('CREATE TABLE marker(value text)');c.execute('INSERT INTO marker VALUES (%s)',(value,))
 try:sync.publish(config,directory,prefix+'_incoming')
 except RuntimeError as e:assert 'simulated' in str(e)
 with psycopg.connect(config['database_url']) as c:assert c.execute('SELECT value FROM marker').fetchone()==('old',)
 for item in config['files'].values():assert (Path(item['local'])/'value').read_text()=='old'
 sync.healthcheck=lambda cfg:None
 sync.publish(config,directory,prefix+'_incoming')
 with psycopg.connect(config['database_url']) as c:assert c.execute('SELECT value FROM marker').fetchone()==('new',)
 for item in config['files'].values():assert (Path(item['local'])/'value').read_text()=='new'
 print('Real PostgreSQL publish and failed-health rollback passed')
finally:
 with psycopg.connect(admin,autocommit=True) as c:
  for db in (prefix,prefix+'_incoming',prefix+'_before_snapshot'):
   c.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(db)))

PY
