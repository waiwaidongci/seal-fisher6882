from pydantic import BaseModel,Field
class TrialCountInput(BaseModel):
    source_key:str=Field(min_length=1,max_length=100)
    values:dict
    change_reason:str=Field(default='',max_length=500)
ObservationInput=TrialCountInput
