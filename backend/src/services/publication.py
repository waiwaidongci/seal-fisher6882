import logging
import os

from redis import Redis
from sqlalchemy import select

from src.models.publication import PublicationBatch, PublicationItem
from src.models.run import Run
from src.models.study import Study
from src.services import dependencies
from src.services.algorithm import calculate
from src.services.audits import log
from src.utils.serializers import checksum, now

_log = logging.getLogger(__name__)


def push_ids(ids):
    """Hand queued run ids to Redis. Kept separate so workers/tests can avoid the broker."""
    if not ids:
        return
    Redis.from_url(os.environ['REDIS_URL']).rpush('analysis-queue', *[int(x) for x in ids])


def _latest_parameters(db, study):
    if study.current_run_id:
        run = db.get(Run, study.current_run_id)
        if run and run.parameters is not None:
            return dict(run.parameters)
    latest = db.scalar(select(Run).where(Run.study_id == study.id).order_by(Run.id.desc()))
    if latest and latest.parameters is not None:
        return dict(latest.parameters)
    from src.config.domain import DOMAIN
    return dict(DOMAIN['defaults'])


def _failed_batch(db, trigger_kind, trigger_study_id, reason):
    batch = PublicationBatch(
        status='failed', reason=reason, trigger_kind=trigger_kind,
        trigger_study_id=trigger_study_id, created_at=now(), finished_at=now(),
    )
    db.add(batch)
    db.flush()
    log(db, f'system:{trigger_study_id}', 'publication.blocked', reason)
    return batch


def recalculate(db, origin_id, trigger_kind, reason, actor):
    """Queue re-computation for every study whose resolved inputs changed.

    Affected studies are the origin plus all transitive dependents. Studies whose
    resolved fingerprint already equals their published result are left untouched.
    If any affected study cannot resolve its dependency chain, the whole publication
    is recorded as blocked and every existing result stays in place.
    """
    # Serialise concurrent corrections per origin study (MySQL row lock; SQLite is serial anyway).
    origin = db.scalar(select(Study).where(Study.id == origin_id).with_for_update())
    if not origin:
        return None
    affected = [origin] + [s for s in dependencies.dependents(db, origin_id) if s]
    planned = []
    errors = []
    for study in affected:
        # A study without a published version has nothing to invalidate; its first
        # computation stays an explicit, named manual run.
        if not study.current_run_id:
            continue
        try:
            payload = dependencies.build_input(db, study.id)
        except dependencies.DependencyError as exc:
            errors.append(f'「{study.name}」{exc}')
            continue
        except ValueError as exc:
            errors.append(f'「{study.name}」{exc}')
            continue
        current = db.get(Run, study.current_run_id)
        if current and current.study_fingerprint == payload['fingerprint']:
            continue
        planned.append((study, payload))
    if errors:
        return _failed_batch(db, trigger_kind, origin_id, '依赖链无法解析，保留上一版结果：' + '；'.join(errors))
    if not planned:
        return None

    batch = PublicationBatch(
        status='pending', reason=reason, trigger_kind=trigger_kind,
        trigger_study_id=origin_id, created_at=now(),
    )
    db.add(batch)
    db.flush()
    ids = []
    for study, payload in planned:
        run = Run(
            study_id=study.id,
            name=f'{study.name} · 自动重算 {batch.id}',
            parameters=_latest_parameters(db, study),
            snapshot=payload['snapshot'],
            checksum=checksum(payload['snapshot']),
            study_fingerprint=payload['fingerprint'],
            status='queued',
            kind='auto',
            aggregate=1 if study.kind == 'joint' else 0,
            batch_id=batch.id,
            created_at=now(),
        )
        db.add(run)
        db.flush()
        db.add(PublicationItem(batch_id=batch.id, study_id=study.id, run_id=run.id, fingerprint=payload['fingerprint']))
        ids.append(run.id)
    log(db, actor, 'publication.queue', f'batch {batch.id} · {reason} · runs {ids}')
    return batch, ids


def _active_items(db, batch_id):
    return list(db.scalars(select(PublicationItem).where(PublicationItem.batch_id == batch_id)))


def attempt(db, batch_id, actor='system'):
    """Try to publish a pending batch. All items switch together or nothing switches."""
    batch = db.scalar(select(PublicationBatch).where(PublicationBatch.id == batch_id).with_for_update())
    if not batch or batch.status != 'pending':
        return batch
    items = _active_items(db, batch_id)
    if not items:
        batch.status = 'failed'
        batch.reason = (batch.reason + '；' if batch.reason else '') + '批次没有待发布的运行'
        batch.finished_at = now()
        db.commit()
        return batch
    runs = []
    for item in items:
        run = db.get(Run, item.run_id)
        if not run:
            batch.status = 'failed'
            batch.reason = '关联运行缺失，保留上一版结果'
            batch.finished_at = now()
            db.commit()
            return batch
        runs.append(run)
    failures = [f'运行#{r.id}：{r.error or "计算失败"}' for r in runs if r.status == 'failed']
    if failures:
        batch.status = 'failed'
        batch.reason = '同批结果未全部就绪，保留上一版结果：' + '；'.join(failures)
        batch.finished_at = now()
        log(db, actor, 'publication.failed', f'batch {batch.id}: {batch.reason}')
        db.commit()
        return batch
    if any(r.status != 'completed' for r in runs):
        return batch

    newer = _newer_pending_batch(db, batch)
    if newer:
        batch.status = 'superseded'
        batch.reason = f'已有更新批次 #{newer.id} 排队，本批结果不再生效；上一版保留'
        batch.finished_at = now()
        for run in runs:
            run.error = batch.reason
        log(db, actor, 'publication.superseded', f'batch {batch.id}: {batch.reason}')
        db.commit()
        return batch

    stale = []
    for item, run in zip(items, runs):
        study = db.get(Study, item.study_id)
        if not study:
            stale.append('批次已被移除')
            continue
        if study.current_batch_id and study.current_batch_id > batch.id:
            stale.append(f'「{study.name}」已由更新的批次 #{study.current_batch_id} 发布')
            continue
        try:
            current_fingerprint = dependencies.build_input(db, study.id)['fingerprint']
        except (dependencies.DependencyError, ValueError) as exc:
            stale.append(f'「{study.name}」依赖已失效：{exc}')
            continue
        if current_fingerprint != item.fingerprint or run.study_fingerprint != item.fingerprint:
            stale.append(f'「{study.name}」期间出现更新的更正，本批结果已过期')
    if stale:
        batch.status = 'superseded'
        batch.reason = '未发布，保留上一版结果：' + '；'.join(stale)
        batch.finished_at = now()
        log(db, actor, 'publication.superseded', f'batch {batch.id}: {batch.reason}')
        db.commit()
        return batch

    for item, run in zip(items, runs):
        study = db.get(Study, item.study_id)
        study.current_run_id = run.id
        study.current_batch_id = batch.id
        run.applied = 1
    batch.status = 'applied'
    batch.finished_at = now()
    if not batch.reason:
        batch.reason = '全部关联结果就绪后原子切换'
    log(db, actor, 'publication.applied', f'batch {batch.id} · {len(items)} 项 · {batch.reason}')
    db.commit()
    return batch


