"""API contract smoke test: joint creation, cycle rejection, dependency view."""

import pytest


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class FakeRedis:
    def incr(self, *_a, **_k):
        return 1

    def expire(self, *_a, **_k):
        return True

    def rpush(self, *_a, **_k):
        return 1


@pytest.fixture()
def client(monkeypatch):
    from src.config import database as database_module
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, 'engine', engine)
    import src.models.user, src.models.study, src.models.observation, src.models.run
    import src.models.review, src.models.audit, src.models.study_member, src.models.publication
    from src.config.database import Base
    Base.metadata.create_all(engine)

    from src.middlewares import rate_limit
    monkeypatch.setattr(rate_limit, 'redis', FakeRedis())
    from src.services import publication
    monkeypatch.setattr(publication, 'push_ids', lambda ids: None)
    from src.services import runs as runs_service
    monkeypatch.setattr(runs_service.Redis, 'from_url', lambda *_a, **_k: FakeRedis())

    monkeypatch.setattr(database_module, 'Session', Session)

    from src.utils.security import hash_password
    with Session() as db:
        db.add(src.models.user.User(email='analyst@demo.local', role='analyst', password_hash=hash_password('pw')))
        db.add(src.models.user.User(email='viewer@demo.local', role='viewer', password_hash=hash_password('pw')))
        db.commit()

    from fastapi.testclient import TestClient
    from src.main import app
    from src.config.database import get_db
    def override_db():
        with Session() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test:
        yield test
    app.dependency_overrides.clear()




def auth(client, email='analyst@demo.local'):
    token = client.post('/auth/login', json={'email': email, 'password': 'pw'}).json()['token']
    return {'Authorization': 'Bearer ' + token}


def source(client, headers, name, rows):
    sid = client.post('/studies', json={'name': name, 'source_filename': name + '.csv'}, headers=headers).json()['id']
    for key, values in rows.items():
        response = client.post(f'/studies/{sid}/observations', json={'source_key': key, 'values': values, 'change_reason': '首次录入'}, headers=headers)
        assert response.status_code == 200, response.text
    return sid


