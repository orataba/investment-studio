"""Real request authentication plus portfolio ACLs; no identity dependency override."""
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from studio_identity import Principal, principal_context

from portfolio_app.db.models import PortfolioAccessStateModel, PortfolioMembershipModel, PortfolioAccessAuditModel, PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_access import initialize_access


@pytest.fixture
def secured(monkeypatch):
    people = {
        "alice": Principal("alice", "甲经理", "default", "admin", is_team_owner=True, session_id="alice-session"),
        "bob": Principal("bob", "乙经理", "default", session_id="bob-session"),
        "owner": Principal("owner", "团队拥有者", "default", "admin", is_team_owner=True),
        "other": Principal("other", "另一个团队", "other", "admin", is_team_owner=True),
        "maintenance": Principal(None, "估值维护", "default", kind="service", service_id="valuation", scopes=["portfolio:maintain"]),
    }
    class Home(BaseHTTPRequestHandler):
        def do_POST(self):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b'{}')
            principal = people.get(token)
            if principal is None:
                self.send_response(401); self.end_headers(); self.wfile.write(b'{"detail":"invalid session"}'); return
            if self.path.endswith('/introspect') and body.get('audience') == 'portfolio':
                data = principal.to_dict()
            else:
                self.send_response(403); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(data).encode())
        def do_GET(self):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in people:
                self.send_response(401); self.end_headers(); return
            data = {"members": [{"user_id": p.user_id, "display_name": p.display_name, "status": "active", "team_role": p.team_role} for p in people.values() if p.kind == 'user' and p.team_id == people[token].team_id]}
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(data).encode())
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Home)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv('INVESTMENT_STUDIO_AUTH_URL', f'http://127.0.0.1:{server.server_port}/api/auth')
    from portfolio_app.services.portfolio_store import create_portfolio
    from datetime import date
    create_portfolio("乙的组合", base_currency="CNY", inception_date=date(2026, 1, 1))
    with get_session_factory()() as session:
        ids = list(session.scalars(select(PortfolioRecordModel.portfolio_id).order_by(PortfolioRecordModel.portfolio_id)))
        a, b = ids[:2]
        initialize_access(session, a, people['alice'])
        initialize_access(session, b, people['bob'])
        session.add(PortfolioMembershipModel(portfolio_id=a, user_id='bob', display_name='乙经理', role='viewer', granted_by='alice', granted_at='2026-09-08'))
        session.commit()
    import portfolio_app.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, people, a, b
    server.shutdown(); server.server_close(); thread.join()


def headers(user='alice'):
    return {'Authorization': f'Bearer {user}'}


def test_unauthenticated_and_forged_identity_headers_cannot_read(secured):
    client, _, a, _ = secured
    for request_headers in ({}, {'X-User-ID': 'alice', 'X-Team-Role': 'admin'}, headers('revoked')):
        response = client.get(f'/api/portfolios/{a}/access', headers=request_headers)
        assert response.status_code == 401


def test_session_distinguishes_team_research_write_from_portfolio_read(secured):
    client, people, a, _ = secured
    from dataclasses import replace
    assert client.get('/api/portfolios/session', headers=headers('bob')).json()['can_write_team_research'] is True
    people['bob'] = replace(people['bob'], team_role='reader')
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('bob')).json()['can_read'] is True
    assert client.get('/api/portfolios/session', headers=headers('bob')).json()['can_write_team_research'] is False


def test_cookie_writes_require_trusted_origin(secured):
    client, _, a, _ = secured
    cookie = {'Cookie': '__Secure-yungu_session=alice'}
    assert client.get(f'/api/portfolios/{a}/access', headers=cookie).status_code == 200
    path = f'/api/portfolios/{a}/table-views/holdings'
    assert client.put(path, headers=cookie, json={'store': {}}).status_code == 403
    assert client.put(path, headers={**cookie, 'Origin': 'https://evil.example'}, json={'store': {}}).status_code == 403
    assert client.put(path, headers={**cookie, 'Origin': 'http://testserver'}, json={'store': {}}).status_code == 200


def test_directory_filters_before_valuation_and_no_admin_implicit_read(secured, monkeypatch):
    client, _, a, b = secured
    import portfolio_app.services.portfolio_store as store
    observed = []
    monkeypatch.setattr(store, '_serialize_portfolio_row_with_materialized_summary', lambda session, row: observed.append(row.portfolio_id) or {'portfolio_id': row.portfolio_id})
    response = client.get('/api/portfolios', headers=headers())
    assert response.status_code == 200
    assert [item['portfolio_id'] for item in response.json()] == [a]
    assert observed == [a]
    assert client.get(f'/api/portfolios/{b}/access', headers=headers()).status_code == 404
    assert client.get('/api/portfolios', headers=headers('owner')).json() == []
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('owner')).status_code == 404


