#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export SYNC_TEST_ADMIN_URL="postgresql://${INVESTMENT_STUDIO_TEST_DB_USER:-$(id -un)}@${INVESTMENT_STUDIO_TEST_DB_HOST:-127.0.0.1}:${INVESTMENT_STUDIO_TEST_DB_PORT:-5432}/postgres"
"${PYTHON_BIN:-$ROOT/.venv/bin/python}" - <<'PY'
import json,os,sys,tempfile
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
original_write_journal=sync.write_cutover_journal
def fail_before_workers(path, record, phase, **kwargs):
 if phase=='writers-may-have-resumed':raise RuntimeError('simulated pre-start failure')
 return original_write_journal(path,record,phase,**kwargs)
sync.write_cutover_journal=fail_before_workers
with psycopg.connect(admin,autocommit=True) as c:
 for db in (prefix,prefix+'_incoming'):
  c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db)))
try:
 for db,value in [(prefix,'old'),(prefix+'_incoming','new')]:
  with psycopg.connect(sync.database_url(admin,db)) as c:
   c.execute('CREATE TABLE marker(value text)');c.execute('INSERT INTO marker VALUES (%s)',(value,))
   c.execute('CREATE SCHEMA identity')
   c.execute('CREATE TABLE identity.teams(id text PRIMARY KEY)')
   c.execute("INSERT INTO identity.teams VALUES ('default')")
   c.execute('''CREATE TABLE identity.service_credentials(
       id text PRIMARY KEY, token_hash text UNIQUE, service_id text,
       team_id text REFERENCES identity.teams(id), audiences json, scopes json,
       revoked_at timestamptz)''')
   c.execute('''INSERT INTO identity.service_credentials VALUES
       ('revoked','old-revoked-hash','reader','default','["market"]','["read"]',NULL),
       ('rotated','old-rotated-hash','writer','default','["portfolio"]','["write"]',NULL),
       ('deleted','old-deleted-hash','retired','default','[]','[]',NULL)''')
 # Simulate credentials changing locally after the incoming snapshot and its
 # initial credentials have already been restored; cloud credentials must not
 # survive either replacement, even if present in the staged fixture.
 with psycopg.connect(sync.database_url(admin,prefix+'_incoming')) as c:
  c.execute('''INSERT INTO identity.service_credentials VALUES
      ('cloud-only','cloud-hash','cloud','default','["market"]','["read"]',NULL)''')
 with psycopg.connect(config['database_url']) as c:
  c.execute("UPDATE identity.service_credentials SET revoked_at='2026-09-29T00:00:00Z' WHERE id='revoked'")
  c.execute("DELETE FROM identity.service_credentials WHERE id IN ('rotated','deleted')")
  c.execute('''INSERT INTO identity.service_credentials VALUES
      ('replacement','new-rotated-hash','writer','default','["portfolio"]','["read","write"]',NULL)''')
  expected_credentials=sync.local_credentials(c)
 try:sync.publish(config,directory,prefix+'_incoming')
 except RuntimeError as e:assert 'simulated' in str(e)
 with psycopg.connect(config['database_url']) as c:
  assert c.execute('SELECT value FROM marker').fetchone()==('old',)
  assert sync.local_credentials(c)==expected_credentials
  c.execute("UPDATE identity.service_credentials SET token_hash='latest-rotated-hash' WHERE id='replacement'")
  expected_credentials=sync.local_credentials(c)
 for item in config['files'].values():assert (Path(item['local'])/'value').read_text()=='old'
 # Rollback deliberately quarantines the failed database. This test reuses it
 # for a second publication; normal sync restores a fresh incoming database.
 with psycopg.connect(admin,autocommit=True) as c:
  sync.allow_connections(c,prefix+'_incoming',True)
 sync.write_cutover_journal=original_write_journal
 def start_writer(action,service_state):
  if action=='start':
   assert json.loads((directory/'cutover.json').read_text())['phase']=='writers-may-have-resumed'
   with psycopg.connect(config['database_url']) as c:
    c.execute("INSERT INTO marker VALUES ('local-write-after-start')")
   for item in config['files'].values():(Path(item['local'])/'local-write').write_text('preserved')
 sync.control_services=start_writer
 sync.healthcheck=lambda cfg: (_ for _ in ()).throw(RuntimeError('simulated health failure'))
 try:sync.publish(config,directory,prefix+'_incoming')
 except RuntimeError as e:assert 'automatic rollback is disabled' in str(e)
 else:raise AssertionError('health failure was not reported')
 with psycopg.connect(config['database_url']) as c:
  assert set(c.execute('SELECT value FROM marker').fetchall())=={('new',),('local-write-after-start',)}
  assert sync.local_credentials(c)==expected_credentials
 with psycopg.connect(admin,autocommit=True) as c:
  sync.allow_connections(c,prefix+'_before_snapshot',True)
 with psycopg.connect(sync.database_url(admin,prefix+'_before_snapshot')) as c:
  assert c.execute('SELECT value FROM marker').fetchone()==('old',)
 for name,item in config['files'].items():
  assert (Path(item['local'])/'value').read_text()=='new'
  assert (Path(item['local'])/'local-write').read_text()=='preserved'
  assert (directory/(name+'-before')/'value').read_text()=='old'
 assert json.loads((directory/'cutover.json').read_text())['phase']=='forward-repair-required'
 try:sync.check_unfinished_cutovers(state)
 except RuntimeError as e:assert 'unfinished' in str(e)
 else:raise AssertionError('unrepaired publication did not block the next sync')
 print('Real PostgreSQL pre-start rollback, post-start write retention, and latest local credentials passed')
finally:
 with psycopg.connect(admin,autocommit=True) as c:
  for db in (prefix,prefix+'_incoming',prefix+'_before_snapshot'):
   c.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(db)))

PY
