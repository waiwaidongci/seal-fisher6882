import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models import run,review,audit,study_member,publication
from src.utils.security import hash_password

def seed():
    Base.metadata.create_all(engine)
    from src.config.schema import ensure_schema
    ensure_schema(engine)
    rng=np.random.default_rng(42)
    i=DOMAIN['index']
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            rows=[]
            if i==0:
                for n in range(120): rows.append({'moisture':round(float(np.clip(rng.normal(5.2+k*.7,.7),0,100)),3),'extract':round(float(np.clip(rng.normal(34+k*2,2.4),0,100)),3)})
            elif i==1:
                rows=[{'group':'A','success':87+k*2,'failure':13-k*2},{'group':'B','success':96-k,'failure':4+k}]
            elif i==2:
                for n in range(24):
                    b=float(rng.uniform(3,6));rows.append({'before':round(b,3),'after':round(max(.1,b+rng.normal(-.45+k*.3,.5)),3)})
            elif i==3:
                for n in range(90):
                    x=round(float(rng.uniform(4,28)),1);rows.append({'porosity':x,'absorption':round(max(0,x*(.18+k*.08)+rng.normal(0,1.5)),2)})
            else: rows=[{'p0':.08+k*.02,'p1':.1+k*.025,'cost_per_contact':.12+k*.04,'budget':3000+k*500}]
            for n,values in enumerate(rows):
                key=f'DEMO-{k+1}-{n+1:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,values=values,revision=1,updated_at='2026-11-17T09:15:00+00:00',change_reason='seed 首次录入（对应 seal-fisher-202611-1.csv）'))
        db.commit()
        seed_joint(db)
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation)}
        print(json.dumps(counts));return counts
def seed_joint(db):
    """A ready-made joint study: the two seal trials plus a nested branch that shares one of them.

    The nested branch re-references the first trial so the diamond path is visible in the UI;
    the resolver de-duplicates those rows by source key.
    """
    from src.models.study_member import StudyMember
    names=DOMAIN['names']
    source_a=db.scalar(select(Study).where(Study.name==names[0]))
    source_b=db.scalar(select(Study).where(Study.name==names[1]))
    if not source_a or not source_b: return
    branch=db.scalar(select(Study).where(Study.name=='薄膜一轮·复核支路'))
    if not branch:
        branch=Study(name='薄膜一轮·复核支路',source_filename='joint-synthesis',description='集团复核时与主方案共用的来源，用于演示按来源去重',kind='joint')
        db.add(branch);db.flush()
        db.add(StudyMember(parent_id=branch.id,child_id=source_a.id,ordinal=0))
        db.commit()
    joint=db.scalar(select(Study).where(Study.name=='砚田·联合复核'))
    if not joint:
        joint=Study(name='砚田·联合复核',source_filename='joint-synthesis',description='引用两个试封方案与一个嵌套支路；同一底层来源只计一次',kind='joint')
        db.add(joint);db.flush()
        for ordinal,child in enumerate((source_a,branch,source_b)):
            db.add(StudyMember(parent_id=joint.id,child_id=child.id,ordinal=ordinal))
        db.commit()
if __name__=='__main__': seed()
