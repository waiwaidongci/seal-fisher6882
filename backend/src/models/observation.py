from sqlalchemy import Column,Integer,String,JSON,ForeignKey,UniqueConstraint
from src.config.database import Base
class TrialCount(Base):
    __tablename__='observations'
    __table_args__=(UniqueConstraint('study_id','source_key'),)
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    source_key=Column(String(100),nullable=False)
    values=Column(JSON,nullable=False)
    revision=Column(Integer,nullable=False,default=1,server_default='1')
    updated_at=Column(String(40),nullable=False,default='',server_default='')
    change_reason=Column(String(500),nullable=False,default='',server_default='')
Observation=TrialCount
