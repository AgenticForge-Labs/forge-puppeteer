"""Opt-in action execution on the existing stage's clock, events and locks.

No hardware is opened here. Explicit providers validate typed operations and live
policy twice, and must supply evidence rather than queue acknowledgements. IDs
are retained for the lifetime of this object or in an optional SQLite journal.
Recovery treats unfinished work as unknown and quarantines its performer until a
confirmed stop.
Legacy TimelineDirector callers keep their existing behavior; ownership applies
only to callers routed through this runtime. Resource names matching existing
ActionResource values share the stage's locks with legacy cues while executing.
Legacy direct execution does not consult ownership or unknown-outcome quarantine;
all writers must use this runtime before relying on those guarantees.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import anyio
from pydantic import Field, JsonValue

from .events import EventType, PerformanceEvent
from .robot_contracts import ActionRecord, ActionState, ContractModel
from .runtime import PerformanceStage


class ExecutionError(RuntimeError):
    """Stable, bounded runtime error; backend exception text is not exposed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ActionRequest(ContractModel):
    id: str = Field(min_length=1)
    performer_id: str = Field(min_length=1)
    caller_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    cue_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    asset_revision: str | None = None


class AmbientBehaviorControl(ContractModel):
    """Explicit, bounded local blink/idle behavior settings."""

    enabled: bool = False
    interval_seconds: float = Field(default=8.0, ge=2.0, le=60.0)
    max_events: int = Field(default=20, ge=1, le=100)
    max_duration_seconds: float = Field(default=300.0, ge=1.0, le=1800.0)


@dataclass(frozen=True)
class Completion:
    """An adapter-confirmed completion, never merely an enqueue acknowledgement."""

    evidence: str


@dataclass(frozen=True)
class StopResult:
    confirmed: bool
    evidence: str


class ExecutionProvider(Protocol):
    """Explicit dispatch only; validate must reject unknown operations/parameters.

    Validation checks current readiness, authority, pinned asset identity and
    adapter limits without submitting work. Claims include broader SDK serialization
    constraints. execute must remain cancellable and may only return Completion
    after confirming completion; exceptions/timeouts are conservatively unknown.
    stop is independent of execute and confirms all work on this performer stopped.
    """

    async def validate(self, request: ActionRequest) -> None: ...
    def resources_for(self, request: ActionRequest) -> tuple[str, ...]: ...
    async def execute(self, request: ActionRequest) -> Completion: ...
    async def stop(self) -> StopResult: ...


@dataclass
class _Entry:
    request: ActionRequest
    fingerprint: str
    record: ActionRecord
    reserved: bool
    done: anyio.Event
    scope: anyio.CancelScope | None = None


_TERMINAL = {
    ActionState.COMPLETED,
    ActionState.FAILED,
    ActionState.CANCELLED,
    ActionState.UNKNOWN,
}