@pytest.mark.parametrize('suffix', ['/accounts', '/transactions', '/transactions.csv', '/transactions.xlsx', '/transaction-captures', '/research/workbench', '/risk-context', '/snapshots/daily'])
def test_hidden_portfolio_all_real_surfaces_are_denied(secured, suffix):
    client, _, _, b = secured
    assert client.get(f'/api/portfolios/{b}{suffix}', headers=headers()).status_code == 404


def test_workspace_requires_explicit_accessible_portfolio(secured):
    client, _, _, b = secured
    assert client.get('/api/workspace/summary', headers=headers()).status_code == 422
    assert client.get('/api/workspace/summary', params={'portfolio_id': b}, headers=headers()).status_code == 404


@pytest.mark.parametrize('method,suffix', [('post', '/accounts'), ('post', '/transactions'), ('put', '/research/settings'), ('patch', ''), ('delete', ''), ('post', '/copy'), ('post', '/transaction-capture-batches')])
def test_viewer_cannot_mutate_business_facts(secured, method, suffix):
    client, _, a, _ = secured
    response = getattr(client, method)(f'/api/portfolios/{a}{suffix}', headers=headers('bob'), **({'json': {}} if method != 'delete' else {}))
    assert response.status_code == 403


def test_membership_change_and_revocation_are_immediate(secured):
    client, _, a, _ = secured
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('bob')).json()['role'] == 'viewer'
    path = f'/api/portfolios/{a}/members/bob'
    assert client.put(path, headers=headers('bob'), json={'role': 'manager'}).status_code == 403
    assert client.put(path, headers=headers(), json={'role': 'editor', 'granted_by': 'bob'}).status_code == 422
    assert client.put(path, headers=headers(), json={'role': 'editor'}).status_code == 200
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('bob')).json()['can_edit'] is True
    assert client.delete(path, headers=headers()).status_code == 200
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('bob')).status_code == 404
    with get_session_factory()() as session:
        event = session.scalars(select(PortfolioAccessAuditModel).where(PortfolioAccessAuditModel.action == 'membership_changed')).first()
        assert event.actor_user_id == 'alice'


def test_rename_requires_editor_and_preserves_current_access(secured):
    client, _, a, b = secured
    path = f'/api/portfolios/{a}'
    assert client.patch(path, headers=headers('bob'), json={'name': 'No authority'}).status_code == 403
    assert client.patch(f'/api/portfolios/{b}', headers=headers(), json={'name': 'Hidden'}).status_code == 404
    client.put(f'/api/portfolios/{a}/members/bob', headers=headers(), json={'role': 'editor'})
    response = client.patch(path, headers=headers('bob'), json={'name': ' 新名称 '})
    assert response.status_code == 200
    assert response.json()['portfolio_id'] == a
    assert response.json()['portfolio_name'] == '新名称'
    assert response.json()['access']['role'] == 'editor'
    assert response.json()['access']['can_edit'] is True
    assert response.json()['access']['can_manage'] is False


def test_last_active_manager_requires_handover(secured):
    client, _, a, _ = secured
    own = f'/api/portfolios/{a}/members/alice'
    assert client.delete(own, headers=headers()).status_code == 409
    assert client.put(f'/api/portfolios/{a}/members/bob', headers=headers(), json={'role': 'manager'}).status_code == 200
    assert client.delete(own, headers=headers()).status_code == 200
    assert client.get(f'/api/portfolios/{a}/access', headers=headers()).status_code == 404


def test_owner_recovery_is_explicit_and_audited(secured):
    client, _, a, _ = secured
    path = f'/api/portfolios/{a}/recover-access'
    payload = {'user_id': 'owner', 'reason': '原管理者离职，交接组合'}
    assert client.post(path, headers=headers('bob'), json=payload).status_code == 403
    assert client.post(path, headers=headers('other'), json={**payload, 'user_id': 'other'}).status_code == 404
    assert client.post(path, headers=headers('owner'), json=payload).status_code == 200
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('owner')).json()['can_manage']
    with get_session_factory()() as session:
        event = session.scalars(select(PortfolioAccessAuditModel).where(PortfolioAccessAuditModel.action == 'manager_recovered')).one()
        assert event.actor_user_id == 'owner'
        assert event.details_json['reason'] == payload['reason']


