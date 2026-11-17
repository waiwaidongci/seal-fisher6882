import os,json,csv,io
from redis import Redis
from sqlalchemy import select
from fastapi import HTTPException
from src.models.run import Run
from src.models.review import Review
from src.models.study import Study
from src.models.publication import PublicationBatch, PublicationItem
from src.services import studies,dependencies
from src.services.domain_validation import validate_parameters
from src.services.audits import log
from src.utils.serializers import record,now,checksum

def create(db,data,user):
    study=studies.require(db,data.study_id)
    try: params=validate_parameters(data.parameters)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    try: payload=dependencies.build_input(db,data.study_id)
    except dependencies.DependencyError as e: raise HTTPException(422,str(e))
    except ValueError as e: raise HTTPException(422,str(e))
    if not payload['snapshot']: raise HTTPException(422,'先录入至少一条业务观测')
    run=Run(study_id=data.study_id,name=data.name,parameters=params,snapshot=payload['snapshot'],checksum=checksum(payload['snapshot']),study_fingerprint=payload['fingerprint'],created_at=now(),status='queued',kind='manual',aggregate=1 if study.kind=='joint' else 0)
    db.add(run);db.flush();log(db,user,'run.queue',str(run.id));db.commit()
    Redis.from_url(os.environ['REDIS_URL']).rpush('analysis-queue',run.id)
    return record(run)

def _decorate(db,run,with_fingerprint=False):
    result=record(run)
    review=db.scalar(select(Review).where(Review.run_id==run.id));result['review']=record(review) if review else None
    study=db.get(Study,run.study_id)
    result['current']=bool(study and study.current_run_id==run.id)
    result['aggregate']=bool(run.aggregate)
    result['study_kind']=study.kind if study else 'source'
    result['fingerprint_matches']=None
    if with_fingerprint and study:
        try: result['fingerprint_matches']=dependencies.build_input(db,study.id)['fingerprint']==run.study_fingerprint
        except (dependencies.DependencyError,ValueError): result['fingerprint_matches']=False
    return result

def get(db,id):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    return _decorate(db,run,with_fingerprint=True)

def list_runs(db):
    return [_decorate(db,x) for x in db.scalars(select(Run).order_by(Run.id.desc()).limit(200))]

def batches(db):
    rows=[]
    for b in db.scalars(select(PublicationBatch).order_by(PublicationBatch.id.desc()).limit(100)):
        x=record(b)
        x['items']=[{**record(i),'study_name':(db.get(Study,i.study_id).name if db.get(Study,i.study_id) else ''),'run_status':(db.get(Run,i.run_id).status if db.get(Run,i.run_id) else 'missing')} for i in db.scalars(select(PublicationItem).where(PublicationItem.batch_id==b.id).order_by(PublicationItem.study_id))]
        rows.append(x)
    return rows

def approve(db,id,data,user):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    if run.status!='completed': raise HTTPException(409,'只能复核已完成运行')
    if not run.applied or not (run.batch_id or _is_current(db,run)):
        raise HTTPException(409,'该结果尚未生效或已被更新版本取代，不能复核')
    if run.batch_id:
        batch=db.get(PublicationBatch,run.batch_id)
        if batch and batch.status!='applied': raise HTTPException(409,'结果所属发布批次未生效，不能复核')
    if db.scalar(select(Review).where(Review.run_id==id)): raise HTTPException(409,'已复核，记录不可覆盖')
    obj=Review(run_id=id,note=data.note,author=user.email,created_at=now());db.add(obj);log(db,user,'review.approve',f'{id}: {data.note}');db.commit();return record(obj)

def _is_current(db,run):
    study=db.get(Study,run.study_id)
    return bool(study and study.current_run_id==run.id)

def export(db,id,user):
    r=get(db,id)
    if r['status']!='completed': raise HTTPException(409,'尚无完成结果')
    if user.role=='viewer' and not r['review']: raise HTTPException(403,'观察员只能导出已复核结果')
    if not r['current']:
        log(db,user,'run.export.historical',f'{id} 导出历史版本，当前有效运行已更新');db.commit()
    stream=io.StringIO();w=csv.writer(stream);w.writerow(['运行编号','输入校验SHA256','研究指纹','指标','值'])
    for k,v in r['result']['metrics'].items(): w.writerow([id,r['checksum'],r['study_fingerprint'],k,json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v])
    w.writerow([id,r['checksum'],r['study_fingerprint'],'解释',r['result']['interpretation']]);log(db,user,'run.export',id);db.commit()
    return '\ufeff'+stream.getvalue()
