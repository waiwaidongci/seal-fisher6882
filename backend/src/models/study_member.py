from sqlalchemy import Column,Integer,ForeignKey,UniqueConstraint
from src.config.database import Base
class StudyMember(Base):
    __tablename__='study_members'
    __table_args__=(UniqueConstraint('parent_id','child_id'),)
    id=Column(Integer,primary_key=True)
    parent_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    child_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    ordinal=Column(Integer,nullable=False,default=0)
