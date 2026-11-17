"""Service-level verification for joint studies, de-duplication and atomic publication.

Run with an in-memory SQLite database; no Docker/MySQL/Redis required.
    python -m pytest backend/tests -q      (from the repository root)
"""
import types

import pytest


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import database as database_module
from src.config.database import Base


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, 'engine', engine)
    import src.models.user, src.models.study, src.models.observation, src.models.run
    import src.models.review, src.models.audit, src.models.study_member, src.models.publication
    Base.metadata.create_all(engine)
    with Session() as session:
        yield session


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    from src.services import publication
    monkeypatch.setattr(publication, 'push_ids', lambda ids: None)
    from src.services import runs as runs_service
    monkeypatch.setattr(runs_service.Redis, 'from_url', lambda *_a, **_k: types.SimpleNamespace(rpush=lambda *_x: None))
    from src.services import studies as studies_service
    monkeypatch.setattr(studies_service.publication, 'push_ids', lambda ids: None)
    yield


class User:
    email = 'analyst@demo.local'


def make_source(db, name, rows):
    from src.models.study import Study
    from src.models.observation import Observation
    study = Study(name=name, source_filename=name + '.csv', kind='source')
    db.add(study)
    db.flush()
    for key, values in rows.items():
        db.add(Observation(study_id=study.id, source_key=key, values=values, revision=1, updated_at='t0', change_reason='seed'))
    db.commit()
    return study


def make_joint(db, name, member_ids):
    from src.models.study import Study
    from src.models.study_member import StudyMember
    study = Study(name=name, source_filename='joint', kind='joint')
    db.add(study)
    db.flush()
    for ordinal, child in enumerate(member_ids):
        db.add(StudyMember(parent_id=study.id, child_id=child, ordinal=ordinal))
    db.commit()
    return study


def run_all(db):
    from src.services import publication
    from src.models.run import Run
    for run in list(db.query(Run).filter(Run.status.in_(['queued', 'running'])).order_by(Run.id)):
        publication.execute_run(db, run.id)
    return db.query(Run).order_by(Run.id).all()


def rows_ab(a_success, a_failure, b_success=12, b_failure=1):
    return {'A-row': {'group': 'A', 'success': a_success, 'failure': a_failure},
            'B-row': {'group': 'B', 'success': b_success, 'failure': b_failure}}


def rows_a(success, failure):
    return {'g': {'group': 'A', 'success': success, 'failure': failure}}


def rows_b(success, failure):
    return {'g': {'group': 'B', 'success': success, 'failure': failure}}


def test_diamond_reference_counts_each_source_once(db):
    from src.services import dependencies

    base = make_source(db, '基础批次', {'S-1': {'group': 'A', 'success': 10, 'failure': 2}, 'S-2': {'group': 'B', 'success': 12, 'failure': 1}})
    left = make_joint(db, '左支路', [base.id])
    right = make_joint(db, '右支路', [base.id])
    diamond = make_joint(db, '菱形联合', [left.id, right.id])
    # Distinct batches carrying the same underlying source record also merge by source key.
    mirror = make_source(db, '镜像批次', {'S-1': {'group': 'A', 'success': 10, 'failure': 2}, 'S-2': {'group': 'B', 'success': 12, 'failure': 1}})
    diamond2 = make_joint(db, '跨批次菱形', [left.id, right.id, mirror.id])

    payload = dependencies.build_input(db, diamond.id)
    assert payload['source_count'] == 2
    assert payload['referenced_rows'] == 2  # both branches resolve to the same two leaf rows
    counts = {row['values']['group']: (row['values']['success'], row['values']['failure']) for row in payload['snapshot']}
    assert counts == {'A': (10, 2), 'B': (12, 1)}

    wide = dependencies.build_input(db, diamond2.id)
    assert wide['source_count'] == 2
    assert wide['referenced_rows'] == 4
    assert wide['duplicate_rows'] == 2
    wide_counts = {row['values']['group']: (row['values']['success'], row['values']['failure']) for row in wide['snapshot']}
    assert wide_counts == counts


def test_conflicting_duplicate_source_values_rejected(db):
    from src.services import dependencies

    base = make_source(db, '来源甲', rows_a(10, 2))
    other = make_source(db, '来源乙', {'g': {'group': 'A', 'success': 11, 'failure': 1}})
    joint = make_joint(db, '冲突联合', [base.id, other.id])
    with pytest.raises(dependencies.DependencyError) as err:
        dependencies.build_input(db, joint.id)
    assert '冲突' in str(err.value)


