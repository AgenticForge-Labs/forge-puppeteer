"""Playback-driven conversational expression above native SDK/device authority.

This is a semantic *orchestration* boundary, not another motion controller.
Drivers must delegate to already authorized device SDK/ExecutionRuntime primitives;
the coordinator never assembles motor commands, inspects PCM or claims hardware
STOP/settling evidence. A failed/ambiguous driver result inhibits further calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import anyio


class BehaviorUnavailable(Exception):
    """The performer has no permitted native implementation of this behavior."""


@dataclass(frozen=True)
class BehaviorAck:
    """Confirmation of an adapter operation, not measured physical motion."""

    confirmed: bool
    evidence: str


class TalkingDriver(Protocol):
    """An adapter using existing device APIs and their resource/safety authority."""

    async def start_talking(self) -> BehaviorAck: ...
    async def stop_talking(self) -> BehaviorAck: ...
    async def set_idle(self) -> BehaviorAck: ...


State = Literal["idle", "talking", "unavailable", "blocked"]
Event = Literal["speech_started", "speech_finished", "speech_interrupted", "idle"]


@dataclass(frozen=True)
class PerformerResult:
    performer_id: str
    state: State
    evidence: str


@dataclass(frozen=True)
class BehaviorReport:
    turn_id: str
    event: Event
    performers: tuple[PerformerResult, ...]
    stale: bool = False


class ConversationBehaviors:
    """One authoritative playback lifecycle, broadcast across registered performers.

    Speech events must originate from *playback*, not generated text, response
    creation, server audio-done messages or inferred voice activity. The caller
    chooses a stable turn ID; stale finish/interruption events never end a newer
    turn. Hardware-safe cancellation belongs to each approved driver.
    """

    def __init__(self) -> None:
        self._drivers: dict[str, TalkingDriver] = {}
        self._states: dict[str, State] = {}
        self._evidence: dict[str, str] = {}
        self._turn: str | None = None
        self._lock = anyio.Lock()

    def register(self, performer_id: str, driver: TalkingDriver) -> None:
        if not performer_id or performer_id in self._drivers:
            raise ValueError("performer ID must be nonempty and unique")
        if any(other is driver for other in self._drivers.values()):
            raise ValueError("one driver cannot be registered under multiple performers")
        self._drivers[performer_id] = driver
        self._states[performer_id] = "idle"
        self._evidence[performer_id] = "registered; no actuator command"

    def _report(self, turn_id: str, event: Event, *, stale: bool = False) -> BehaviorReport:
        return BehaviorReport(
            turn_id, event,
            tuple(PerformerResult(name, self._states[name], self._evidence[name])
                  for name in self._drivers),
            stale=stale,
        )

    @staticmethod
    async def _operation(driver: TalkingDriver, operation: str) -> tuple[str, str]:
        try:
            ack = await getattr(driver, operation)()
            if not isinstance(ack, BehaviorAck):
                return "blocked", "invalid_adapter_ack"
            if not ack.confirmed:
                return "blocked", "unconfirmed_adapter_outcome"
            return "confirmed", ack.evidence
        except BehaviorUnavailable:
            return "unavailable", "native_behavior_unavailable"
        except PermissionError:
            return "unavailable", "not_authorized_or_resource_busy"
        except Exception:
            # No automatic retry of an operation whose device outcome is unknown.
            return "blocked", "adapter_outcome_unknown"

    async def _broadcast(self, operation: str, selected: tuple[str, ...]) -> dict[str, tuple[str, str]]:
        results: dict[str, tuple[str, str]] = {}

        async def invoke(name: str) -> None:
            results[name] = await self._operation(self._drivers[name], operation)

        async with anyio.create_task_group() as group:
            for name in selected:
                group.start_soon(invoke, name)
        return results

    async def _end_locked(self) -> None:
        talking = tuple(name for name, state in self._states.items() if state == "talking")
        outcomes = await self._broadcast("stop_talking", talking)
        for name in talking:
            status, evidence = outcomes[name]
            self._states[name] = "idle" if status == "confirmed" else (
                "unavailable" if status == "unavailable" else "blocked"
            )
            self._evidence[name] = evidence
        # Only confirmed stopping permits a transition to the native idle mode.
        settled = tuple(name for name in talking if outcomes[name][0] == "confirmed")
        idle_results = await self._broadcast("set_idle", settled)
        for name in settled:
            status, evidence = idle_results[name]
            self._states[name] = "idle" if status == "confirmed" else (
                "unavailable" if status == "unavailable" else "blocked"
            )
            self._evidence[name] = evidence

    async def speech_started(self, turn_id: str) -> BehaviorReport:
        if not turn_id:
            raise ValueError("turn_id required")
        async with self._lock:
            if turn_id == self._turn:
                return self._report(turn_id, "speech_started")
            if self._turn is not None:
                await self._end_locked()
            self._turn = turn_id
            available = tuple(name for name, state in self._states.items() if state == "idle")
            outcomes = await self._broadcast("start_talking", available)
            for name in available:
                status, evidence = outcomes[name]
                self._states[name] = "talking" if status == "confirmed" else (
                    "unavailable" if status == "unavailable" else "blocked"
                )
                self._evidence[name] = evidence
            return self._report(turn_id, "speech_started")

    async def _finish(self, turn_id: str, event: Event) -> BehaviorReport:
        if not turn_id:
            raise ValueError("turn_id required")
        async with self._lock:
            if turn_id != self._turn:
                return self._report(turn_id, event, stale=True)
            await self._end_locked()
            self._turn = None
            return self._report(turn_id, event)

    async def speech_finished(self, turn_id: str) -> BehaviorReport:
        return await self._finish(turn_id, "speech_finished")

    async def speech_interrupted(self, turn_id: str) -> BehaviorReport:
        return await self._finish(turn_id, "speech_interrupted")

    async def idle(self, turn_id: str) -> BehaviorReport:
        """Explicit idle request; never overrides a current speaking turn."""
        if not turn_id:
            raise ValueError("turn_id required")
        async with self._lock:
            if self._turn is not None:
                return self._report(turn_id, "idle", stale=True)
            available = tuple(name for name, state in self._states.items() if state == "idle")
            outcomes = await self._broadcast("set_idle", available)
            for name in available:
                status, evidence = outcomes[name]
                self._states[name] = "idle" if status == "confirmed" else (
                    "unavailable" if status == "unavailable" else "blocked"
                )
                self._evidence[name] = evidence
            return self._report(turn_id, "idle")
