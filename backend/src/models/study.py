from sqlalchemy import Column,Integer,String,Text
from src.config.database import Base
class SealTrial(Base):
    __tablename__='studies'
    id=Column(Integer,primary_key=True)
    name=Column(String(120),unique=True,nullable=False)
    source_filename=Column(String(120),nullable=False)
    description=Column(Text,default='')
    kind=Column(String(20),nullable=False,default='source',server_default='source')
    current_run_id=Column(Integer,nullable=True)
    current_batch_id=Column(Integer,nullable=True)
Study=SealTrial