def _newer_pending_batch(db, batch):
    study_ids = select(PublicationItem.study_id).where(PublicationItem.batch_id == batch.id)
    return db.scalar(
        select(PublicationBatch)
        .join(PublicationItem, PublicationItem.batch_id == PublicationBatch.id)
        .where(
            PublicationBatch.status == 'pending',
            PublicationBatch.id > batch.id,
            PublicationItem.study_id.in_(study_ids),
        )
        .order_by(PublicationBatch.id)
    )


def publish_manual(db, run, actor='system'):
    """Publish a user-triggered run only when its frozen input is still current."""
    study = db.get(Study, run.study_id)
    if not study:
        run.error = '业务批次不存在'
        db.commit()
        return False
    try:
        current = dependencies.build_input(db, study.id)['fingerprint']
    except (dependencies.DependencyError, ValueError) as exc:
        run.error = f'依赖已失效，结果未生效：{exc}'
        log(db, actor, 'run.superseded', f'{run.id}: {run.error}')
        db.commit()
        return False
    if current != run.study_fingerprint:
        run.error = '输入在计算期间已更新，结果未生效；旧版结果保留，请重新发起'
        log(db, actor, 'run.superseded', f'{run.id}: {run.error}')
        db.commit()
        return False
    study.current_run_id = run.id
    study.current_batch_id = None
    run.applied = 1
    log(db, actor, 'run.applied', f'{run.id} 已生效')
    db.commit()
    return True


def execute_run(db, run_id, actor='system'):
    """Compute one queued run and publish it when possible. Used by the worker and tests."""
    run = db.get(Run, run_id)
    if not run or run.status in ('completed', 'failed'):
        return run
    if run.batch_id:
        batch = db.get(PublicationBatch, run.batch_id)
        if batch and batch.status != 'pending':
            run.status = 'failed'
            run.error = f'发布批次 #{batch.id} 已{batch.status}，本次结果不生效；旧版保留'
            db.commit()
            return run
    run.status = 'running'
    db.commit()
    try:
        if checksum(run.snapshot) != run.checksum:
            raise ValueError('输入快照校验失败')
        result = calculate(run.snapshot, run.parameters)
        result['input_sha256'] = run.checksum
        result['study_fingerprint'] = run.study_fingerprint
        run.result = result
        run.status = 'completed'
    except Exception as exc:
        _log.exception('analysis failed')
        run.status = 'failed'
        run.error = str(exc)
    db.commit()
    if run.status == 'completed' and not run.batch_id:
        publish_manual(db, run, actor)
    if run.batch_id:
        attempt(db, run.batch_id, actor)
    return run


def recover(db):
    """After an interruption: re-queue unfinished runs and settle pending batches once.

    Only runs already in the database are reused, so no study or run is duplicated and
    studies that already published keep their frozen result.
    """
    ids = []
    seen = set()
    pending_batches = list(db.scalars(select(PublicationBatch).where(PublicationBatch.status == 'pending').order_by(PublicationBatch.id)))
    for batch in pending_batches:
        items = _active_items(db, batch.id)
        runs = [db.get(Run, i.run_id) for i in items]
        if any(r and r.status == 'failed' for r in runs):
            attempt(db, batch.id, 'system:recover')
            continue
        unfinished = [r for r in runs if r and r.status in ('queued', 'running')]
        if unfinished:
            for run in unfinished:
                if run.status == 'running':
                    run.status = 'queued'
                if run.id not in seen:
                    seen.add(run.id)
                    ids.append(run.id)
        elif runs and all(r and r.status == 'completed' for r in runs):
            attempt(db, batch.id, 'system:recover')
    # Manual runs, and runs whose batch can no longer publish, still need a terminal state.
    for run in db.scalars(select(Run).where(Run.status.in_(['queued', 'running'])).order_by(Run.id)):
        if run.id in seen:
            continue
        if run.batch_id:
            batch = db.get(PublicationBatch, run.batch_id)
            if batch and batch.status == 'pending':
                continue
            run.status = 'failed'
            run.error = f'发布批次 #{run.batch_id} 已{batch.status if batch else "缺失"}，结果不生效；旧版保留'
            continue
        if run.status == 'running':
            run.status = 'queued'
        seen.add(run.id)
        ids.append(run.id)
    db.commit()
    return ids
