from __future__ import annotations
from collections.abc import Mapping,Sequence
from dataclasses import dataclass,field
from functools import partial
from typing import Any,Protocol
import anyio
from .actions import Capability,GestureAction,PerformanceAction,StopAction
from .resources import ActionResource,default_action_resources

class Embodiment(Protocol):
    @property
    def capabilities(self)->frozenset[Capability]: ...
    def resources_for(self,action:PerformanceAction)->frozenset[ActionResource]: ...
    async def execute(self,action:PerformanceAction)->None: ...
    async def stop(self)->None: ...

DEFAULT_MOCK_CAPABILITIES=frozenset({Capability.GESTURE,Capability.EXPRESSION,Capability.LOOK_AT,Capability.MOVE,Capability.GRIP})
@dataclass
class MockEmbodiment:
    supported:frozenset[Capability]=DEFAULT_MOCK_CAPABILITIES
    resource_overrides:dict[str,frozenset[ActionResource]]=field(default_factory=dict)
    actions:list[PerformanceAction]=field(default_factory=list); stop_count:int=0
    @property
    def capabilities(self): return self.supported
    def resources_for(self,action): return self.resource_overrides.get(action.type,default_action_resources(action))
    async def execute(self,action): self.actions.append(action)
    async def stop(self): self.stop_count+=1

JointTarget=Mapping[str,float]|Sequence[float]
class SOARM101Embodiment:
    """Adapter for `soarm101-motion-sdk`; semantic gestures remain bounded here."""
    def __init__(self,arm:Any,gestures:Mapping[str,Sequence[JointTarget]],*,resource_id:str='motion-platform:soarm101',speed:float|None=None,acceleration:float|None=None):
        self._arm=arm; self._gestures={k:tuple(v) for k,v in gestures.items()}; self.resource_id=resource_id; self._speed=speed; self._acceleration=acceleration; self._active_handle=None
    @property
    def capabilities(self): return frozenset({Capability.GESTURE})
    def resources_for(self,action): return frozenset({ActionResource.MOTION}) if isinstance(action,GestureAction) else default_action_resources(action)
    async def execute(self,action):
        if isinstance(action,StopAction): await self.stop(); return
        if not isinstance(action,GestureAction): raise ValueError(f'SO-ARM101 does not support {action.type!r}')
        try: waypoints=self._gestures[action.name]
        except KeyError as exc: raise ValueError(f'unknown SO-ARM101 gesture: {action.name}') from exc
        try:
            for target in waypoints:
                handle=await anyio.to_thread.run_sync(partial(self._arm.move_joints,target,speed=self._speed,acceleration=self._acceleration,wait=False)); self._active_handle=handle; await anyio.to_thread.run_sync(handle.wait)
        finally: self._active_handle=None
    async def stop(self):
        if self._active_handle is not None: await anyio.to_thread.run_sync(self._active_handle.cancel)
        await anyio.to_thread.run_sync(self._arm.stop); self._active_handle=None
