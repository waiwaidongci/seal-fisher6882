import json

from sqlalchemy import select

from src.models.observation import Observation
from src.models.study import Study
from src.models.study_member import StudyMember
from src.utils.serializers import checksum, now


class DependencyError(ValueError):
    """Raised when a joint study cannot be resolved into a clean source set."""


def _canonical(values):
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _children(db, study_id):
    return list(db.scalars(select(StudyMember).where(StudyMember.parent_id == study_id).order_by(StudyMember.ordinal, StudyMember.id)))


def closure_ids(db, study_id):
    """Return every study reachable through member edges (self included), rejecting any cycle."""
    seen = set()
    stack = [(study_id, (study_id,))]
    while stack:
        current_id, path = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        for member in _children(db, current_id):
            if member.child_id in path:
                names = ' → '.join(str(x) for x in path + (member.child_id,))
                raise DependencyError('联合研究存在循环引用：' + names)
            stack.append((member.child_id, path + (member.child_id,)))
    return seen


def resolve_leaves(db, study_id):
    """Resolve a study into its underlying source studies, cycle-safe and de-duplicated by study id."""
    root = db.get(Study, study_id)
    if not root:
        raise DependencyError('业务批次不存在')
    leaves = []
    seen = set()
    stack = [(study_id, (study_id,))]
    while stack:
        current_id, path = stack.pop()
        current = db.get(Study, current_id)
        if not current:
            raise DependencyError('引用的批次不存在')
        if current.kind == 'source':
            if current_id not in seen:
                seen.add(current_id)
                leaves.append(current)
            continue
        for member in _children(db, current_id):
            if member.child_id in path:
                names = ' → '.join(str(x) for x in path + (member.child_id,))
                raise DependencyError('联合研究存在循环引用：' + names)
            stack.append((member.child_id, path + (member.child_id,)))
    return leaves


def would_cycle(db, parent_id, child_ids):
    """True when making parent reference any child would close a cycle."""
    child_ids = set(child_ids)
    if parent_id in child_ids:
        return True
    try:
        for child_id in child_ids:
            if parent_id in closure_ids(db, child_id):
                return True
    except DependencyError:
        return True
    return False


def _leaf_rows(db, leaf):
    rows = list(db.scalars(select(Observation).where(Observation.study_id == leaf.id).order_by(Observation.source_key)))
    per_group = {}
    for row in rows:
        group = (row.values or {}).get('group')
        per_group[group] = per_group.get(group, 0) + 1
    for group, count in per_group.items():
        if count > 1:
            raise DependencyError(f'批次「{leaf.name}」的{group}组存在{count}条汇总计数；每批次每组仅允许一条，请编辑原记录后再联合')
    return rows


def collect(db, study_id, include_provenance=True):
    """Resolve, de-duplicate and aggregate a study into a frozen A/B comparison payload.

    De-duplication is by the underlying ``source_key``. The same physical record may be
    imported into more than one referenced batch (the diamond case); it is counted once.
    Two different values for the same source key are rejected instead of silently guessing.
    """
    root = db.get(Study, study_id)
    if not root:
        raise DependencyError('业务批次不存在')
    leaves = resolve_leaves(db, study_id)
    if not leaves:
        raise DependencyError('联合研究未引用任何来源批次')

    merged = {}
    order = []
    leaf_meta = []
    for leaf in leaves:
        rows = _leaf_rows(db, leaf)
        leaf_meta.append({
            'id': leaf.id,
            'name': leaf.name,
            'kind': leaf.kind,
            'row_count': len(rows),
            'fingerprint': checksum(sorted(({'source_key': r.source_key, 'values': r.values} for r in rows), key=lambda x: x['source_key'])),
        })
        for row in rows:
            values = dict(row.values or {})
            provenance = {
                'study_id': leaf.id,
                'study_name': leaf.name,
                'source_key': row.source_key,
                'values': values,
                'revision': row.revision,
                'updated_at': row.updated_at,
                'change_reason': row.change_reason,
            }
            existing = merged.get(row.source_key)
            if existing:
                if _canonical(existing['values']) != _canonical(values):
                    raise DependencyError(f'底层来源 {row.source_key} 在多个批次中取值冲突；按来源去重后无法唯一计数')
                existing['provenance'].append(provenance)
                continue
            merged[row.source_key] = {'source_key': row.source_key, 'values': values, 'provenance': [provenance]}
            order.append(row.source_key)

    groups = {}
    for key in order:
        entry = merged[key]
        group = entry['values'].get('group')
        if group not in ('A', 'B'):
            raise DependencyError(f'底层来源 {key} 的工艺组必须为A或B')
        for field in ('success', 'failure'):
            value = entry['values'].get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise DependencyError(f'底层来源 {key} 的{field}必须为非负计数')
        bucket = groups.setdefault(group, {'success': 0, 'failure': 0, 'provenance': []})
        bucket['success'] += entry['values']['success']
        bucket['failure'] += entry['values']['failure']
        bucket['provenance'].extend(entry['provenance'])
    if set(groups) != {'A', 'B'}:
        raise DependencyError('联合研究必须同时包含A组与B组的底层计数')

    snapshot = []
    for group in ('A', 'B'):
        bucket = groups[group]
        snapshot.append({
            'source_key': f'AGG-{group}',
            'values': {'group': group, 'success': _number(bucket['success']), 'failure': _number(bucket['failure'])},
            'provenance': bucket['provenance'] if include_provenance else [],
        })
    fingerprint = checksum([
        {'source_key': key, 'values': merged[key]['values']} for key in sorted(order)
    ])
    return {
        'study': root,
        'leaves': leaf_meta,
        'snapshot': snapshot,
        'fingerprint': fingerprint,
        'source_count': len(merged),
        'referenced_rows': sum(x['row_count'] for x in leaf_meta),
        'duplicate_rows': max(0, sum(x['row_count'] for x in leaf_meta) - len(merged)),
    }


