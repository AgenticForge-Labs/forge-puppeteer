"""One session-owned semantic ensemble over the existing ExecutionRuntime.

This module cannot open hardware, play audio or authorize motion. Every cue
uses the same runtime/provider admission and action journal as ordinary MCP
actions. A denied/busy/uncertain device cannot be silently retried or replaced.
"""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Iterable

import anyio

from .behavior_contract import (
    BehaviorPattern, EnsemblePresence, PerformerBehaviors, Presence,
)
from .execution import ActionRequest, ExecutionError, ExecutionRuntime
from .robot_contracts import ActionState


@dataclass
class _Slot:
    scope: anyio.CancelScope
    done: anyio.Event = field(default_factory=anyio.Event)
    status: str = "starting"
    evidence: str = "no native action dispatched"
    last_action_id: str | None = None
    pending: bool = False
    action_count: int = 0


class ConversationEnsemble:
    """A single, ephemeral conversation performance owner.

    The application supplies a stable session identity and increasing event
    sequence. No persona, LLM, microphone, provider token or platform service
    is stored here. Only a trusted caller with existing runtime authority may
    launch actions; these contracts themselves grant none.
    """

    def __init__(
        self,
        runtime: ExecutionRuntime,
        packs: Iterable[PerformerBehaviors],
        *,
        caller_id: str = "conversation",
        presence_ttl_s: float = 30,
        stop_timeout_s: float = 5,
    ):
        if not caller_id or not 1 <= presence_ttl_s <= 3600 or not 0.1 <= stop_timeout_s <= 60:
            raise ValueError("invalid conversation coordinator settings")
        # Deep JSON revalidation catches mutated nested cue dictionaries and
        # never trusts a caller-supplied revision without checking its bytes.
        items = tuple(
            PerformerBehaviors.model_validate_json(p.model_dump_json())
            for p in packs
        )
        if not items or len({p.performer_id for p in items}) != len(items):
            raise ValueError("one or more unique performer bindings are required")
        self.runtime = runtime
        self.packs = {p.performer_id: p for p in items}
        self.caller_id = caller_id
        self.presence_ttl_s = presence_ttl_s
        self.stop_timeout_s = stop_timeout_s
        self._lock = anyio.Lock()
        self._tasks = None
        self._slots: dict[str, _Slot] = {}
        self._blocked: set[str] = set()
        self._session: str | None = None
        self._state: Presence = "disconnected"
        self._sequence = -1
        self._event_id: str | None = None
        self._last_seen: float | None = None
        self._expired = False
        self._gesture_ids: set[str] = set()
        self._gesture_order: deque[str] = deque()

    @asynccontextmanager
    async def run(self):
        """Use inside the *same* ExecutionRuntime.run() lifecycle as MCP actions."""
        if self._tasks is not None:
            raise RuntimeError("conversation ensemble already started")
        async with anyio.create_task_group() as tasks:
            self._tasks = tasks
            tasks.start_soon(self._watchdog)
            try:
                yield self
            finally:
                # Shield teardown so the parent cancellation still requests a
                # bounded SDK STOP for an in-flight action.
                with anyio.CancelScope(shield=True):
                    async with self._lock:
                        await self._stop_slots(tuple(self._slots))
                self._tasks = None
                tasks.cancel_scope.cancel()

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": "forge.behavior.v1",
            "session_id": self._session,
            "state": self._state,
            "sequence": self._sequence,
            "stale": self._expired,
            "timing": "approximate_or_unknown",
            "performers": {
                name: {
                    "status": "blocked" if name in self._blocked else (
                        self._slots[name].status if name in self._slots else "idle"
                    ),
                    "evidence": self._slots[name].evidence if name in self._slots else "no action",
                    "action_id": self._slots[name].last_action_id if name in self._slots else None,
                }
                for name in self.packs
            },
        }

    async def observe(self, event: EnsemblePresence) -> dict[str, object]:
        """Ordered room state. Same-state heartbeat renews TTL without moving."""
        async with self._lock:
            if self._tasks is None:
                raise RuntimeError("ensemble must run inside run()")
            if self._session is not None and event.session_id != self._session:
                return {"outcome": "denied", "reason": "another_session_owns_ensemble", **self.snapshot()}
            if self._session == event.session_id:
                if event.sequence < self._sequence:
                    return {"outcome": "stale", **self.snapshot()}
                if event.sequence == self._sequence:
                    if event.event_id == self._event_id:
                        return {"outcome": "duplicate", **self.snapshot()}
                    return {"outcome": "denied", "reason": "conflicting_sequence", **self.snapshot()}
            elif event.state == "disconnected":
                return {"outcome": "no_session", **self.snapshot()}
            previous = self._state
            if self._session is None:
                self._session = event.session_id
            self._sequence = event.sequence
            self._event_id = event.event_id
            self._last_seen = anyio.current_time()
            self._expired = False
            self._state = event.state
            if event.state != previous or event.interrupted:
                await self._stop_slots(tuple(self._slots))
                if event.state != "disconnected":
                    for name in self.packs:
                        self._spawn(name, event.state, f"{event.session_id}-{event.sequence}")
            if event.state == "disconnected":
                self._session = None
            return {"outcome": "accepted", **self.snapshot()}

    async def express(
        self,
        expression: str,
        *,
        action_id: str,
        performer_ids: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """One-shot named cue; preempts ambient loops and then resumes presence.

        Explicit requests are not LLM tool grants. The caller must be authorized
        separately at MCP/Runtime/SDK layers.
        """
        if not expression or not action_id or len(action_id) > 128:
            raise ValueError("expression and bounded action ID required")
        async with self._lock:
            if self._tasks is None or self._session is None or self._state == "disconnected":
                return {"outcome": "denied", "reason": "no_active_conversation", **self.snapshot()}
            if action_id in self._gesture_ids:
                return {"outcome": "duplicate", **self.snapshot()}
            names = tuple(self.packs) if performer_ids is None else performer_ids
            if not names or len(names) != len(set(names)) or any(n not in self.packs for n in names):
                return {"outcome": "denied", "reason": "unknown_or_duplicate_performer", **self.snapshot()}
            if expression in {"idle", "listening", "speaking", "disconnected"}:
                return {"outcome": "denied", "reason": "reserved_presence_state", **self.snapshot()}
            if any(n in self._blocked for n in names):
                return {"outcome": "denied", "reason": "performer_quarantined", **self.snapshot()}
            if any(expression not in self.packs[n].behaviors for n in names):
                return {"outcome": "unsupported", "reason": "unmapped_expression", **self.snapshot()}
            if any(self.packs[n].behaviors[expression].mode != "once" for n in names):
                return {"outcome": "denied", "reason": "expression_must_be_finite", **self.snapshot()}
            await self._stop_slots(names)
            if any(n in self._blocked for n in names):
                return {"outcome": "denied", "reason": "unconfirmed_prior_stop", **self.snapshot()}
            self._gesture_ids.add(action_id)
            self._gesture_order.append(action_id)
            if len(self._gesture_order) > 256:
                self._gesture_ids.discard(self._gesture_order.popleft())
            for name in names:
                self._spawn(name, expression, action_id, resume=True)
            return {"outcome": "accepted", **self.snapshot()}

    def _spawn(self, name: str, behavior: str, action_id: str, *, resume: bool = False):
        if name in self._blocked or self._session is None:
            return
        pattern = self.packs[name].behaviors.get(behavior)
        if pattern is None:
            return  # Missing device behavior is unsupported, never substituted.
        scope = anyio.CancelScope()
        slot = _Slot(scope=scope)
        self._slots[name] = slot
        self._tasks.start_soon(self._play, name, behavior, pattern, slot, action_id, resume)

    async def _stop_slots(self, names: tuple[str, ...]):
        active = [(n, self._slots.pop(n)) for n in names if n in self._slots]
        for _, slot in active:
            slot.scope.cancel()
        for name, slot in active:
            with anyio.move_on_after(self.stop_timeout_s) as deadline:
                await slot.done.wait()
            if deadline.cancel_called or slot.status == "blocked":
                self._blocked.add(name)

    async def _play(
        self, name: str, behavior: str, pattern: BehaviorPattern,
        slot: _Slot, action_id: str, resume: bool
    ):
        with slot.scope:
            try:
                await self._execute_pattern(name, behavior, pattern, slot, action_id)
                # An expression returns to the *latest* conversational presence
                # only if it was not interrupted or blocked.
                if resume and name not in self._blocked and not slot.scope.cancel_called:
                    background = self.packs[name].behaviors.get(self._state)
                    if background and self._session:
                        await self._execute_pattern(
                            name, self._state, background, slot, f"{action_id}-resume"
                        )
            except anyio.get_cancelled_exc_class():
                slot.status = "cancelling"
            except ExecutionError as exc:
                slot.status = "unavailable" if exc.code in {
                    "performer_busy", "ownership_busy", "permission_denied",
                    "resource_busy", "not_authorized",
                } else "blocked"
                slot.evidence = exc.code
            except Exception:
                slot.status = "blocked"
                slot.evidence = "behavior_execution_unknown"
            finally:
                if slot.pending:
                    # Cancellation of a host coroutine is not physical STOP.
                    # This call uses the canonical runtime/provider STOP path.
                    with anyio.CancelScope(shield=True):
                        try:
                            with anyio.fail_after(self.stop_timeout_s):
                                result = await self.runtime.stop(name)
                            if not result.confirmed:
                                slot.status = "blocked"
                                slot.evidence = "device_stop_unconfirmed"
                        except Exception:
                            slot.status = "blocked"
                            slot.evidence = "device_stop_unknown"
                if slot.status == "blocked":
                    self._blocked.add(name)
                slot.done.set()

    async def _execute_pattern(
        self, name: str, behavior: str, pattern: BehaviorPattern,
        slot: _Slot, action_id: str
    ):
        start = anyio.current_time()
        cycles = pattern.max_cycles if pattern.mode == "loop" else 1
        for cycle in range(cycles):
            if anyio.current_time() - start >= pattern.max_duration_s:
                break
            for index, cue in enumerate(pattern.cues):
                offset = cycle * (pattern.period_s or 0.0) + cue.at_s
                target = start + offset
                if offset >= pattern.max_duration_s:
                    return
                now = anyio.current_time()
                if pattern.mode == "loop" and now - target > (pattern.period_s or 0.0):
                    # Slow hardware must not trigger high-rate catch-up bursts.
                    continue
                await anyio.sleep(max(0, target - now))
                if not self._session or self._state == "disconnected":
                    return
                request = ActionRequest(
                    id=f"{action_id}:{name}:{cycle}:{index}",
                    performer_id=name, caller_id=self.caller_id,
                    session_id=self._session, operation=cue.operation,
                    parameters=dict(cue.parameters),
                    cue_id=f"{behavior}:{cycle}:{index}",
                    asset_id=cue.asset_id, asset_revision=cue.asset_revision,
                )
                slot.last_action_id = request.id
                # Runtime.submit validates authority and resource ownership BEFORE
                # it admits the request. Never send device STOP when that admission
                # was denied: another controller may own the robot.
                await self.runtime.submit(request)
                slot.pending = True
                record = await self.runtime.wait(request.id)
                slot.pending = record.state == ActionState.UNKNOWN
                slot.action_count += 1
                if record.state != ActionState.COMPLETED:
                    slot.status = "blocked" if record.state == ActionState.UNKNOWN else "unavailable"
                    slot.evidence = f"action_{record.state.value}"
                    if slot.status == "blocked":
                        self._blocked.add(name)
                    return
                slot.status = "active"
                slot.evidence = "runtime_confirmed_action_completion"
        slot.status = "completed"
        slot.evidence = "behavior_pattern_finished"

    async def _watchdog(self):
        while True:
            await anyio.sleep(min(1, self.presence_ttl_s / 2))
            async with self._lock:
                if (self._session is not None and self._last_seen is not None
                        and anyio.current_time() - self._last_seen >= self.presence_ttl_s):
                    await self._stop_slots(tuple(self._slots))
                    self._session = None
                    self._state = "disconnected"
                    self._expired = True
