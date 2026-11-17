from pydantic import BaseModel,Field
class SealTrialInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    source_filename:str=Field(min_length=3,max_length=120)
    description:str=Field(default='',max_length=2000)
StudyInput=SealTrialInput
class JointStudyInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    description:str=Field(default='',max_length=2000)
    member_ids:list[int]=Field(min_length=1,max_length=50)
JointStudyCreate=JointStudyInput
class JointMembersInput(BaseModel):
    member_ids:list[int]=Field(min_length=1,max_length=50)
JointMembersUpdate=JointMembersInput