def test_personal_table_preferences_do_not_modify_shared_records(secured):
    client, _, a, _ = secured
    path = f'/api/portfolios/{a}/table-views/holdings'
    assert client.put(path, headers=headers('bob'), json={'store': {'columns': ['nav']}}).status_code == 200
    assert client.get(path, headers=headers('bob')).json()['store'] == {'columns': ['nav']}
    assert client.get(path, headers=headers()).json()['store'] is None


def test_created_portfolio_has_creator_only_and_team_scope(secured):
    client, _, _, _ = secured
    response = client.post('/api/portfolios', headers=headers(), json={'name': '新授权组合', 'base_currency': 'CNY', 'inception_date': '2026-09-01'})
    assert response.status_code == 200, response.text
    portfolio_id = response.json()['portfolio_id']
    assert response.json()['access']['role'] == 'manager'
    assert client.get(f'/api/portfolios/{portfolio_id}/access', headers=headers('bob')).status_code == 404


def test_scoped_agent_cannot_enumerate_or_escape_portfolio(secured):
    client, people, a, b = secured
    people['agent'] = Principal('bob', '乙经理', 'default', resource_scope={'kind': 'portfolio', 'id': a})
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('agent')).status_code == 200
    assert client.get(f'/api/portfolios/{b}/access', headers=headers('agent')).status_code == 404
    assert client.post('/api/portfolios', headers=headers('agent'), json={}).status_code == 403
    people['agent'] = Principal('bob', '乙经理', 'default', resource_scope={'kind': 'instrument', 'id': 'gold'})
    assert client.get('/api/portfolios', headers=headers('agent')).status_code == 403


def test_maintenance_reads_do_not_authorize_ledger_or_membership_write(secured):
    client, people, a, b = secured
    for portfolio_id in (a, b):
        assert client.get(f'/api/portfolios/{portfolio_id}/access', headers=headers('maintenance')).status_code == 200
        assert client.post(f'/api/portfolios/{portfolio_id}/transactions', headers=headers('maintenance'), json={}).status_code == 403
    assert client.post('/api/portfolios/snapshots/daily/recalculations', headers=headers('bob'), json={'refresh_all': True}).status_code == 403
    people['scoped-maintenance'] = Principal(None, '绑定维护任务', 'default', kind='service', service_id='valuation', scopes=['portfolio:maintain'], resource_scope={'kind': 'portfolio', 'id': a})
    assert client.post('/api/portfolios/snapshots/daily/recalculations', headers=headers('scoped-maintenance'), json={'refresh_all': True}).status_code == 403


def test_capture_credentials_are_task_bound_and_lose_access_on_revocation(secured):
    client, people, a, _ = secured
    image = b'\x89PNG\r\n\x1a\nportfolio-screenshot-fixture'
    def capture(content):
        response = client.post(f'/api/portfolios/{a}/transaction-captures', headers=headers(), files={'file': ('broker.png', content, 'image/png')})
        assert response.status_code == 201, response.text
        return response.json()['capture_id']
    first = capture(image)
    second = capture(image + b'2')
    def batch(capture_id):
        response = client.post(f'/api/portfolios/{a}/transaction-capture-batches', headers=headers(), json={'capture_ids': [capture_id], 'purpose': 'auto'})
        assert response.status_code == 201, response.text
        return response.json()['batch_id']
    task = batch(first)
    other_task = batch(second)
    people['capture-run'] = Principal('alice', '甲经理', 'default', resource_scope={'kind': 'capture', 'id': task})
    context = f'/api/portfolios/{a}/transaction-capture-batches/{task}/agent-context'
    assert client.get(context, headers=headers()).status_code == 403
    assert client.get(context, headers=headers('capture-run')).status_code == 200
    first_response = client.get(f'/api/portfolios/{a}/transaction-captures/{first}/image', headers=headers('capture-run'))
    assert first_response.status_code == 200
    assert first_response.headers['cache-control'] == 'private, no-store'
    assert client.get(f'/api/portfolios/{a}/transaction-captures/{second}/image', headers=headers('capture-run')).status_code == 403
    assert client.get(f'/api/portfolios/{a}/transaction-capture-batches/{other_task}/agent-context', headers=headers('capture-run')).status_code == 403
    assert client.post(f'/api/portfolios/{a}/transactions', headers=headers('capture-run'), json={}).status_code == 403
    assert client.get('/api/portfolios', headers=headers('capture-run')).status_code == 403
    with get_session_factory()() as session:
        session.delete(session.get(PortfolioMembershipModel, (a, 'alice')))
        session.commit()
    assert client.get(context, headers=headers('capture-run')).status_code == 404


