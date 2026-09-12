from __future__ import annotations
from enum import StrEnum
from typing import Annotated,Any,Literal,TypeAlias
from pydantic import BaseModel,ConfigDict,Field

class Capability(StrEnum):
    GESTURE='gesture'; EXPRESSION='expression'; LOOK_AT='look_at'; SPEECH='speech'; MOVE='move'; GRIP='grip'

class ActionBase(BaseModel): model_config=ConfigDict(extra='forbid',frozen=True)
class GestureAction(ActionBase):
    type:Literal['gesture']='gesture'; name:str=Field(min_length=1); parameters:dict[str,Any]=Field(default_factory=dict)
class ExpressionAction(ActionBase):
    type:Literal['expression']='expression'; name:str=Field(min_length=1); intensity:float=Field(default=1.0,ge=0,le=1)
class LookAtAction(ActionBase):
    type:Literal['look_at']='look_at'; target:str=Field(min_length=1)
class MoveAction(ActionBase):
    type:Literal['move']='move'; target:str=Field(min_length=1); parameters:dict[str,Any]=Field(default_factory=dict)
class GripAction(ActionBase):
    type:Literal['grip']='grip'; state:Literal['open','close','hold']; parameters:dict[str,Any]=Field(default_factory=dict)
class SpeakAction(ActionBase):
    type:Literal['speak']='speak'; text:str=Field(min_length=1); audio_uri:str|None=None; model_alias:str|None=None; voice:str|None=None; response_format:Literal['mp3','opus','aac','flac','wav','pcm']|None=None; speed:float|None=Field(default=None,gt=0,le=4)
class WaitAction(ActionBase): type:Literal['wait']='wait'; seconds:float=Field(ge=0)
class StopAction(ActionBase): type:Literal['stop']='stop'

PerformanceAction:TypeAlias=Annotated[GestureAction|ExpressionAction|LookAtAction|MoveAction|GripAction|SpeakAction|WaitAction|StopAction,Field(discriminator='type')]

def required_capability(action:PerformanceAction)->Capability|None:
    if isinstance(action,GestureAction): return Capability.GESTURE
    if isinstance(action,ExpressionAction): return Capability.EXPRESSION
    if isinstance(action,LookAtAction): return Capability.LOOK_AT
    if isinstance(action,MoveAction): return Capability.MOVE
    if isinstance(action,GripAction): return Capability.GRIP
    if isinstance(action,SpeakAction): return Capability.SPEECH
    return None
