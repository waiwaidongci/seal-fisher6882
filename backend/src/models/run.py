from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey
from src.config.database import Base
class ExactRun(Base):
    __tablename__='runs'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    name=Column(String(120),nullable=False)
    status=Column(String(20),default='queued')
    parameters=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    checksum=Column(String(64),nullable=False)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
    batch_id=Column(Integer,nullable=True)
    kind=Column(String(20),nullable=False,default='manual',server_default='manual')
    aggregate=Column(Integer,nullable=False,default=0,server_default='0')
    applied=Column(Integer,nullable=False,default=0,server_default='0')
    study_fingerprint=Column(String(64),nullable=False,default='',server_default='')
Run=ExactRun