def test_cycle_reference_rejected_and_original_members_kept(db):
    from src.services.studies import create_joint, update_members
    from src.schemas.study import JointStudyInput, JointMembersInput
    from fastapi import HTTPException

    base = make_source(db, '来源', rows_a(5, 5))
    a = create_joint(db, JointStudyInput(name='联合A', member_ids=[base.id]), User())
    b = create_joint(db, JointStudyInput(name='联合B', member_ids=[a['study']['id']]), User())
    with pytest.raises(HTTPException) as err:
        update_members(db, a['study']['id'], JointMembersInput(member_ids=[b['study']['id']]), User())
    assert err.value.status_code == 409
    a_ref = db.get(type(a) if False else __import__('src.models.study', fromlist=['Study']).Study, a['study']['id'])
    from src.models.study_member import StudyMember
    kept = [m.child_id for m in db.query(StudyMember).filter(StudyMember.parent_id == a_ref.id).all()]
    assert kept == [base.id]


def test_self_reference_and_direct_duplicate_members_rejected(db):
    from src.schemas.study import JointStudyInput, JointMembersInput
    from src.services.studies import create_joint, update_members
    from fastapi import HTTPException

    base = make_source(db, '来源', rows_a(5, 5))
    a = create_joint(db, JointStudyInput(name='联合A', member_ids=[base.id]), User())
    with pytest.raises(HTTPException) as err:
        update_members(db, a['study']['id'], JointMembersInput(member_ids=[a['study']['id']]), User())
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err2:
        update_members(db, a['study']['id'], JointMembersInput(member_ids=[base.id, base.id]), User())
    assert err2.value.status_code == 422


def test_continuous_corrections_only_latest_applies(db):
    from src.services import dependencies, publication
    from src.models.study import Study

    src = make_source(db, '来源', rows_ab(10, 2))
    other = make_source(db, '来源B', rows_b(12, 1))
    joint = make_joint(db, '联合', [src.id, other.id])
    from src.schemas.run import RunInput
    from src.services import runs as runs_service

    first = runs_service.create(db, RunInput(study_id=joint.id, name='初版', parameters={'alpha': 0.05}), User())
    run_all(db)
    db.expire_all()
    assert db.get(Study, joint.id).current_run_id == first['id']

    # Two corrections in a row queue two batches; only the newest may publish.
    from src.services import studies as studies_service
    from src.schemas.observation import ObservationInput
    original = db.query(__import__('src.models.observation', fromlist=['Observation']).Observation).filter_by(study_id=src.id, source_key='A-row').one()
    studies_service.save_observation(db, src.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 20, 'failure': 2}, change_reason='更正一'), User(), original.id)
    studies_service.save_observation(db, src.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 30, 'failure': 2}, change_reason='更正二'), User(), original.id)
    assert len(db.query(publication.PublicationBatch).all()) >= 2

    accepts = []
    for run in run_all(db):
        if run.kind == 'auto' and run.study_id == joint.id:
            accepts.append(run)
    for run in accepts:
        publication.attempt(db, run.batch_id)
    db.expire_all()
    current = db.get(Study, joint.id).current_run_id
    current_run = db.get(__import__('src.models.run', fromlist=['Run']).Run, current)
    assert current_run.result['metrics']['A组合格率'] == pytest.approx(30 / 32)
    batches = db.query(publication.PublicationBatch).all()
    applied = [b for b in batches if b.status == 'applied']
    assert len(applied) == 1 and applied[0].id == max(b.id for b in batches)
    assert sum(1 for b in batches if b.status == 'superseded') == 1
    # The superseded (middle) correction was computed but never switched.
    assert db.get(Study, joint.id).current_batch_id == applied[0].id


