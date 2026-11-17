from pydantic import BaseModel,Field
class PurgeInput(BaseModel):
    reference:str=Field(min_length=1,max_length=120)
    reason:str=Field(default='',max_length=500)