def test_joint_and_cycle_over_api(client):
    headers = auth(client)
    a = source(client, headers, '批次甲', {'S1': {'group': 'A', 'success': 5, 'failure': 1}, 'S2': {'group': 'B', 'success': 6, 'failure': 1}})
    b = source(client, headers, '批次乙', {'S1': {'group': 'A', 'success': 5, 'failure': 1}})
    left = client.post('/joint-studies', json={'name': '左支路', 'member_ids': [a]}, headers=headers)
    assert left.status_code == 200, left.text
    left_id = left.json()['study']['id']
    right = client.post('/joint-studies', json={'name': '右支路', 'member_ids': [a, b]}, headers=headers)
    right_id = right.json()['study']['id']
    diamond = client.post('/joint-studies', json={'name': '菱形', 'member_ids': [left_id, right_id]}, headers=headers)
    assert diamond.status_code == 200, diamond.text

    detail = client.get(f'/studies/{right_id}/dependencies', headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body['error'] == ''
    assert len(body['leaves']) == 2
    assert len(client.get('/overview', headers=headers).json()['batches']) == 0

    # left → right is a legal deepening of the graph (no cycle yet).
    deepen = client.put(f'/studies/{left_id}/members', json={'member_ids': [right_id]}, headers=headers)
    assert deepen.status_code == 200, deepen.text
    # right → left would close the loop and must be refused, keeping the old members.
    cycle = client.put(f'/studies/{right_id}/members', json={'member_ids': [left_id]}, headers=headers)
    assert cycle.status_code == 409
    kept = client.get(f'/studies/{right_id}/dependencies', headers=headers).json()['direct_members']
    assert {m['id'] for m in kept} == {a, b}
    self_ref = client.put(f'/studies/{left_id}/members', json={'member_ids': [left_id]}, headers=headers)
    assert self_ref.status_code == 409
    duplicate = client.put(f'/studies/{left_id}/members', json={'member_ids': [a, a]}, headers=headers)
    assert duplicate.status_code == 422

    # A joint study cannot be edited as if it carried its own counts.
    row = client.post(f'/studies/{left_id}/observations', json={'source_key': 'X', 'values': {'group': 'A', 'success': 1, 'failure': 1}, 'change_reason': ''}, headers=headers)
    assert row.status_code == 422

    # Viewer may read the dependency chain but not modify it.
    viewer = auth(client, 'viewer@demo.local')
    assert client.get(f'/studies/{right_id}/dependencies', headers=viewer).status_code == 200
    assert client.put(f'/studies/{left_id}/members', json={'member_ids': [a]}, headers=viewer).status_code == 403


def test_update_reason_and_dependency_view(client):
    headers = auth(client)
    sid = source(client, headers, '批次', {'S1': {'group': 'A', 'success': 5, 'failure': 1}, 'S2': {'group': 'B', 'success': 6, 'failure': 1}})
    rows = client.get(f'/studies/{sid}/observations', headers=headers).json()
    target = rows[0]
    updated = client.put(f'/studies/{sid}/observations/{target["id"]}', json={'source_key': target['source_key'], 'values': {'group': 'A', 'success': 7, 'failure': 1}, 'change_reason': '复核发现抄录错误'}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()['revision'] == 2
    body = client.get(f'/studies/{sid}/dependencies', headers=headers).json()
    assert '复核发现抄录错误' in [r['change_reason'] for r in body['leaves'][0]['rows']]

def test_interrupted_publication_over_api_and_recovery(client):
    """End-to-end: correction queues a batch, interrupted worker resumes without duplicates."""
    from src.config.database import Session
    from src.services import publication
    from src.models.run import Run
    from src.models.study import Study

    headers = auth(client)
    a = source(client, headers, '批次A', {'S1': {'group': 'A', 'success': 10, 'failure': 2}, 'S2': {'group': 'B', 'success': 12, 'failure': 1}})
    joint = client.post('/joint-studies', json={'name': '联合研究', 'member_ids': [a]}, headers=headers).json()['study']['id']
    assert client.post('/runs', json={'study_id': a, 'name': '来源初版', 'parameters': {'alpha': 0.05}}, headers=headers).status_code == 200
    assert client.post('/runs', json={'study_id': joint, 'name': '联合初版', 'parameters': {'alpha': 0.05}}, headers=headers).status_code == 200
    with Session() as db:
        for run in db.query(Run).filter(Run.status == 'queued'):
            publication.execute_run(db, run.id)
    with Session() as db:
        old = db.get(Study, joint).current_run_id
    assert old

    row = client.get(f'/studies/{a}/observations', headers=headers).json()[0]
    updated = client.put(f'/studies/{a}/observations/{row["id"]}', json={'source_key': row['source_key'], 'values': {'group': 'A', 'success': 20, 'failure': 2}, 'change_reason': '连续更正之一'}, headers=headers)
    assert updated.status_code == 200
    batches = client.get('/publications', headers=headers).json()
    pending = [b for b in batches if b['status'] == 'pending']
    assert len(pending) == 1
    assert {i['study_id'] for i in pending[0]['items']} == {a, joint}
    # Nothing switched yet: publication waits for every related run in the batch.
    assert client.get(f'/runs/{old}', headers=headers).json()['current'] is True

    with Session() as db:
        item = db.query(publication.PublicationItem).filter_by(batch_id=pending[0]['id']).first()
        run = db.get(Run, item.run_id)
        run.status = 'running'
        db.commit()
        before = {r.id for r in db.query(Run)}
        requeued = publication.recover(db)
        assert requeued
        assert {r.id for r in db.query(Run)} == before
    with Session() as db:
        for run in db.query(Run).filter(Run.status.in_(['queued', 'running'])):
            publication.execute_run(db, run.id)
    batch = client.get('/publications', headers=headers).json()[0]
    assert batch['status'] == 'applied'
    assert client.get(f'/runs/{old}', headers=headers).json()['current'] is False
    newest = client.get('/runs', headers=headers).json()[0]
    assert newest['current']
    detail = client.get(f"/runs/{newest['id']}", headers=headers).json()
    assert detail['fingerprint_matches'] is True
    assert detail['batch_id'] == batch['id']