def test_atomic_batch_waits_for_all_and_keeps_old_on_failure(db):
    from src.services import publication
    from src.models.run import Run
    from src.models.study import Study

    a = make_source(db, '来源A', rows_ab(10, 2))
    b = make_source(db, '来源B', rows_b(12, 1))
    joint = make_joint(db, '联合', [a.id, b.id])
    from src.schemas.run import RunInput
    from src.services import runs as runs_service
    # Publish an initial version of both source batch and joint, so both have something to invalidate.
    runs_service.create(db, RunInput(study_id=a.id, name='来源初版', parameters={'alpha': 0.05}), User())
    runs_service.create(db, RunInput(study_id=joint.id, name='初版', parameters={'alpha': 0.05}), User())
    run_all(db)
    old = db.get(Study, joint.id).current_run_id
    assert old

    from src.services import studies as studies_service
    from src.schemas.observation import ObservationInput
    row = db.query(__import__('src.models.observation', fromlist=['Observation']).Observation).filter_by(study_id=a.id, source_key='A-row').one()
    studies_service.save_observation(db, a.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 15, 'failure': 2}, change_reason='更正'), User(), row.id)
    batch = db.query(publication.PublicationBatch).order_by(publication.PublicationBatch.id.desc()).first()

    items = db.query(publication.PublicationItem).filter_by(batch_id=batch.id).all()
    assert len(items) >= 2  # source batch and joint both recompute
    # Compute only the first item; the batch must remain pending and the old run stays current.
    publication.execute_run(db, items[0].run_id)
    db.expire_all()
    assert db.get(Study, joint.id).current_run_id == old
    assert db.get(publication.PublicationBatch, batch.id).status == 'pending'

    # Force a failure in a sibling run: nothing may switch.
    victim = db.get(Run, items[1].run_id)
    victim.status = 'failed'
    victim.error = '模拟失败'
    db.commit()
    publication.attempt(db, batch.id)
    db.expire_all()
    assert db.get(publication.PublicationBatch, batch.id).status == 'failed'
    assert '保留上一版' in db.get(publication.PublicationBatch, batch.id).reason
    assert db.get(Study, joint.id).current_run_id == old


def test_resume_after_interruption_reuses_runs_and_keeps_frozen_results(db):
    from src.services import publication
    from src.models.run import Run
    from src.models.study import Study
    from src.schemas.run import RunInput
    from src.services import studies as studies_service
    from src.services import runs as runs_service
    from src.schemas.observation import ObservationInput

    src = make_source(db, '来源', rows_ab(10, 2))
    other = make_source(db, '来源B', rows_b(12, 1))
    make_joint(db, '联合', [src.id, other.id])
    runs_service.create(db, RunInput(study_id=src.id, name='独立研究', parameters={'alpha': 0.05}), User())
    run_all(db)
    independent = db.get(Study, src.id).current_run_id
    assert independent

    row = db.query(__import__('src.models.observation', fromlist=['Observation']).Observation).filter_by(study_id=src.id, source_key='A-row').one()
    studies_service.save_observation(db, src.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 40, 'failure': 2}, change_reason='更正'), User(), row.id)
    batch = db.query(publication.PublicationBatch).order_by(publication.PublicationBatch.id.desc()).first()
    before = {r.id for r in db.query(Run).all()}

    # Simulate a crash after dequeue: one item marked running, nothing published yet.
    items = db.query(publication.PublicationItem).filter_by(batch_id=batch.id).order_by(publication.PublicationItem.study_id).all()
    crashed = db.get(Run, items[0].run_id)
    crashed.status = 'running'
    db.commit()

    requeued = publication.recover(db)
    assert sorted(requeued) == sorted(i.run_id for i in items)  # both unfinished items reused
    assert {r.id for r in db.query(Run).all()} == before  # no duplicated research
    assert db.get(Study, src.id).current_run_id == independent  # frozen independent result kept

    run_all(db)
    db.expire_all()
    assert db.get(publication.PublicationBatch, batch.id).status == 'applied'
    assert db.get(Study, src.id).current_run_id != independent
    frozen = db.get(Run, independent)
    assert frozen.status == 'completed' and frozen.result  # old result still stored


