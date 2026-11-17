from fastapi import HTTPException
from sqlalchemy import select, func
from src.models.study import Study
from src.models.observation import Observation
from src.models.study_member import StudyMember
from src.models.run import Run
from src.models.publication import PublicationBatch, PublicationItem
from src.services import dependencies, publication
from src.services.domain_validation import validate_values
from src.services.audits import log
from src.utils.serializers import record, now


def _current(db, study):
    data = record(study)
    if study.current_run_id:
        run = db.get(Run, study.current_run_id)
        data['current_run'] = {k: getattr(run, k) for k in ('id', 'name', 'status', 'checksum', 'study_fingerprint', 'created_at', 'batch_id', 'kind')} if run else None
    else:
        data['current_run'] = None
    batch = db.get(PublicationBatch, study.current_batch_id) if study.current_batch_id else None
    data['current_batch'] = record(batch) if batch else None
    data['pending_batches'] = [
        record(x) for x in db.scalars(
            select(PublicationBatch).where(
                PublicationBatch.status == 'pending',
                PublicationBatch.id.in_(
                    select(PublicationItem.batch_id).where(PublicationItem.study_id == study.id)
                ),
            ).order_by(PublicationBatch.id)
        )
    ]
    return data


def list_studies(db):
    data=[]
    for s in db.scalars(select(Study).order_by(Study.id)):
        x=_current(db,s);x['row_count']=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==s.id))
        x['member_count']=db.scalar(select(func.count()).select_from(StudyMember).where(StudyMember.parent_id==s.id))
        data.append(x)
    return data


def create(db,data,user):
    if db.scalar(select(Study).where(Study.name==data.name)): raise HTTPException(409,'批次名称已存在')
    s=Study(**data.model_dump(),kind='source');db.add(s);log(db,user,'study.create',data.name);db.commit();return _current(db,s)


def create_joint(db,data,user):
    if db.scalar(select(Study).where(Study.name==data.name)): raise HTTPException(409,'批次名称已存在')
    member_ids=[int(x) for x in data.member_ids]
    if not member_ids: raise HTTPException(422,'联合研究至少引用一个批次')
    if len(set(member_ids))!=len(member_ids): raise HTTPException(422,'引用列表存在重复批次')
    for child_id in member_ids:
        child=db.get(Study,child_id)
        if not child: raise HTTPException(404,f'引用的批次 {child_id} 不存在')
        try: dependencies.closure_ids(db,child_id)
        except dependencies.DependencyError as e: raise HTTPException(422,str(e))
    s=Study(name=data.name,source_filename='joint-synthesis',description=data.description,kind='joint')
    db.add(s);db.flush()
    for ordinal,child_id in enumerate(member_ids):
        db.add(StudyMember(parent_id=s.id,child_id=child_id,ordinal=ordinal))
    log(db,user,'study.joint.create',f'{data.name}: {member_ids}')
    db.commit()
    return dependency_view(db,s.id)


def update_members(db,id,data,user):
    study=require(db,id)
    if study.kind!='joint': raise HTTPException(422,'仅联合研究可管理引用')
    member_ids=[int(x) for x in data.member_ids]
    if not member_ids: raise HTTPException(422,'联合研究至少引用一个批次')
    if len(set(member_ids))!=len(member_ids): raise HTTPException(422,'引用列表存在重复批次')
    for child_id in member_ids:
        if not db.get(Study,child_id): raise HTTPException(404,f'引用的批次 {child_id} 不存在')
    if dependencies.would_cycle(db,id,member_ids): raise HTTPException(409,'该引用会形成循环，已拒绝；原引用保持不变')
    db.query(StudyMember).filter(StudyMember.parent_id==id).delete()
    for ordinal,child_id in enumerate(member_ids): db.add(StudyMember(parent_id=id,child_id=child_id,ordinal=ordinal))
    log(db,user,'study.members',f'{id}: {member_ids}')
    db.commit()
    _recalculate(db,id,'联合研究的引用批次已更新',user)
    return dependency_view(db,id)


def require(db,id):
    s=db.get(Study,id)
    if not s: raise HTTPException(404,'业务批次不存在')
    return s


def observations(db,id):
    require(db,id);return [record(x) for x in db.scalars(select(Observation).where(Observation.study_id==id).order_by(Observation.id))]


def save_observation(db,id,data,user,row_id=None):
    study=db.scalar(select(Study).where(Study.id==id).with_for_update())
    if not study: raise HTTPException(404,'业务批次不存在')
    if study.kind!='source': raise HTTPException(422,'联合研究不直接录入计数；请编辑其来源批次')
    try: values=validate_values(data.values)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    other=db.scalar(select(Observation).where(Observation.study_id==id,Observation.source_key==data.source_key))
    if other and other.id!=row_id: raise HTTPException(409,'源记录编号重复')
    reason=(getattr(data,'change_reason','') or '').strip()
    if row_id:
        row=db.get(Observation,row_id)
        if not row or row.study_id!=id: raise HTTPException(404,'记录不存在')
        changed=row.source_key!=data.source_key or row.values!=values
        row.source_key=data.source_key;row.values=values
        if changed:
            row.revision=(row.revision or 1)+1;row.updated_at=now();row.change_reason=reason or '更正计数'
            detail=f'{id}/{data.source_key} 第{row.revision}版'
        else:
            detail=f'{id}/{data.source_key} 无变化'
    else:
        if len(observations(db,id))>=5000: raise HTTPException(422,'小型研究最多5000行')
        row=Observation(study_id=id,source_key=data.source_key,values=values,revision=1,updated_at=now(),change_reason=reason or '首次录入');db.add(row)
        detail=f'{id}/{data.source_key} 首次录入'
    log(db,user,'observation.save',detail);db.commit()
    if row_id is not None and row.revision>1:
        outcome=_recalculate(db,id,f'批次「{study.name}」的源记录 {data.source_key} 第{row.revision}次更正',user)
    elif row_id is None:
        outcome=_recalculate(db,id,f'批次「{study.name}」新增源记录 {data.source_key}',user)
    else:
        outcome=None
    data=record(row)
    if outcome and isinstance(outcome,tuple):
        data['publication_batch']=record(outcome[0]);data['queued_runs']=outcome[1]
    return data


def delete_observation(db,id,row_id,user):
    study=require(db,id)
    if study.kind!='source': raise HTTPException(422,'联合研究不直接维护计数')
    row=db.get(Observation,row_id)
    if not row or row.study_id!=id: raise HTTPException(404,'记录不存在')
    key=row.source_key
    db.delete(row);log(db,user,'observation.delete',f'{id}/{key}');db.commit()
    _recalculate(db,id,f'批次「{study.name}」删除源记录 {key}',user)
    return {'deleted':True}


def _recalculate(db,id,reason,user):
    try:
        outcome=publication.recalculate(db,id,'source-update',reason,user)
    except Exception as exc:
        db.rollback()
        from fastapi import HTTPException as _H
        raise _H(500,f'已保存更改，但重算排队失败：{exc}')
    db.commit()
    if outcome and isinstance(outcome,tuple):
        batch,ids=outcome
        _push(ids)
        return batch,ids
    return outcome


def _push(ids):
    try: publication.push_ids(ids)
    except Exception: pass


def dependency_view(db,id):
    try: return dependencies.dependency_detail(db,id)
    except dependencies.DependencyError as e: raise HTTPException(404,str(e))
