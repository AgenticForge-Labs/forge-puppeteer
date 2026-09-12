from __future__ import annotations
import inspect
from collections.abc import AsyncIterator,Awaitable,Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
import anyio
from anyio.streams.memory import MemoryObjectSendStream
from pydantic import BaseModel,ConfigDict

class EventType(StrEnum):
    PERFORMANCE_STARTED='performance_started'; PERFORMANCE_COMPLETED='performance_completed'; PERFORMANCE_FAILED='performance_failed'; PERFORMANCE_CANCELLED='performance_cancelled'; PERFORMANCE_FINALIZED='performance_finalized'; ACTION_STARTED='action_started'; ACTION_COMPLETED='action_completed'; ACTION_FAILED='action_failed'; SPEECH_STARTED='speech_started'; SPEECH_FINISHED='speech_finished'; SAFETY_STOP='safety_stop'

class PerformanceEvent(BaseModel):
    model_config=ConfigDict(frozen=True)
    type:EventType; timestamp:float; performance:str; character:str|None=None; action:str|None=None; detail:str|None=None; error_type:str|None=None; error_message:str|None=None

EventListener=Callable[[PerformanceEvent],Awaitable[None]|None]
@dataclass(frozen=True)
class ListenerError: event:PerformanceEvent; listener:str; error:Exception

class EventEmitter:
    """Inline control hooks plus queued best-effort telemetry."""
    def __init__(self,max_buffer_size:int=1024):
        self._telemetry_listeners:list[EventListener]=[]; self._control_listeners:list[EventListener]=[]; self._max_buffer_size=max_buffer_size; self._send_stream:MemoryObjectSendStream[PerformanceEvent]|None=None; self._running=False; self._listener_errors:list[ListenerError]=[]; self._dropped_events=0
    @property
    def listener_errors(self): return tuple(self._listener_errors)
    @property
    def dropped_events(self): return self._dropped_events
    def subscribe(self,listener:EventListener): self._telemetry_listeners.append(listener)
    def subscribe_control(self,listener:EventListener): self._control_listeners.append(listener)
    async def emit(self,event:PerformanceEvent):
        for listener in tuple(self._control_listeners): await self._invoke(listener,event)
        if self._send_stream is None: await self._dispatch(event); return
        try: self._send_stream.send_nowait(event)
        except anyio.WouldBlock: self._dropped_events+=1
    @asynccontextmanager
    async def run(self)->AsyncIterator['EventEmitter']:
        if self._running: raise RuntimeError('EventEmitter is already running')
        send,recv=anyio.create_memory_object_stream[PerformanceEvent](self._max_buffer_size); self._send_stream=send; self._running=True; body_error=None
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._consume,recv)
                try: yield self
                except BaseException as exc: body_error=exc
                finally:
                    with anyio.CancelScope(shield=True): await send.aclose()
        finally: self._send_stream=None; self._running=False
        if body_error is not None: raise body_error
    async def _consume(self,recv):
        with anyio.CancelScope(shield=True):
            async with recv:
                async for event in recv: await self._dispatch(event)
    async def _dispatch(self,event):
        for listener in tuple(self._telemetry_listeners):
            try: await self._invoke(listener,event)
            except Exception as exc: self._listener_errors.append(ListenerError(event,repr(listener),exc))
    @staticmethod
    async def _invoke(listener,event):
        result=listener(event)
        if inspect.isawaitable(result): await result
