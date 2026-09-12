from __future__ import annotations
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack,asynccontextmanager
from dataclasses import dataclass
from typing import Protocol
import anyio
from .actions import Capability,SpeakAction,StopAction,WaitAction,required_capability
from .embodiments import Embodiment
from .events import EventEmitter,EventType,PerformanceEvent
from .performance import Cue,Performance
from .resources import ActionResource

class Clock(Protocol):
    def now(self)->float: ...
    async def sleep(self,seconds:float)->None: ...
    async def sleep_until(self,target:float)->None: ...

class SystemClock:
    def now(self): return time.monotonic()
    async def sleep(self,seconds): await anyio.sleep(seconds)
    async def sleep_until(self,target): await anyio.sleep(max(0,target-self.now()))

class FakeClock:
    def __init__(self): self._time=0.0; self.requested_sleeps=[]
    def now(self): return self._time
    async def sleep(self,seconds):
        if seconds<0: raise ValueError('seconds must be non-negative')
        self.requested_sleeps.append(seconds); await anyio.sleep(0)
    async def sleep_until(self,target):
        if target>=self._time: self._time=target
        await anyio.sleep(0)

class VoiceProvider(Protocol):
    async def speak(self,text:str,**kwargs)->None: ...
    async def stop(self)->None: ...

@dataclass(frozen=True)
class Character:
    id:str; name:str; embodiment:Embodiment; voice:VoiceProvider|None=None
    @property
    def capabilities(self):
        caps=set(self.embodiment.capabilities)
        if self.voice is not None: caps.add(Capability.SPEECH)
        return frozenset(caps)

class PerformanceStage:
    """Performer registry/resource arbiter; distinct from capture/environment Stage."""
    def __init__(self,clock:Clock|None=None,emitter:EventEmitter|None=None):
        self.clock=clock or SystemClock(); self.emitter=emitter or EventEmitter(); self._characters={}; self._resource_locks={}
    @property
    def characters(self): return dict(self._characters)
    def register(self,character:Character):
        if character.id in self._characters: raise ValueError(f'Character already registered: {character.id}')
        self._characters[character.id]=character
    def get(self,character_id):
        try: return self._characters[character_id]
        except KeyError as exc: raise KeyError(f'Unknown character: {character_id}') from exc
    def validate(self,performance:Performance):
        for cue in performance.cues:
            character=self.get(cue.character); required=required_capability(cue.action)
            if required is not None and required not in character.capabilities: raise ValueError(f'{character.name} does not support capability: {required.value}')
    async def execute_cue(self,performance_name:str,cue:Cue):
        character=self.get(cue.character); action_name=cue.action.type
        await self.emitter.emit(PerformanceEvent(type=EventType.ACTION_STARTED,timestamp=self.clock.now(),performance=performance_name,character=character.id,action=action_name))
        try:
            if isinstance(cue.action,WaitAction): await self.clock.sleep(cue.action.seconds)
            elif isinstance(cue.action,StopAction): await self.stop_character(character.id)
            else:
                async with self._claim_resources(character,cue):
                    if isinstance(cue.action,SpeakAction):
                        if character.voice is None: raise ValueError(f'{character.name} has no voice provider')
                        await self.emitter.emit(PerformanceEvent(type=EventType.SPEECH_STARTED,timestamp=self.clock.now(),performance=performance_name,character=character.id,action='speak',detail=cue.action.text))
                        await character.voice.speak(cue.action.text,audio_uri=cue.action.audio_uri,model_alias=cue.action.model_alias,voice=cue.action.voice,response_format=cue.action.response_format,speed=cue.action.speed)
                        await self.emitter.emit(PerformanceEvent(type=EventType.SPEECH_FINISHED,timestamp=self.clock.now(),performance=performance_name,character=character.id,action='speak',detail=cue.action.text))
                    else: await character.embodiment.execute(cue.action)
        except anyio.get_cancelled_exc_class(): raise
        except BaseException as exc:
            await self.emitter.emit(PerformanceEvent(type=EventType.ACTION_FAILED,timestamp=self.clock.now(),performance=performance_name,character=character.id,action=action_name,error_type=type(exc).__name__,error_message=str(exc))); raise
        await self.emitter.emit(PerformanceEvent(type=EventType.ACTION_COMPLETED,timestamp=self.clock.now(),performance=performance_name,character=character.id,action=action_name))
    @asynccontextmanager
    async def _claim_resources(self,character:Character,cue:Cue)->AsyncIterator[None]:
        resources=frozenset({ActionResource.VOICE}) if isinstance(cue.action,SpeakAction) else character.embodiment.resources_for(cue.action)
        async with AsyncExitStack() as stack:
            for resource in sorted(resources,key=lambda x:x.value):
                lock=self._resource_locks.setdefault((character.id,resource),anyio.Lock()); await stack.enter_async_context(lock)
            yield
    async def stop_character(self,character_id):
        c=self.get(character_id)
        async with anyio.create_task_group() as tg:
            tg.start_soon(c.embodiment.stop)
            if c.voice is not None: tg.start_soon(c.voice.stop)
    async def stop_all(self):
        async with anyio.create_task_group() as tg:
            for c in self._characters.values():
                tg.start_soon(c.embodiment.stop)
                if c.voice is not None: tg.start_soon(c.voice.stop)

class TimelineDirector:
    def __init__(self,stage:PerformanceStage): self.stage=stage
    async def perform(self,performance:Performance):
        from itertools import groupby
        self.stage.validate(performance); start=self.stage.clock.now()
        async with self.stage.emitter.run():
            await self.stage.emitter.emit(PerformanceEvent(type=EventType.PERFORMANCE_STARTED,timestamp=start,performance=performance.name))
            try:
                async with anyio.create_task_group() as tg:
                    for at,grouped in groupby(performance.cues,key=lambda c:c.at):
                        await self.stage.clock.sleep_until(start+at)
                        for cue in grouped: tg.start_soon(self.stage.execute_cue,performance.name,cue)
            except BaseException as exc:
                with anyio.CancelScope(shield=True): await self.stage.stop_all()
                await self.stage.emitter.emit(PerformanceEvent(type=EventType.PERFORMANCE_FAILED,timestamp=self.stage.clock.now(),performance=performance.name,error_type=type(exc).__name__,error_message=str(exc)))
                await self.stage.emitter.emit(PerformanceEvent(type=EventType.PERFORMANCE_FINALIZED,timestamp=self.stage.clock.now(),performance=performance.name)); raise
            with anyio.CancelScope(shield=True): await self.stage.stop_all()
            await self.stage.emitter.emit(PerformanceEvent(type=EventType.PERFORMANCE_COMPLETED,timestamp=self.stage.clock.now(),performance=performance.name))
            await self.stage.emitter.emit(PerformanceEvent(type=EventType.PERFORMANCE_FINALIZED,timestamp=self.stage.clock.now(),performance=performance.name))