def test_recovery_directory_is_owner_only_minimal_and_explicit(secured):
    client, _, a, b = secured
    assert client.get('/api/portfolios/access-recovery', headers=headers('bob')).status_code == 403
    response = client.get('/api/portfolios/access-recovery', headers=headers('owner'))
    assert response.status_code == 200, response.text
    assert {item['portfolio_id'] for item in response.json()['portfolios']} == {a, b}
    assert all(set(item) == {'portfolio_id', 'portfolio_name', 'has_active_manager'} for item in response.json()['portfolios'])
    assert client.get('/api/portfolios', headers=headers('owner')).json() == []
    with get_session_factory()() as session:
        event = session.scalars(select(PortfolioAccessAuditModel).where(PortfolioAccessAuditModel.action == 'recovery_directory_opened')).one()
        assert event.actor_user_id == 'owner'


def test_cookie_session_revocation_removes_access(secured):
    client, people, a, _ = secured
    assert client.get(f'/api/portfolios/{a}/access', headers=headers()).status_code == 200
    del people['alice']
    response = client.get(f'/api/portfolios/{a}/access', headers=headers())
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'private, no-store'


def test_explicit_research_service_read_is_team_wide_but_never_business_write(secured, monkeypatch):
    client, people, a, b = secured
    people['risk-service'] = Principal(None, '组合风险研究', 'default', kind='service', service_id='risk', scopes=['portfolio:read'])
    people['foreign-service'] = Principal(None, '其他团队服务', 'other', kind='service', service_id='foreign', scopes=['portfolio:read'])
    import portfolio_app.services.portfolio_store as store
    monkeypatch.setattr(store, '_serialize_portfolio_row_with_materialized_summary', lambda session, row: {'portfolio_id': row.portfolio_id})
    response = client.get('/api/portfolios', headers=headers('risk-service'))
    assert response.status_code == 200
    assert {row['portfolio_id'] for row in response.json()} == {a, b}
    assert client.get('/api/portfolios', headers=headers('foreign-service')).json() == []
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('foreign-service')).status_code == 404
    assert client.post(f'/api/portfolios/{a}/transactions', headers=headers('risk-service'), json={}).status_code == 403
    assert client.put(f'/api/portfolios/{a}/members/bob', headers=headers('risk-service'), json={'role': 'manager'}).status_code == 403
    assert client.post('/api/portfolios/snapshots/daily/recalculations', headers=headers('risk-service'), json={'refresh_all': True}).status_code == 403
    people['risk-service'] = Principal(None, '组合风险研究', 'default', kind='service', service_id='risk', scopes=['portfolio:read'], resource_scope={'kind': 'portfolio', 'id': a})
    assert {row['portfolio_id'] for row in client.get('/api/portfolios', headers=headers('risk-service')).json()} == {a}
    assert client.get(f'/api/portfolios/{b}/access', headers=headers('risk-service')).status_code == 404