class ExecutionRuntime:
    """In-memory registry with explicit reject-on-conflict scheduling.

    Unknown execution outcomes quarantine claims until a confirmed independent
    stop. This runtime must be the sole ownership boundary for its providers.
    Shutdown cancels software tasks and records unknown; it does not claim safe
    hardware shutdown or automatically issue physical operations.
    """

    def __init__(
        self,
        stage: PerformanceStage,
        *,
        execution_timeout: float = 60,
        stop_timeout: float = 5,
        journal_path: str | None = None,
        ownership_path: str | None = None,
    ):
        import math
        import os

        if any(
            not math.isfinite(v) or v <= 0 for v in (execution_timeout, stop_timeout)
        ):
            raise ValueError("timeouts must be finite and positive")
        self.stage = stage
        self.execution_timeout = execution_timeout
        self.stop_timeout = stop_timeout
        self._providers: dict[str, ExecutionProvider] = {}
        self._entries: dict[str, _Entry] = {}
        self._owners: dict[str, tuple[str, str]] = {}
        self._owner_process = uuid.uuid4().hex
        self._generation: dict[str, int] = {}
        self._stopping: set[str] = set()
        self._inhibited: set[str] = set()
        self._ambient_scopes: dict[str, anyio.CancelScope] = {}
        self._ambient_serial: dict[str, int] = {}
        self._tasks = None
        self.event_errors: list[str] = []
        # An explicit constructor value wins; deployments may also configure
        # every packaged server process through its trusted local environment.
        journal_path = journal_path or os.environ.get(
            "FORGE_PUPPETEER_ACTION_JOURNAL"
        )
        self._journal = sqlite3.connect(journal_path) if journal_path else None
        if self._journal is not None:
            self._journal.execute(
                "CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, request TEXT NOT NULL, fingerprint TEXT NOT NULL, record TEXT NOT NULL, reserved INTEGER NOT NULL)"
            )
            self._journal.commit()
            for action_id, request_json, fingerprint, record_json, reserved in self._journal.execute(
                "SELECT id, request, fingerprint, record, reserved FROM actions"
            ):
                request = ActionRequest.model_validate_json(request_json)
                record = ActionRecord.model_validate_json(record_json)
                # A process restart cannot distinguish an interrupted device action
                # from a lost completion acknowledgement. Preserve its claim and
                # require an independent confirmed stop before reuse.
                if record.state not in _TERMINAL:
                    record = record.model_copy(
                        update={"state": ActionState.UNKNOWN, "error": "execution_outcome_unknown"}
                    )
                    reserved = 1
                if record.state == ActionState.UNKNOWN and reserved:
                    self._inhibited.add(request.performer_id)
                entry = _Entry(request, fingerprint, record, bool(reserved), anyio.Event())
                if record.state in _TERMINAL:
                    entry.done.set()
                self._entries[action_id] = entry
                self._persist(entry)
        ownership_path = ownership_path or os.environ.get(
            "FORGE_PUPPETEER_OWNERSHIP_DB"
        )
        self._ownership_db = sqlite3.connect(
            ownership_path, timeout=5, isolation_level=None
        ) if ownership_path else None
        if self._ownership_db is not None:
            self._ownership_db.execute(
                "CREATE TABLE IF NOT EXISTS performer_owners (performer_id TEXT PRIMARY KEY, caller_id TEXT NOT NULL, session_id TEXT NOT NULL, process_id TEXT NOT NULL)"
            )
            # A durable admission row prevents a second server from dispatching
            # while this process has work in flight. Unknown/crashed work stays
            # here until an operator reconciles the physical device.
            self._ownership_db.execute(
                "CREATE TABLE IF NOT EXISTS performer_activity (performer_id TEXT NOT NULL, process_id TEXT NOT NULL, action_id TEXT NOT NULL, caller_id TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY (performer_id, process_id, action_id))"
            )

    def _persist(self, entry: _Entry) -> None:
        if self._journal is None:
            return
        self._journal.execute(
            "INSERT INTO actions(id, request, fingerprint, record, reserved) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET request=excluded.request, fingerprint=excluded.fingerprint, record=excluded.record, reserved=excluded.reserved",
            (
                entry.request.id,
                entry.request.model_dump_json(),
                entry.fingerprint,
                entry.record.model_dump_json(),
                int(entry.reserved),
            ),
        )
        self._journal.commit()

    def close(self) -> None:
        """Close the optional journal after runtime shutdown."""
        if self._journal is not None:
            self._journal.close()
            self._journal = None
        if self._ownership_db is not None:
            self._ownership_db.close()
            self._ownership_db = None

    def register(self, performer_id: str, provider: ExecutionProvider) -> None:
        if not performer_id or performer_id in self._providers:
            raise ValueError("performer ID must be nonempty and unique")
        if any(existing is provider for existing in self._providers.values()):
            raise ValueError("provider already registered")
        self._providers[performer_id] = provider
        self._generation[performer_id] = 0

    def _provider(self, performer_id):
        try:
            return self._providers[performer_id]
        except KeyError:
            raise ExecutionError("unknown_performer") from None

    def acquire_control(
        self, performer_id: str, caller_id: str, session_id: str
    ) -> None:
        self._provider(performer_id)
        if not caller_id or not session_id:
            raise ValueError("caller and session must be nonempty")
        owner = (caller_id, session_id)
        if self._ownership_db is not None:
            db = self._ownership_db
            try:
                db.execute("BEGIN IMMEDIATE")
                activity = db.execute(
                    "SELECT 1 FROM performer_activity WHERE performer_id=? LIMIT 1",
                    (performer_id,),
                ).fetchone()
                if activity is not None:
                    db.execute("ROLLBACK")
                    raise ExecutionError("performer_busy")
                row = db.execute(
                    "SELECT caller_id, session_id, process_id FROM performer_owners WHERE performer_id=?",
                    (performer_id,),
                ).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO performer_owners VALUES (?, ?, ?, ?)",
                        (performer_id, caller_id, session_id, self._owner_process),
                    )
                elif row != (caller_id, session_id, self._owner_process):
                    db.execute("ROLLBACK")
                    raise ExecutionError("ownership_busy")
                db.execute("COMMIT")
            except ExecutionError:
                raise
            except sqlite3.Error:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise ExecutionError("ownership_unavailable") from None
        if self._owners.get(performer_id, owner) != owner:
            raise ExecutionError("ownership_busy")
        if any(
            e.request.performer_id == performer_id
            and e.reserved
            and (e.request.caller_id, e.request.session_id) != owner
            for e in self._entries.values()
        ):
            raise ExecutionError("performer_busy")
        self._owners[performer_id] = owner
        scope = self._ambient_scopes.pop(performer_id, None)
        if scope is not None:
            scope.cancel()

    def release_control(
        self, performer_id: str, caller_id: str, session_id: str
    ) -> None:
        if (
            self._owners.get(performer_id) != (caller_id, session_id)
            and not self._shared_is_owner(performer_id, caller_id, session_id)
        ):
            raise ExecutionError("not_owner")
        if any(
            e.request.performer_id == performer_id
            and (e.reserved or e.record.state not in _TERMINAL)
            for e in self._entries.values()
        ):
            raise ExecutionError("performer_busy")
        if self._ownership_db is not None:
            try:
                db = self._ownership_db
                db.execute("BEGIN IMMEDIATE")
                active = db.execute(
                    "SELECT 1 FROM performer_activity WHERE performer_id=? LIMIT 1",
                    (performer_id,),
                ).fetchone()
                if active is not None:
                    db.execute("ROLLBACK")
                    raise ExecutionError("performer_busy")
                cursor = db.execute(
                    "DELETE FROM performer_owners WHERE performer_id=? AND caller_id=? AND session_id=? AND process_id=?",
                    (performer_id, caller_id, session_id, self._owner_process),
                )
                if cursor.rowcount != 1:
                    db.execute("ROLLBACK")
                    raise ExecutionError("not_owner")
                db.execute("COMMIT")
            except ExecutionError:
                raise
            except sqlite3.Error:
                if self._ownership_db.in_transaction:
                    self._ownership_db.execute("ROLLBACK")
                raise ExecutionError("ownership_unavailable") from None
        self._owners.pop(performer_id, None)

    def _shared_is_owner(self, performer_id, caller_id, session_id):
        if self._ownership_db is None:
            return False
        try:
            row = self._ownership_db.execute(
                "SELECT caller_id, session_id, process_id FROM performer_owners WHERE performer_id=?",
                (performer_id,),
            ).fetchone()
        except sqlite3.Error:
            raise ExecutionError("ownership_unavailable") from None
        return row == (caller_id, session_id, self._owner_process)

    def _check_owner(self, request):
        owner = self._owners.get(request.performer_id)
        if owner is not None and owner != (request.caller_id, request.session_id):
            raise ExecutionError("ownership_busy")
        if self._ownership_db is not None:
            try:
                row = self._ownership_db.execute(
                    "SELECT caller_id, session_id, process_id FROM performer_owners WHERE performer_id=?",
                    (request.performer_id,),
                ).fetchone()
            except sqlite3.Error:
                raise ExecutionError("ownership_unavailable") from None
            if row is not None and row != (
                request.caller_id, request.session_id, self._owner_process
            ):
                raise ExecutionError("ownership_busy")

    def _reserve_shared_activity(self, request):
        """Atomically arbitrate ownership and motion admission across runtimes."""
        if self._ownership_db is None:
            return
        db = self._ownership_db
        try:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute(
                "SELECT caller_id, session_id, process_id FROM performer_owners WHERE performer_id=?",
                (request.performer_id,),
            ).fetchone()
            if owner is not None and owner != (
                request.caller_id, request.session_id, self._owner_process
            ):
                db.execute("ROLLBACK")
                raise ExecutionError("ownership_busy")
            active = db.execute(
                "SELECT 1 FROM performer_activity WHERE performer_id=? LIMIT 1",
                (request.performer_id,),
            ).fetchone()
            if active is not None:
                db.execute("ROLLBACK")
                raise ExecutionError("performer_busy")
            db.execute(
                "INSERT INTO performer_activity VALUES (?, ?, ?, ?, ?)",
                (request.performer_id, self._owner_process, request.id,
                 request.caller_id, request.session_id),
            )
            db.execute("COMMIT")
        except ExecutionError:
            raise
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise ExecutionError("ownership_unavailable") from None

    def _release_shared_activity(self, request):
        if self._ownership_db is None:
            return
        try:
            self._ownership_db.execute(
                "DELETE FROM performer_activity WHERE performer_id=? AND process_id=? AND action_id=?",
                (request.performer_id, self._owner_process, request.id),
            )
        except sqlite3.Error:
            raise ExecutionError("ownership_unavailable") from None

    @asynccontextmanager
    async def run(self):
        if self._tasks is not None:
            raise ExecutionError("already_running")
        async with anyio.create_task_group() as tasks:
            self._tasks = tasks
            try:
                yield self
            finally:
                self._tasks = None
                tasks.cancel_scope.cancel()

    def lookup(self, action_id: str) -> ActionRecord:
        try:
            return self._entries[action_id].record.model_copy(deep=True)
        except KeyError:
            raise ExecutionError("unknown_action") from None

    async def wait(self, action_id: str) -> ActionRecord:
        self.lookup(action_id)
        await self._entries[action_id].done.wait()
        return self.lookup(action_id)

    async def set_ambient_behavior(
        self, performer_id: str, control: AmbientBehaviorControl
    ) -> None:
        """Start or stop deterministic, bounded idle eye blinks.

        Behavior runs through the same action provider, resource arbiter, and
        ownership checks as authored operations. Any control acquisition or
        stop invalidates its generation; no event is dispatched after that.
        """
        self._provider(performer_id)
        previous = self._ambient_scopes.pop(performer_id, None)
        if previous is not None:
            previous.cancel()
        if not control.enabled:
            return
        if self._ownership_db is not None:
            # Ambient schedules live in process memory and cannot be invalidated
            # by a stop or a primary action in another server process.
            raise ExecutionError("ambient_shared_process_unsupported")
        if self._tasks is None:
            raise ExecutionError("not_running")
        generation = self._generation[performer_id]
        serial = self._ambient_serial.get(performer_id, 0) + 1
        self._ambient_serial[performer_id] = serial
        scope = anyio.CancelScope()
        self._ambient_scopes[performer_id] = scope
        self._tasks.start_soon(
            self._run_ambient, performer_id, control.model_copy(deep=True), generation, serial, scope
        )

    async def _run_ambient(self, performer_id, control, generation, serial, scope):
        # Stable cadence and IDs make the schedule reproducible under FakeClock.
        start = self.stage.clock.now()
        with scope:
            for index in range(control.max_events):
                target = start + (index + 1) * control.interval_seconds
                if target - start > control.max_duration_seconds:
                    break
                await self.stage.clock.sleep_until(target)
                if generation != self._generation[performer_id]:
                    break
                # Authored ownership suppresses ambient behavior before it can
                # occupy resources or submit a provider operation.
                if performer_id in self._owners or performer_id in self._stopping:
                    break
                request = ActionRequest(
                    id=f"ambient-{performer_id}-{generation}-{serial}-{index}",
                    performer_id=performer_id,
                    caller_id="ambient",
                    session_id="idle",
                    operation="face",
                    parameters={
                        "left_eye": {"weight": 0},
                        "right_eye": {"weight": 0},
                    },
                )
                try:
                    await self.submit(request)
                except ExecutionError:
                    # Busy/owned/inhibited means yield. Never retry against a
                    # primary action or after a stop-invalidated generation.
                    break
                await self.wait(request.id)
                if generation != self._generation[performer_id]:
                    break
                if performer_id not in self._owners:
                    request = request.model_copy(update={
                        "id": f"ambient-{performer_id}-{generation}-{serial}-{index}-restore",
                        "parameters": {
                            "left_eye": {"weight": 100},
                            "right_eye": {"weight": 100},
                        },
                    })
                    try:
                        await self.submit(request)
                        await self.wait(request.id)
                    except ExecutionError:
                        break
        if self._ambient_scopes.get(performer_id) is scope:
            self._ambient_scopes.pop(performer_id, None)

    def _update(self, entry, state, **changes):
        timestamps = dict(entry.record.timestamps)
        timestamps[state.value] = datetime.now(UTC).isoformat()
        entry.record = entry.record.model_copy(
            update={"state": state, "timestamps": timestamps, **changes}
        )
        self._persist(entry)
        if state in _TERMINAL:
            entry.done.set()

    async def submit(self, request: ActionRequest) -> ActionRecord:
        # JSON roundtrip both validates JSON safety and isolates mutable input.
        try:
            payload = json.dumps(
                request.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            snapshot = ActionRequest.model_validate_json(payload)
        except (ValueError, TypeError):
            raise ExecutionError("invalid_payload") from None
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        if request.id in self._entries:
            entry = self._entries[request.id]
            if entry.fingerprint != fingerprint:
                raise ExecutionError("request_id_conflict")
            return self.lookup(request.id)
        if self._tasks is None:
            raise ExecutionError("not_running")
        provider = self._provider(snapshot.performer_id)
        self._check_owner(snapshot)
        if snapshot.performer_id in self._inhibited:
            raise ExecutionError("stop_unconfirmed")
        generation = self._generation[snapshot.performer_id]
        try:
            with anyio.fail_after(self.execution_timeout):
                await provider.validate(snapshot.model_copy(deep=True))
        except Exception:  # noqa: BLE001
            raise ExecutionError("validation_failed") from None
        # A concurrent submit/stop/ownership change may have happened in validation.
        if snapshot.id in self._entries:
            return await self.submit(snapshot)
        if self._tasks is None:
            raise ExecutionError("not_running")
        self._check_owner(snapshot)
        if snapshot.performer_id in self._inhibited:
            raise ExecutionError("stop_unconfirmed")
        if (
            snapshot.performer_id in self._stopping
            or generation != self._generation[snapshot.performer_id]
        ):
            raise ExecutionError("stop_invalidated_submission")
        try:
            resources = tuple(
                sorted(set(provider.resources_for(snapshot.model_copy(deep=True))))
            )
        except Exception:  # noqa: BLE001
            raise ExecutionError("invalid_resources") from None
        if not resources or any(not isinstance(r, str) or not r for r in resources):
            raise ExecutionError("invalid_resources")
        for entry in self._entries.values():
            if (
                entry.reserved
                and entry.request.performer_id == snapshot.performer_id
                and set(entry.record.resources).intersection(resources)
            ):
                raise ExecutionError("resource_busy")
        if any(
            self.stage._resource_locks.get((snapshot.performer_id, r)) is not None
            and self.stage._resource_locks[(snapshot.performer_id, r)].locked()
            for r in resources
        ):
            raise ExecutionError("resource_busy")
        record = ActionRecord(
            id=snapshot.id,
            state=ActionState.ACCEPTED,
            operation=snapshot.operation,
            caller_id=snapshot.caller_id,
            session_id=snapshot.session_id,
            cue_id=snapshot.cue_id,
            take_id=snapshot.take_id,
            asset_id=snapshot.asset_id,
            asset_revision=snapshot.asset_revision,
            parameters=snapshot.parameters,
            resources=resources,
        )
        entry = _Entry(snapshot, fingerprint, record, True, anyio.Event())
        self._reserve_shared_activity(snapshot)
        self._entries[snapshot.id] = entry
        self._persist(entry)
        self._update(entry, ActionState.ACCEPTED)
        self._update(entry, ActionState.QUEUED)
        self._tasks.start_soon(self._execute, entry, provider, generation)
        return self.lookup(snapshot.id)

    async def _emit(self, entry, event_type):
        await self.stage.emitter.emit(
            PerformanceEvent(
                type=event_type,
                timestamp=self.stage.clock.now(),
                performance=entry.request.take_id or "execution",
                character=entry.request.performer_id,
                action=entry.request.operation,
                detail=entry.request.id,
            )
        )

    async def _execute(self, entry, provider, generation):
        submitted = False
        locks = []
        with anyio.CancelScope() as scope:
            entry.scope = scope
            try:
                if entry.record.state in _TERMINAL:
                    return
                for resource in entry.record.resources:
                    lock = self.stage._resource_locks.setdefault(
                        (entry.request.performer_id, resource), anyio.Lock()
                    )
                    lock.acquire_nowait()
                    locks.append(lock)
                self._check_owner(entry.request)
                with anyio.fail_after(self.execution_timeout):
                    await provider.validate(entry.request.model_copy(deep=True))
                if generation != self._generation[entry.request.performer_id]:
                    raise ExecutionError("stop_invalidated_submission")
                current_resources = tuple(
                    sorted(
                        set(provider.resources_for(entry.request.model_copy(deep=True)))
                    )
                )
                if current_resources != entry.record.resources:
                    raise ExecutionError("resource_claims_changed")
                self._update(entry, ActionState.RUNNING)
                await self._emit(entry, EventType.ACTION_STARTED)
                # Event callbacks can change authority or readiness. Check again
                # after they finish and immediately before dispatch. The provider
                # must also enforce live authority at its own device boundary.
                with anyio.fail_after(self.execution_timeout):
                    await provider.validate(entry.request.model_copy(deep=True))
                if (
                    entry.record.state in _TERMINAL
                    or generation != self._generation[entry.request.performer_id]
                ):
                    raise ExecutionError("stop_invalidated_submission")
                submitted = True
                with anyio.fail_after(self.execution_timeout):
                    completion = await provider.execute(
                        entry.request.model_copy(deep=True)
                    )
                if generation != self._generation[entry.request.performer_id]:
                    raise ExecutionError("stop_invalidated_completion")
                if (
                    not isinstance(completion, Completion)
                    or not isinstance(completion.evidence, str)
                    or not completion.evidence
                ):
                    raise ExecutionError("missing_completion_evidence")
                if entry.record.state not in _TERMINAL:
                    self._update(
                        entry,
                        ActionState.COMPLETED,
                        completion_evidence=completion.evidence[:1024],
                    )
                    entry.reserved = False
                    self._persist(entry)
                    self._release_shared_activity(entry.request)
                    await self._emit(entry, EventType.ACTION_COMPLETED)
            except BaseException as exc:
                if entry.record.state not in _TERMINAL:
                    self._update(
                        entry,
                        ActionState.UNKNOWN if submitted else ActionState.FAILED,
                        error="execution_outcome_unknown"
                        if submitted
                        else "pre_execution_failed",
                    )
                    if not submitted:
                        entry.reserved = False
                        self._persist(entry)
                        self._release_shared_activity(entry.request)
                    with anyio.CancelScope(shield=True):
                        try:
                            with anyio.move_on_after(self.stop_timeout) as event_timeout:
                                await self._emit(entry, EventType.ACTION_FAILED)
                            if event_timeout.cancel_called:
                                self.event_errors.append("failure_event_timeout")
                        except Exception as event_error:  # noqa: BLE001
                            self.event_errors.append(type(event_error).__name__)
                if not isinstance(exc, Exception):
                    raise
            finally:
                for lock in reversed(locks):
                    lock.release()
                entry.scope = None

    async def cancel(self, action_id: str) -> StopResult:
        """Device-wide stop: invalidates queued work and affects sibling actions."""
        self.lookup(action_id)
        entry = self._entries[action_id]
        if entry.record.state in _TERMINAL and not entry.reserved:
            raise ExecutionError("action_not_active")
        return await self.stop(entry.request.performer_id)

    async def stop(self, performer_id: str) -> StopResult:
        provider = self._provider(performer_id)
        if performer_id in self._stopping:
            raise ExecutionError("stop_in_progress")
        if self._ownership_db is not None:
            try:
                remote = self._ownership_db.execute(
                    "SELECT 1 FROM performer_activity WHERE performer_id=? AND process_id<>? LIMIT 1",
                    (performer_id, self._owner_process),
                ).fetchone()
            except sqlite3.Error:
                raise ExecutionError("ownership_unavailable") from None
            if remote is not None:
                # A device stop cannot fence a remote task between its last
                # software check and the provider dispatch. Never imply that
                # stopping this process reconciles that task.
                raise ExecutionError("cross_process_stop_unsupported")
        self._stopping.add(performer_id)
        self._generation[performer_id] += 1
        ambient_scope = self._ambient_scopes.pop(performer_id, None)
        if ambient_scope is not None:
            ambient_scope.cancel()
        try:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(self.stop_timeout):
                        outcome = await provider.stop()
                    if (
                        not isinstance(outcome, StopResult)
                        or not isinstance(outcome.confirmed, bool)
                        or not isinstance(outcome.evidence, str)
                        or not outcome.evidence
                    ):
                        outcome = StopResult(False, "missing_stop_evidence")
                    else:
                        outcome = StopResult(outcome.confirmed, outcome.evidence[:1024])
                except Exception:  # noqa: BLE001
                    outcome = StopResult(False, "stop_outcome_unknown")
                if outcome.confirmed:
                    self._inhibited.discard(performer_id)
                else:
                    self._inhibited.add(performer_id)
                for entry in self._entries.values():
                    if entry.request.performer_id != performer_id:
                        continue
                    if entry.record.state in _TERMINAL and not entry.reserved:
                        continue
                    if outcome.confirmed:
                        self._update(
                            entry,
                            ActionState.CANCELLED,
                            cancellation_outcome=outcome.evidence[:1024],
                        )
                        if entry.scope is not None:
                            entry.scope.cancel()
                        entry.reserved = False
                        self._persist(entry)
                        # Only this runtime's own rows can be released. A
                        # different process may still have an unobserved task.
                        self._release_shared_activity(entry.request)
                    else:
                        self._update(
                            entry,
                            ActionState.UNKNOWN,
                            error="stop_outcome_unknown",
                            cancellation_outcome="stop_unconfirmed",
                        )
                        if entry.scope is not None:
                            entry.scope.cancel()
                return outcome
        finally:
            self._stopping.remove(performer_id)
