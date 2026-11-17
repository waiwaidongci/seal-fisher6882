from sqlalchemy import Column,Integer,String,Text,ForeignKey,UniqueConstraint
from src.config.database import Base
class PublicationBatch(Base):
    __tablename__='publication_batches'
    id=Column(Integer,primary_key=True)
    status=Column(String(20),nullable=False,default='pending')
    reason=Column(Text,nullable=False,default='')
    error=Column(Text,nullable=False,default='')
    trigger_kind=Column(String(40),nullable=False,default='')
    trigger_study_id=Column(Integer,nullable=True)
    created_at=Column(String(40),nullable=False)
    finished_at=Column(String(40),nullable=False,default='')
class PublicationItem(Base):
    __tablename__='publication_items'
    __table_args__=(UniqueConstraint('batch_id','study_id'),)
    id=Column(Integer,primary_key=True)
    batch_id=Column(Integer,ForeignKey('publication_batches.id'),nullable=False)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
    fingerprint=Column(String(64),nullable=False)