def test_local_owner_uses_real_identity_and_manages_every_portfolio_without_grants(secured, monkeypatch):
    from datetime import date
    from studio_identity import LOCAL_OWNER_CREDENTIAL
    from portfolio_app.services.portfolio_store import create_portfolio
    client, people, a, b = secured
    orphan = create_portfolio('本机旧组合', base_currency='CNY', inception_date=date(2026, 1, 1))['portfolio_id']
    local = Principal('owner', 'shaw', 'default', 'admin', is_team_owner=True, local_unrestricted=True)
    people[LOCAL_OWNER_CREDENTIAL] = local
    people['local-owner'] = local
    # Account/cloud mode cannot accept a local marker, even from a token.
    assert client.get(f'/api/portfolios/{a}/access', headers=headers('local-owner')).status_code == 403
    monkeypatch.setenv('INVESTMENT_STUDIO_AUTH_MODE', 'local')
    import portfolio_app.services.portfolio_store as store
    monkeypatch.setattr(store, '_serialize_portfolio_row_with_materialized_summary', lambda session, row: {'portfolio_id': row.portfolio_id})
    with TestClient(client.app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as local_client:
        assert local_client.get('/api/portfolios/session').json()['display_name'] == 'shaw'
        assert {row['portfolio_id'] for row in local_client.get('/api/portfolios').json()} == {a, b, orphan}
        for portfolio_id in (a, b, orphan):
            access = local_client.get(f'/api/portfolios/{portfolio_id}/access').json()
            assert access['role'] == 'manager' and access['can_edit'] and access['can_manage']
            assert access['user_id'] == 'owner' and access['local_unrestricted']
        assert local_client.get('/api/portfolios/nonexistent/access').status_code == 404
        origin = {'Origin': 'http://127.0.0.1'}
        assert local_client.patch(f'/api/portfolios/{orphan}', headers=origin, json={'base_currency': 'CNY'}).status_code == 200
        assert local_client.put(f'/api/portfolios/{orphan}/members/bob', headers=origin, json={'role': 'viewer'}).status_code == 200
        # The local owner remains manager even after removing the last explicit grant.
        assert local_client.delete(f'/api/portfolios/{a}/members/alice', headers=origin).status_code == 200
    with get_session_factory()() as session:
        assert session.get(PortfolioMembershipModel, (orphan, 'owner')) is None
        audit = session.scalars(select(PortfolioAccessAuditModel).where(PortfolioAccessAuditModel.portfolio_id == orphan, PortfolioAccessAuditModel.action == 'membership_changed')).one()
        assert audit.actor_user_id == 'owner' and audit.actor_name == 'shaw'


def test_local_scoped_delegations_cannot_escape_or_write_ledger(secured, monkeypatch):
    from dataclasses import replace
    client, people, a, b = secured
    local = Principal('owner', 'shaw', 'default', 'admin', is_team_owner=True, local_unrestricted=True)
    people['local-owner'] = local
    people['local-portfolio'] = replace(local, resource_scope={'kind': 'portfolio', 'id': a})
    monkeypatch.setenv('INVESTMENT_STUDIO_AUTH_MODE', 'local')
    with TestClient(client.app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as local_client:
        image = local_client.post(f'/api/portfolios/{a}/transaction-captures', headers=headers('local-owner'), files={'file': ('broker.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')})
        assert image.status_code == 201, image.text
        batch = local_client.post(f'/api/portfolios/{a}/transaction-capture-batches', headers=headers('local-owner'), json={'capture_ids': [image.json()['capture_id']], 'purpose': 'auto'})
        assert batch.status_code == 201, batch.text
        batch_id = batch.json()['batch_id']
        people['local-capture'] = replace(local, resource_scope={'kind': 'capture', 'id': batch_id})
        assert local_client.get(f'/api/portfolios/{a}/transaction-capture-batches/{batch_id}/agent-context', headers=headers('local-capture')).status_code == 200
        for token in ('local-portfolio', 'local-capture'):
            assert local_client.get(f'/api/portfolios/{a}/access', headers=headers(token)).status_code == 200
            assert local_client.get(f'/api/portfolios/{b}/access', headers=headers(token)).status_code == 404
            assert local_client.post(f'/api/portfolios/{a}/transactions', headers=headers(token), json={}).status_code == 403
            assert local_client.put(f'/api/portfolios/{a}/members/bob', headers=headers(token), json={'role': 'manager'}).status_code == 403
            assert local_client.post('/api/portfolios/snapshots/daily/recalculations', headers=headers(token), json={'refresh_all': True}).status_code == 403
            assert local_client.post('/api/portfolios/snapshots/daily/recalculations', headers=headers(token), json={'portfolio_ids': [a]}).status_code == 403


def test_session_bootstrap_returns_current_user_capabilities_and_matching_acl(secured):
    client, people, a, b = secured
    response = client.get('/api/portfolios/session', params={'portfolio_id': a}, headers=headers('bob'))
    assert response.status_code == 200
    body = response.json()
    assert body['user_id'] == body['access']['user_id'] == 'bob'
    assert body['session_id'] == 'bob-session'
    assert body['access']['portfolio_id'] == a
    assert body['access']['role'] == 'viewer'
    assert isinstance(body['capabilities']['research_enabled'], bool)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert client.get('/api/portfolios/session', headers=headers('bob')).json()['access'] is None
    assert client.get('/api/portfolios/session', params={'portfolio_id': b}, headers=headers('alice')).status_code == 404
    assert client.get('/api/portfolios/session', params={'portfolio_id': a}, headers=headers('maintenance')).status_code == 403
    from dataclasses import replace
    people['task'] = replace(people['bob'], resource_scope={'kind': 'portfolio', 'id': a})
    assert client.get('/api/portfolios/session', params={'portfolio_id': a}, headers=headers('task')).status_code == 403
    client.delete(f'/api/portfolios/{a}/members/bob', headers=headers('alice'))
    assert client.get('/api/portfolios/session', params={'portfolio_id': a}, headers=headers('bob')).status_code == 404