def test_unaffected_study_not_recomputed(db):
    from src.services import publication
    from src.models.run import Run
    from src.schemas.run import RunInput
    from src.services import runs as runs_service

    a = make_source(db, '来源A', rows_ab(10, 2))
    b = make_source(db, '来源B', rows_b(12, 1))
    joint = make_joint(db, '联合', [a.id])
    runs_service.create(db, RunInput(study_id=a.id, name='来源初版', parameters={'alpha': 0.05}), User())
    runs_service.create(db, RunInput(study_id=joint.id, name='联合初版', parameters={'alpha': 0.05}), User())
    runs_service.create(db, RunInput(study_id=b.id, name='无关研究', parameters={'alpha': 0.05}), User())
    run_all(db)
    before = set(x.id for x in db.query(Run).filter(Run.study_id == b.id))

    from src.services import studies as studies_service
    from src.schemas.observation import ObservationInput
    row = db.query(__import__('src.models.observation', fromlist=['Observation']).Observation).filter_by(study_id=a.id, source_key='A-row').one()
    studies_service.save_observation(db, a.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 9, 'failure': 3}, change_reason='更正'), User(), row.id)

    batch = db.query(publication.PublicationBatch).order_by(publication.PublicationBatch.id.desc()).first()
    planned = {i.study_id for i in db.query(publication.PublicationItem).filter_by(batch_id=batch.id).all()}
    assert a.id in planned and joint.id in planned
    assert b.id not in planned
    after = set(x.id for x in db.query(Run).filter(Run.study_id == b.id))
    assert after == before


def test_dependency_detail_reports_provenance(db):
    from src.services import dependencies
    from src.schemas.study import JointStudyInput
    from src.services.studies import create_joint

    base = make_source(db, '来源', {'S-1': {'group': 'A', 'success': 7, 'failure': 1}, 'S-2': {'group': 'B', 'success': 6, 'failure': 2}})
    joint = create_joint(db, JointStudyInput(name='联合', member_ids=[base.id]), User())
    detail = dependencies.dependency_detail(db, joint['study']['id'])
    assert detail['error'] == ''
    assert detail['leaves'][0]['name'] == '来源'
    assert {r['source_key'] for r in detail['leaves'][0]['rows']} == {'S-1', 'S-2'}
    assert all(r['revision'] == 1 and r['change_reason'] for r in detail['leaves'][0]['rows'])


def test_indirect_cycle_inside_existing_graph_rejected(db):
    from src.services import dependencies
    from src.services.studies import create_joint
    from src.schemas.study import JointStudyInput
    from src.models.study_member import StudyMember

    base = make_source(db, '来源', rows_ab(5, 5))
    a = create_joint(db, JointStudyInput(name='联合甲', member_ids=[base.id]), User())
    b = create_joint(db, JointStudyInput(name='联合乙', member_ids=[a['study']['id']]), User())
    c = create_joint(db, JointStudyInput(name='联合丙', member_ids=[b['study']['id']]), User())
    # Directly corrupt the graph to contain a three-hop cycle; resolution must still refuse.
    db.add(StudyMember(parent_id=a['study']['id'], child_id=c['study']['id'], ordinal=5))
    db.commit()
    with pytest.raises(dependencies.DependencyError) as err:
        dependencies.build_input(db, c['study']['id'])
    assert '循环' in str(err.value)
    with pytest.raises(dependencies.DependencyError):
        dependencies.closure_ids(db, a['study']['id'])


def test_joint_value_conflict_blocks_batch_and_keeps_old(db):
    from src.services import publication
    from src.models.study import Study
    from src.models.run import Run
    from src.schemas.run import RunInput
    from src.schemas.observation import ObservationInput
    from src.services import runs as runs_service
    from src.services import studies as studies_service
    from src.models.observation import Observation

    a = make_source(db, '来源A', rows_ab(10, 2))
    mirror = make_source(db, '镜像', {'A-row': {'group': 'A', 'success': 10, 'failure': 2}, 'B-row': {'group': 'B', 'success': 12, 'failure': 1}})
    joint = make_joint(db, '联合', [a.id, mirror.id])
    runs_service.create(db, RunInput(study_id=joint.id, name='初版', parameters={'alpha': 0.05}), User())
    assert joint.id
    run_all(db)
    old = db.get(Study, joint.id).current_run_id
    assert old

    # Editing one copy of the shared source makes the de-duplicated value ambiguous.
    row = db.query(Observation).filter_by(study_id=mirror.id, source_key='A-row').one()
    studies_service.save_observation(db, mirror.id, ObservationInput(source_key='A-row', values={'group': 'A', 'success': 99, 'failure': 2}, change_reason='镜像单独更正'), User(), row.id)
    latest = db.query(publication.PublicationBatch).order_by(publication.PublicationBatch.id.desc()).first()
    assert latest.status == 'failed'
    assert '保留上一版' in latest.reason and '冲突' in latest.reason
    db.expire_all()
    assert db.get(Study, joint.id).current_run_id == old
    assert db.get(Run, old).status == 'completed'
