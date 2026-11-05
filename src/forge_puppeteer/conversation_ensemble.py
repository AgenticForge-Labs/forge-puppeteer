"""One session-owned semantic ensemble over the existing ExecutionRuntime.

This module cannot open hardware, play audio or authorize motion. Every cue
uses the same runtime/provider admission and action journal as ordinary MCP
actions. A denied/busy/uncertain device cannot be silently retried or replaced.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio

from .behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    EnsemblePresence,
    PerformerBehaviors,
    Presence,
)
from .conversation_native import _head

logger = logging.getLogger(__name__)
from .execution import ActionRequest, ExecutionError, ExecutionRuntime
from .robot_contracts import ActionState


@dataclass
class _Slot:
    scope: anyio.CancelScope
    behavior: str = ""
    expression_active: bool = False
    done: anyio.Event = field(default_factory=anyio.Event)
    status: str = "starting"
    evidence: str = "no native action dispatched"
    last_action_id: str | None = None
    pending: bool = False
    action_done: anyio.Event = field(default_factory=anyio.Event)
    action_count: int = 0
    # Set at the start of a transition stop, before the scope is cancelled,
    # so a speech slot can finish between targets instead of mid-dispatch.
    cancel_requested: bool = False


def _tenths(value: float) -> int:
    """Round a tenths-of-a-degree target; the SDK still validates bounds."""
    rounded = round(value)
    return rounded if isinstance(rounded, int) else int(rounded)


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
        startup_settle_s: float = 0.0,
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
        self._speech_channels: dict[
            str, tuple[object, float, list | None, object | None]
        ] = {}
        self._speech_intervals: dict[str, float] = {}
        # One device connection per performer, but the execution runtime is
        # shared. Targets that would run while another performer's target is
        # still executing contend for it, so dispatch is serialized.
        self._dispatch_gate = anyio.Lock()
        self._dispatch_overruns = 0
        # Bounded dispatch rate for audio-driven targets: slow hardware must
        # never receive a burst of catch-up commands.
        self._speech_interval_s: float = 0.25
        self._blocked: set[str] = set()
        self._owned: set[str] = set()
        self._unavailable: dict[str, str] = {}
        self._session: str | None = None
        self._state: Presence = "disconnected"
        self._sequence = -1
        self._event_id: str | None = None
        self._last_seen: float | None = None
        self._expired = False
        self._gesture_ids: set[str] = set()
        self._gesture_order: deque[str] = deque()
        self._startup_pending = {
            performer_id for performer_id, pack in self.packs.items()
            if pack.startup is not None
        }
        # The first spoken turn waits until startup motion has finished, so an
        # opening greeting never talks over a wake-up or a neutral rise.
        self._startup_settle_s = float(startup_settle_s)
        self._startup_done = anyio.Event()
        self._startup_deadline: float | None = None

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
                        await self._stop_slots(tuple(self._slots), graceful=False)
                        self._release_owners()
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
                        self._slots[name].status if name in self._slots else (
                            "unavailable" if name in self._unavailable else "idle"
                        )
                    ),
                    "evidence": self._slots[name].evidence if name in self._slots else (
                        self._unavailable.get(name, "no action")
                    ),
                    "action_id": self._slots[name].last_action_id if name in self._slots else None,
                    # Immutable, operator-authored catalog — never inferred
                    # from device type, personality, or arbitrary MCP input.
                    "behavior_revision": self.packs[name].revision,
                    "expressions": sorted(
                        behavior for behavior, pattern in self.packs[name].behaviors.items()
                        if pattern.mode == "once"
                        and behavior not in {"idle", "listening", "speaking", "disconnected"}
                    ),
                    # Recorded expressions replay an exact pinned asset; authored
                    # ones are procedural gestures. Callers may group or filter
                    # by this without inferring intent from device type.
                    "expression_kinds": {
                        behavior: (
                            "recorded"
                            if pattern.source == "recorded"
                            else "authored"
                        )
                        for behavior, pattern in sorted(self.packs[name].behaviors.items())
                        if pattern.mode == "once"
                        and behavior not in {"idle", "listening", "speaking", "disconnected"}
                    },
                    "presence_behaviors": sorted(
                        behavior for behavior in ("idle", "listening", "speaking")
                        if behavior in self.packs[name].behaviors
                    ),
                    "startup_behavior": self.packs[name].startup is not None,
                    "startup_pending": name in self._startup_pending,
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
            transition = event.state != previous or event.interrupted
            if transition:
                # A normal change in voice state (for example, speaking while
                # acknowledging a requested gesture) must not preempt that
                # finite gesture. Real interruption/disconnect remains urgent.
                preserve_expressions = event.state != "disconnected" and not event.interrupted
                await self._stop_slots(
                    tuple(name for name, slot in self._slots.items()
                          if not (preserve_expressions and slot.expression_active)),
                    graceful=event.state != "disconnected" and not event.interrupted,
                )
            if event.state == "disconnected":
                self._release_owners()
                self._session = None
            else:
                # Exclusive runtime ownership prevents an automatic device-wide
                # STOP from interrupting an unrelated manipulation controller.
                # A busy performer is skipped, not stopped or auto-retired.
                for name in self.packs:
                    newly_owned = self._acquire_owner(name)
                    if name in self._owned and (transition or newly_owned) and name not in self._slots:
                        if name in self._startup_pending:
                            self._startup_pending.discard(name)
                            # Startup motion owns the robot until it settles;
                            # the first spoken turn must not talk over it.
                            self._mark_startup_pending()
                            self._spawn(
                                name, "startup", f"{event.session_id}-{event.sequence}",
                                resume=True,
                            )
                        elif event.state == "speaking" and not self.startup_complete():
                            # Defer the opening turn rather than interrupting
                            # the rise to neutral.
                            self._spawn_deferred_speaking(
                                name, f"{event.session_id}-{event.sequence}",
                            )
                        else:
                            self._spawn(name, event.state, f"{event.session_id}-{event.sequence}")
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
            if any(n not in self._owned for n in names):
                return {"outcome": "denied", "reason": "performer_not_owned", **self.snapshot()}
            if any(expression not in self.packs[n].behaviors for n in names):
                return {"outcome": "unsupported", "reason": "unmapped_expression", **self.snapshot()}
            if any(self.packs[n].behaviors[expression].mode != "once" for n in names):
                return {"outcome": "denied", "reason": "expression_must_be_finite", **self.snapshot()}
            await self._stop_slots(names, graceful=True)
            if any(n in self._blocked for n in names):
                return {"outcome": "denied", "reason": "unconfirmed_prior_stop", **self.snapshot()}
            self._gesture_ids.add(action_id)
            self._gesture_order.append(action_id)
            if len(self._gesture_order) > 256:
                self._gesture_ids.discard(self._gesture_order.popleft())
            for name in names:
                self._spawn(name, expression, action_id, resume=True)
            return {"outcome": "accepted", **self.snapshot()}

    async def present(
        self,
        performer_id: str,
        preset_id: str,
        *,
        action_id: str,
        cue: BehaviorCue,
    ) -> dict[str, object]:
        """Run one trusted, preauthored visual preset through the normal runtime.

        The caller (not an MCP argument) supplies a source-controlled native
        cue. A preflight occurs BEFORE interrupting the ambient behavior, so
        missing grants/firmware never cause unnecessary STOP or new motion.
        Only the existing action journal and owner coordinate the execution.
        """
        if (
            not performer_id or not preset_id or not action_id
            or len(action_id) > 128 or len(preset_id) > 64
        ):
            raise ValueError("bounded presentation identity and action ID required")
        pattern = BehaviorPattern(
            source="procedural", mode="once", cues=(cue,),
        )
        session_id = self._session
        if self._tasks is None or session_id is None or performer_id not in self.packs:
            return {"outcome": "denied", "reason": "inactive_or_unknown_performer", **self.snapshot()}
        if performer_id in self._blocked or performer_id not in self._owned:
            return {"outcome": "denied", "reason": "performer_unavailable", **self.snapshot()}
        # Validation may make a bounded SDK read. It must not hold the ensemble
        # presence lock, which handles urgent disconnect/interruption events.
        preflight = ActionRequest(
            id=f"{action_id}:{performer_id}:0:0",
            performer_id=performer_id, caller_id=self.caller_id,
            session_id=session_id, operation=cue.operation,
            parameters=dict(cue.parameters), cue_id=f"presentation:{preset_id}:0:0",
            asset_id=cue.asset_id, asset_revision=cue.asset_revision,
        )
        try:
            await self.runtime._provider(performer_id).validate(preflight)
        except (ValueError, TypeError, RuntimeError, PermissionError):
            return {"outcome": "unsupported", "reason": "presentation_preflight_denied", **self.snapshot()}
        async with self._lock:
            if (
                self._tasks is None or self._session != session_id
                or self._state == "disconnected"
            ):
                return {"outcome": "denied", "reason": "session_changed", **self.snapshot()}
            if action_id in self._gesture_ids:
                return {"outcome": "duplicate", **self.snapshot()}
            if performer_id in self._blocked or performer_id not in self._owned:
                return {"outcome": "denied", "reason": "performer_unavailable", **self.snapshot()}
            await self._stop_slots((performer_id,), graceful=True)
            if performer_id in self._blocked:
                return {"outcome": "denied", "reason": "unconfirmed_prior_stop", **self.snapshot()}
            self._gesture_ids.add(action_id)
            self._gesture_order.append(action_id)
            if len(self._gesture_order) > 256:
                self._gesture_ids.discard(self._gesture_order.popleft())
            slot = _Slot(
                scope=anyio.CancelScope(),
                behavior=f"presentation:{preset_id}",
                expression_active=True,
            )
            self._slots[performer_id] = slot
            self._tasks.start_soon(
                self._play, performer_id, f"presentation:{preset_id}",
                pattern, slot, action_id, True,
            )
            return {"outcome": "accepted", **self.snapshot()}

    def _spawn_deferred_speaking(
        self, name: str, action_id: str, *, resume: bool = False,
    ) -> None:
        """Start the speaking slot only after startup motion has settled."""
        if self._tasks is None:
            return
        scope = anyio.CancelScope()
        slot = _Slot(scope=scope, behavior="speaking", expression_active=resume)
        self._slots[name] = slot

        async def wait_then_play() -> None:
            # The wait itself must be interruptible: an operator interrupt
            # during the settle window has to cancel the deferred greeting.
            # The scope is entered for the wait only, because both callees
            # open their own 'with' block on it.
            with scope:
                while not self.startup_complete():
                    if self._session is None or self._state == "disconnected":
                        slot.done.set()
                        return
                    await anyio.sleep(0.1)
            if scope.cancel_called:
                slot.done.set()
                return
            pattern = self.packs[name].behaviors.get("speaking")
            if pattern is None:
                slot.done.set()
                return
            if name in self._speech_channels:
                await self._play_speech_driven(name, pattern, slot, action_id)
            else:
                await self._play(
                    name, "speaking", pattern, slot, action_id, resume,
                )

        self._tasks.start_soon(wait_then_play)

    def startup_complete(self) -> bool:
        """True once startup motion settled, or when no performer needs it.

        ``_startup_deadline`` is unset until a startup action is actually
        spawned. A fresh room has no startup work yet, so treat that as
        not-yet-ready while any startup is still pending: this keeps the very
        first presence event (which carries the greeting) from racing the
        startup action that is spawned in the same call.
        """
        if self._startup_deadline is None:
            return not self._startup_pending
        return anyio.current_time() >= self._startup_deadline

    def _mark_startup_pending(self) -> None:
        self._startup_deadline = anyio.current_time() + self._startup_settle_s

    def _acquire_owner(self, name: str) -> bool:
        if name in self._owned or name in self._blocked or self._session is None:
            return False
        try:
            self.runtime.acquire_control(name, self.caller_id, self._session)
        except ExecutionError:
            self._unavailable[name] = "runtime_owner_or_resources_busy"
            return False
        self._unavailable.pop(name, None)
        self._owned.add(name)
        return True

    def _release_owners(self) -> None:
        if self._session is None:
            return
        for name in tuple(self._owned):
            if name in self._blocked:
                continue  # Preserve authority/quarantine until deliberate reconciliation.
            try:
                self.runtime.release_control(name, self.caller_id, self._session)
            except ExecutionError:
                self._blocked.add(name)
                continue
            self._owned.remove(name)

    def _spawn(self, name: str, behavior: str, action_id: str, *, resume: bool = False):
        if name in self._blocked or name not in self._owned or self._session is None:
            return
        pattern = (
            self.packs[name].startup if behavior == "startup"
            else self.packs[name].behaviors.get(behavior)
        )
        if pattern is None:
            return  # Missing device behavior is unsupported, never substituted.
        if behavior == "speaking" and name in self._speech_channels:
            if not self.startup_complete():
                # Startup motion owns the robot; defer rather than talk over it.
                self._spawn_deferred_speaking(name, action_id, resume=resume)
                return
            scope = anyio.CancelScope()
            slot = _Slot(scope=scope, behavior=behavior, expression_active=resume)
            self._slots[name] = slot
            self._tasks.start_soon(
                self._play_speech_driven, name, pattern, slot, action_id,
            )
            return
        scope = anyio.CancelScope()
        slot = _Slot(scope=scope, behavior=behavior, expression_active=resume)
        self._slots[name] = slot
        self._tasks.start_soon(self._play, name, behavior, pattern, slot, action_id, resume)

    def bind_speech_channel(
        self, performer_id: str, channel, *, amplitude: float,
        neutral_head: Sequence[Sequence[float]] | None = None,
        warp: Callable[[dict[str, float], float], BehaviorCue] | None = None,
        interval_s: float = 0.25,
    ) -> None:
        """Attach an optional audio-driven channel for one verbal performer.

        The channel is consulted only while a *speaking* presence slot is
        active, so listening/idle and every finite gesture stay exactly as
        authored. Values are unitless and must already be clamped by the
        caller's operator limits.
        """
        if performer_id not in self.packs:
            raise ValueError("speech channel requires a configured performer")
        if neutral_head is not None and (
            len(neutral_head) != 4 or any(len(row) != 4 for row in neutral_head)
        ):
            raise ValueError("speech channel neutral head must be a 4x4 transform")
        self._speech_channels[performer_id] = (
            channel, float(amplitude),
            [[float(v) for v in row] for row in neutral_head] if neutral_head else None,
            warp,
        )
        self._speech_intervals[performer_id] = float(interval_s)

    async def _stop_slots(self, names: tuple[str, ...], *, graceful: bool = False):
        active = [(n, self._slots.pop(n)) for n in names if n in self._slots]
        # On ordinary semantic transitions, an opt-in SDK can complete its
        # already-admitted finite native segment before we cancel the *loop*.
        # This must be time-bounded: a stuck SDK action still takes the
        # authoritative STOP/HOLD path. Explicit interrupt, disconnect,
        # heartbeat loss and process shutdown never wait for this grace.
        if graceful:
            for name, slot in active:
                if not slot.pending:
                    continue
                provider = self.runtime._provider(name)
                grace = getattr(provider, "finish_before_interrupt_s", 0.0)
                if isinstance(grace, (int, float)) and 0 < grace <= 5.0:
                    with anyio.move_on_after(grace):
                        await slot.action_done.wait()
        else:
            # A provider whose SDK cannot cancel a finite segment (no stop
            # acknowledgment exists) may opt in to a short bounded finish on
            # interruption. Cancelling such a segment always yields an
            # unconfirmed stop, which quarantines the performer for the rest
            # of the room. Providers that can stop keep the urgent path.
            for name, slot in active:
                if not slot.pending:
                    continue
                provider = self.runtime._provider(name)
                grace = getattr(provider, "finish_on_interrupt_s", 0.0)
                if isinstance(grace, (int, float)) and 0 < grace <= 5.0:
                    with anyio.move_on_after(grace):
                        await slot.action_done.wait()
        for _, slot in active:
            slot.cancel_requested = True
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
                slot.expression_active = False
                # An expression returns to the *latest* conversational presence
                # only if it was not interrupted or blocked.
                if resume and name not in self._blocked and not slot.scope.cancel_called:
                    background = self.packs[name].behaviors.get(self._state)
                    if background and self._session:
                        # A completed startup/gesture can resume a long-lived
                        # presence loop in the same slot. Do not leave the
                        # previous finite action's "completed" marker visible
                        # while the next SDK action is being admitted.
                        slot.status = "starting"
                        slot.evidence = "resuming_presence_behavior"
                        if self._state == "speaking" and not self.startup_complete():
                            # The resume itself may be the startup rise that
                            # just settled; the greeting still waits for the
                            # settle window instead of talking over it.
                            slot.status = "holding"
                            slot.evidence = "deferred_until_startup_settles"
                            resume_id = f"{action_id}-resume"
                            await self._retire_slot(name, slot)
                            self._spawn_deferred_speaking(
                                name, resume_id, resume=True,
                            )
                            return
                        await self._execute_pattern(
                            name, self._state, background, slot, f"{action_id}-resume"
                        )
            except anyio.get_cancelled_exc_class():
                slot.status = "cancelling"
            except ExecutionError as exc:
                # Exceptions from submit are admission failures, before the
                # provider can receive the action. Keep an existing confirmed
                # unknown STOP inhibited, but do not quarantine the robot for a
                # deterministic pre-dispatch rejection such as bad resources.
                slot.status = "blocked" if (
                    slot.pending or exc.code == "stop_unconfirmed"
                ) else "unavailable"
                slot.evidence = exc.code
            except Exception:  # noqa: BLE001 - unknown SDK outcome must quarantine
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
                        except Exception:  # noqa: BLE001 - STOP failures are uncertain
                            slot.status = "blocked"
                            slot.evidence = "device_stop_unknown"
                if slot.status == "blocked":
                    self._blocked.add(name)
                slot.done.set()

    async def _retire_slot(self, name: str, slot: _Slot) -> None:
        """Vacate a slot the ensemble is handing to a replacement coroutine.

        The caller has already committed to spawning a successor, so this only
        removes the bookkeeping entry; it deliberately does not cancel or STOP
        (the action is complete) and does not mark the performer blocked.
        """
        current = self._slots.get(name)
        if current is slot:
            self._slots.pop(name, None)
        slot.done.set()

    async def _play_speech_driven(
        self, name: str, pattern: BehaviorPattern, slot: _Slot, action_id: str,
    ) -> None:
        """Dispatch bounded targets from the live speech envelope.

        Only the speaking slot uses this. Same runtime, journal and STOP path
        as any other motion; the envelope merely chooses the next target.

        When the envelope is stale the slot goes quiet instead of replaying
        the authored pattern: the authored pattern is a *loop*, and running
        it inside this finite slot would never stop.
        """
        channel, amplitude, _neutral, warp = self._speech_channels[name]
        interval = self._speech_intervals.get(name, self._speech_interval_s)
        with slot.scope:
            index = 0
            started = anyio.current_time()
            try:
                while True:
                    # A transition cancels this scope while we are asleep
                    # between targets. Exit here, before admitting another
                    # action, so the slot is cleanly finished rather than
                    # torn down mid-dispatch (which would look like an
                    # unconfirmed STOP and quarantine the performer).
                    if slot.cancel_requested:
                        slot.status = "completed"
                        slot.evidence = "speech_presence_transition"
                        return
                    values = channel.latest()
                    if values is None:
                        slot.status = "active"
                        slot.evidence = "awaiting_speech_envelope"
                        if index == 0:
                            # Logged once per speaking slot: the channel exists
                            # but no fresh hop has arrived yet.
                            logger.debug(
                                "speech_envelope_awaiting",
                                extra={"performer_id": name, "interval_s": interval},
                            )
                        await anyio.sleep(interval)
                        continue
                    if warp is not None:
                        # Operator-supplied mapping, e.g. the firmware port
                        # which picks its own subtle nod or sway.
                        cue = warp(values, amplitude)
                    else:
                        cue = self._speech_cue(values, amplitude, name)
                    request = ActionRequest(
                        id=f"{action_id}:{name}:0:{index}",
                        performer_id=name, caller_id=self.caller_id,
                        session_id=self._session, operation=cue.operation,
                        parameters=dict(cue.parameters),
                        cue_id=f"speaking:0:{index}",
                        asset_id=cue.asset_id, asset_revision=cue.asset_revision,
                    )
                    slot.last_action_id = request.id
                    # Per-dispatch evidence: distinguishes "waiting for a fresh
                    # envelope" from "envelope accepted and target submitted".
                    # Without this an operator cannot tell a gated channel from
                    # a sleeping robot.
                    logger.debug(
                        "speech_envelope_dispatch",
                        extra={
                            "performer_id": name,
                            "action_id": request.id,
                            "operation": request.operation,
                            "cue_id": request.cue_id,
                            "index": index,
                        },
                    )
                    # Hold the gate across submit *and* execution so another
                    # performer's target cannot queue up behind this one.
                    started = anyio.current_time()
                    async with self._dispatch_gate:
                        await self.runtime.submit(request)
                        slot.pending = True
                        slot.action_done = anyio.Event()
                        record = await self.runtime.wait(request.id)
                    slot.pending = record.state == ActionState.UNKNOWN
                    slot.action_done.set()
                    if record.state != ActionState.COMPLETED:
                        slot.status = (
                            "blocked" if record.state == ActionState.UNKNOWN
                            else "unavailable"
                        )
                        slot.evidence = f"action_{record.state.value}"
                        if slot.status == "blocked":
                            self._blocked.add(name)
                        return
                    slot.status = "active"
                    slot.evidence = "speech_envelope_dispatch"
                    index += 1
                    if anyio.current_time() - started > interval:
                        # Another performer held the gate past this interval.
                        self._dispatch_overruns += 1
                    await anyio.sleep(interval)
            finally:
                if slot.pending:
                    # A cancelled slot must still report the real outcome: the
                    # device call has not returned yet, so end the slot only
                    # after the in-flight target settles. Otherwise the next
                    # performer acquires the shared gate while this device is
                    # still busy.
                    with anyio.CancelScope(shield=True):
                        with anyio.move_on_after(self.stop_timeout_s):
                            await slot.action_done.wait()
                if slot.status not in {"blocked", "unavailable"}:
                    slot.status = "completed"
                    slot.evidence = "behavior_pattern_finished"
                # Without this the transition stop waits for the full timeout
                # and then quarantines a perfectly healthy performer.
                slot.done.set()

    def _speech_cue(
        self, values: dict[str, float], amplitude: float, name: str,
    ) -> BehaviorCue:
        """Reachy mapping: one tapper hop around the operator's neutral pose.

        The tapper already scales by loudness and its own VAD envelope, so the
        channel must not attenuate again: a quiet syllable should still be
        visible. Measured on hardware, a 20 deg command yields ~18.7 deg of
        real yaw, so ``amplitude`` is the operator's full expressive budget.
        """
        levels = {key: max(-1.0, min(1.0, value)) for key, value in values.items()}
        neutral = self._speech_channels[name][2]
        if neutral is None:
            raise ValueError("audio-driven Reachy motion requires a neutral head")
        # Lift the tapper's small per-hop values toward the full budget while
        # keeping the sign and relative shape of the motion.
        def lift(value: float) -> float:
            return math.copysign(min(1.0, abs(value) * 2.5), value)

        pose = _head(
            neutral,
            pitch_rad=lift(levels.get("pitch", 0.0)) * amplitude,
            yaw_rad=lift(levels.get("yaw", 0.0)) * amplitude,
            roll_rad=lift(levels.get("roll", 0.0)) * amplitude * 0.5,
        )
        return BehaviorCue(
            at_s=0.0, operation="head_pose",
            parameters={"pose": pose, "duration_s": 0.25},
        )

    async def _execute_pattern(
        self, name: str, behavior: str, pattern: BehaviorPattern,
        slot: _Slot, action_id: str
    ):
        if pattern.mode == "hold":
            # The preceding transition has already finished or followed the
            # mandatory stop/quarantine path. This is no motor command and
            # provides no independent evidence of physical settling.
            slot.status = "holding"
            slot.evidence = "no_motion_command_dispatched"
            return
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
                # Every performer shares one serialized runtime, so a target
                # is held across submit *and* execution. Without this two
                # robots' patterns overlap and contend for the same device.
                async with self._dispatch_gate:
                    await self.runtime.submit(request)
                    slot.pending = True
                    slot.action_done = anyio.Event()
                    record = await self.runtime.wait(request.id)
                slot.pending = record.state == ActionState.UNKNOWN
                slot.action_done.set()
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
                    await self._stop_slots(tuple(self._slots), graceful=False)
                    self._release_owners()
                    self._session = None
                    self._state = "disconnected"
                    self._expired = True