def build_input(db, study_id):
    """Freeze the comparison input for any study, whether a source batch or joint study.

    Source batches keep their raw rows (the method itself rejects duplicate groups).
    Joint studies aggregate de-duplicated underlying source rows into one A and one B
    summary so repeated references cannot double count.
    """
    root = db.get(Study, study_id)
    if not root:
        raise DependencyError('业务批次不存在')
    if root.kind != 'joint':
        rows = list(db.scalars(select(Observation).where(Observation.study_id == root.id).order_by(Observation.source_key)))
        if not rows:
            raise DependencyError('空批次无法分析')
        snapshot = [
            {
                'source_key': r.source_key,
                'values': r.values,
                'provenance': [{
                    'study_id': root.id,
                    'study_name': root.name,
                    'source_key': r.source_key,
                    'revision': r.revision,
                    'updated_at': r.updated_at,
                    'change_reason': r.change_reason,
                }],
            }
            for r in rows
        ]
        return {
            'study': root,
            'leaves': [{'id': root.id, 'name': root.name, 'kind': root.kind, 'row_count': len(rows)}],
            'snapshot': snapshot,
            'fingerprint': checksum([{'source_key': x['source_key'], 'values': x['values']} for x in snapshot]),
            'source_count': len(rows),
            'referenced_rows': len(rows),
            'duplicate_rows': 0,
        }
    return collect(db, study_id)


def fingerprint_of(db, study_id):
    return build_input(db, study_id)['fingerprint']


def _number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def dependents(db, origin_id):
    """All joint studies whose dependency closure includes origin, transitively."""
    edges = list(db.scalars(select(StudyMember)))
    reverse = {}
    for edge in edges:
        reverse.setdefault(edge.child_id, set()).add(edge.parent_id)
    found = set()
    stack = [origin_id]
    while stack:
        current = stack.pop()
        for parent in reverse.get(current, set()):
            if parent not in found:
                found.add(parent)
                stack.append(parent)
    return [db.get(Study, x) for x in sorted(found)]


def dependency_detail(db, study_id):
    study = db.get(Study, study_id)
    if not study:
        raise DependencyError('业务批次不存在')
    direct = []
    for member in _children(db, study_id):
        child = db.get(Study, member.child_id)
        if child:
            direct.append({'id': child.id, 'name': child.name, 'kind': child.kind})
    leaves = []
    try:
        for leaf in resolve_leaves(db, study_id):
            rows = _leaf_rows(db, leaf)
            leaves.append({
                'id': leaf.id,
                'name': leaf.name,
                'kind': leaf.kind,
                'row_count': len(rows),
                'latest_update': max((r.updated_at or '' for r in rows), default=''),
                'rows': [
                    {
                        'source_key': r.source_key,
                        'values': r.values,
                        'revision': r.revision,
                        'updated_at': r.updated_at,
                        'change_reason': r.change_reason,
                    }
                    for r in rows
                ],
            })
        resolved = collect(db, study_id, include_provenance=False)
        fingerprint = resolved['fingerprint']
        error = ''
    except DependencyError as exc:
        leaves = leaves or []
        fingerprint = ''
        error = str(exc)
    return {
        'study': {'id': study.id, 'name': study.name, 'kind': study.kind, 'description': study.description},
        'direct_members': direct,
        'leaves': leaves,
        'fingerprint': fingerprint,
        'error': error,
        'generated_at': now(),
    }
