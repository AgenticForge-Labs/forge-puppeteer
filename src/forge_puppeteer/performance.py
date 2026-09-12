from __future__ import annotations
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .actions import PerformanceAction

class Cue(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    at:float=Field(ge=0); character:str=Field(min_length=1); action:PerformanceAction

class Performance(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    name:str=Field(min_length=1); cues:tuple[Cue,...]
    @field_validator('cues')
    @classmethod
    def order_cues(cls,cues): return tuple(sorted(cues,key=lambda c:c.at))
