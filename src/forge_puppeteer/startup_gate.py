"""Start and end sessions at each robot's standard ``sleep`` pose.

Flow (see AGENTS.md):

* ``check()`` reads every performer's pose (read-only) and compares it with its
  standard ``sleep`` pose. A performer that is not provably at sleep is *gated*:
  the conversation ensemble will not give it startup or presence motion.
* The gate reports per-joint deltas and asks the user. ``move_to_sleep`` is the
  user's "yes": one sleep move through the normal ExecutionRuntime, then one
  re-check. No retries. ``confirm_at_sleep`` is for robots that cannot measure
  their pose (commanded-only feedback).
* ``park()`` ends a session the same way: sleep move, re-check, report. It stops
  at the first fault and reports it.

The sleep move is an authored cue (an SDK-validated, pinned pose target for
SO-ARM101). Without one the gate can only report; the user moves the robot by
hand and re-checks.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

import anyio

from .behavior_contract import BehaviorCue
from .execution import ActionRequest, ActionState, ExecutionError, ExecutionRuntime
from .standard_poses import (
    PoseCheck,
    PoseReader,
    RobotStandardPoses,
    pose_check,
)

if TYPE_CHECKING:
    from .conversation_ensemble import ConversationEnsemble

AT_SLEEP = "at_sleep"
NOT_AT_SLEEP = "not_at_sleep"
NEEDS_CONFIRMATION = "needs_confirmation"
CONFIRMED = "confirmed_by_user"
UNREADABLE = "unreadable"
OPEN_STATUSES = frozenset({AT_SLEEP, CONFIRMED})


class StartupGate:
    def __init__(
        self,
        runtime: ExecutionRuntime,
        ensemble: ConversationEnsemble,
        readers: Mapping[str, PoseReader],
        poses: Mapping[str, RobotStandardPoses],
        sleep_cues: Mapping[str, BehaviorCue] | None = None,
        *,
        caller_id: str = "startup-gate",
        settle_s: float = 0.5,
        settled_motion_deg: float = 1.0,
    ):
        if set(readers) != set(poses) or not set(readers) <= set(ensemble.packs):
            raise ValueError("readers and standard poses must cover the same configured performers")
        sleep_cues = dict(sleep_cues or {})
        if not set(sleep_cues) <= set(readers):
            raise ValueError("sleep cues must belong to configured performers")
        self.runtime, self.ensemble = runtime, ensemble
        self.readers, self.poses, self.sleep_cues = dict(readers), dict(poses), sleep_cues
        self.caller_id = caller_id
        self.settle_s, self.settled_motion_deg = settle_s, settled_motion_deg
        self._session_id = f"startup-gate-{uuid.uuid4().hex[:12]}"
        self._status: dict[str, dict[str, object]] = {}

    # -- reading ---------------------------------------------------------
    async def _read(self, name: str) -> dict[str, object]:
        """Read-only pose check of one performer; opens or closes its gate."""
        try:
            measured = await self.readers[name].read_pose()
            check: PoseCheck = pose_check(measured, self.poses[name], "sleep")
        except Exception as exc:  # noqa: BLE001 - any read failure keeps the robot still
            entry = {"status": UNREADABLE, "reason": f"pose read failed: {type(exc).__name__}",
                     "deltas_deg": {}, "can_move_to_sleep": False}
        else:
            if check.ok:
                status = AT_SLEEP
            elif check.needs_confirmation:
                status = NEEDS_CONFIRMATION
            elif check.deltas_deg:
                status = NOT_AT_SLEEP
            else:
                status = UNREADABLE
            entry = {"status": status, "reason": check.reason,
                     "deltas_deg": {k: round(v, 2) for k, v in check.deltas_deg.items()},
                     "can_move_to_sleep": name in self.sleep_cues and status != UNREADABLE}
        entry["message"] = self._message(name, entry)
        self._status[name] = entry
        self.ensemble.set_gate(name, None if entry["status"] in OPEN_STATUSES else str(entry["status"]))
        return entry

    def _message(self, name: str, entry: Mapping[str, object]) -> str:
        status = entry["status"]
        if status == AT_SLEEP:
            return f"{name} is at its sleep position."
        if status == CONFIRMED:
            return f"{name} was confirmed at sleep by the user."
        can_move = bool(entry.get("can_move_to_sleep"))
        ask = (f"Move {name} to its sleep position first? (ensemble_prepare, action='move_to_sleep')"
               if can_move else
               f"{name} has no authored sleep move; place it at sleep by hand, then re-check.")
        if status == NEEDS_CONFIRMATION:
            return (f"{name} cannot measure its pose ({entry['reason']}). Is it parked at sleep? "
                    "If so, confirm with action='confirm_at_sleep'."
                    + (" Otherwise it can be moved there (action='move_to_sleep')." if can_move else ""))
        if status == NOT_AT_SLEEP:
            return f"{name} is not at sleep: {entry['reason']}. {ask}"
        return f"{name}: {entry['reason']}. {ask}"

    async def check(self) -> dict[str, object]:
        """Read all performers. Never moves anything."""
        for name in self.readers:
            await self._read(name)
        return {"outcome": "observed", **self.snapshot()}

    def snapshot(self) -> dict[str, object]:
        return {
            "ready": bool(self._status) and all(s["status"] in OPEN_STATUSES for s in self._status.values()),
            "performers": {k: dict(v) for k, v in self._status.items()},
        }

    # -- user decisions --------------------------------------------------
    def _unknown(self, performer_id: str) -> dict[str, object] | None:
        if performer_id not in self.readers:
            return {"outcome": "denied", "reason": "unknown_performer"}
        return None

    async def confirm_at_sleep(self, performer_id: str) -> dict[str, object]:
        """User attests that a commanded-only robot is parked. Refused when a pose is measured."""
        if (bad := self._unknown(performer_id)) is not None:
            return bad
        entry = await self._read(performer_id)
        if entry["status"] in OPEN_STATUSES:
            return {"outcome": "ok", **self.snapshot()}
        if entry["status"] != NEEDS_CONFIRMATION:
            return {"outcome": "denied", "reason": "measured_pose_disagrees", **self.snapshot()}
        entry = {**entry, "status": CONFIRMED, "message": self._message(performer_id, {"status": CONFIRMED})}
        self._status[performer_id] = entry
        self.ensemble.set_gate(performer_id, None)
        return {"outcome": "confirmed", **self.snapshot()}

    async def _sleep_move(self, performer_id: str, action_id: str) -> dict[str, object]:
        """One sleep move + one re-check. Never retries."""
        cue = self.sleep_cues.get(performer_id)
        if cue is None:
            return {"outcome": "unsupported", "reason": "no_sleep_move_configured", **self.snapshot()}
        if self.ensemble.snapshot()["state"] != "disconnected":
            return {"outcome": "denied", "reason": "conversation_session_active", **self.snapshot()}
        if len(action_id) > 128:
            return {"outcome": "denied", "reason": "action_id_too_long"}
        entry = await self._read(performer_id)
        if entry["status"] == AT_SLEEP:
            return {"outcome": "already_at_sleep", **self.snapshot()}
        try:
            await self.runtime.submit(ActionRequest(
                id=action_id, performer_id=performer_id, caller_id=self.caller_id,
                session_id=self._session_id, operation=cue.operation,
                parameters=dict(cue.parameters), cue_id="sleep",
                asset_id=cue.asset_id, asset_revision=cue.asset_revision,
            ))
            record = await self.runtime.wait(action_id)
        except ExecutionError as exc:
            return {"outcome": "denied", "reason": exc.code, **self.snapshot()}
        if record.state != ActionState.COMPLETED:
            return {"outcome": "failed", "state": str(record.state), "error": record.error,
                    "hint": "Not retrying. Check the robot, then re-check, or call ensemble_recover once it rests.", **self.snapshot()}
        entry = await self._read(performer_id)
        return {"outcome": "at_sleep" if entry["status"] in OPEN_STATUSES else "not_at_sleep_after_move",
                **self.snapshot()}

    async def move_to_sleep(self, performer_id: str, action_id: str) -> dict[str, object]:
        """The user's 'yes': move one performer to sleep, re-check once."""
        if (bad := self._unknown(performer_id)) is not None:
            return bad
        return await self._sleep_move(performer_id, action_id)

    async def park(self, action_id: str) -> dict[str, object]:
        """End of session: every performer to sleep, re-checked. Stops at the first fault."""
        results: dict[str, object] = {}
        for name in self.readers:
            if name not in self.sleep_cues:
                entry = await self._read(name)
                results[name] = {"outcome": "no_sleep_move_configured", "status": entry["status"],
                                 "message": entry["message"]}
                continue
            result = await self._sleep_move(name, f"{action_id}:{name}"[:128])
            results[name] = {k: v for k, v in result.items() if k != "performers"}
            if result["outcome"] not in {"at_sleep", "already_at_sleep"}:
                return {"outcome": "stopped_on_fault", "performer": name, "results": results, **self.snapshot()}
        return {"outcome": "parked" if all(
            r["outcome"] in {"at_sleep", "already_at_sleep"} for r in results.values()
        ) else "parked_with_notes", "results": results, **self.snapshot()}

    # -- recovery from quarantine -----------------------------------------
    def _quarantine_sources(self, name: str) -> list[str]:
        sources = []
        if self.ensemble.is_blocked(name):
            sources.append("ensemble")
        if self.runtime.is_inhibited(name):
            sources.append("runtime")
        probe = getattr(self.readers[name], "is_quarantined", None)
        if callable(probe) and probe():
            sources.append("provider")
        return sources

    async def _settled_near_standard_pose(self, name: str) -> str | None:
        """Two reads ``settle_s`` apart, both near sleep or neutral and not moving."""
        try:
            first = await self.readers[name].read_pose()
            await anyio.sleep(self.settle_s)
            second = await self.readers[name].read_pose()
        except Exception:  # noqa: BLE001 - no evidence, fall back to STOP
            return None
        if first.source != "measured" or second.source != "measured":
            return None
        if set(first.channels) != set(second.channels) or not first.channels:
            return None
        moved = max(abs(first.channels[k] - second.channels[k]) for k in first.channels)
        if moved > self.settled_motion_deg:
            return None
        for pose in ("sleep", "neutral"):
            a = pose_check(first, self.poses[name], pose)
            b = pose_check(second, self.poses[name], pose)
            if a.ok and b.ok:
                return (f"measured pose settled near {pose} "
                        f"(worst delta {abs(b.worst_delta_deg):.1f} deg, moved {moved:.2f} deg in {self.settle_s:g} s)")
        return None

    async def recover(self, performer_id: str) -> dict[str, object]:
        """Lift a quarantine after fresh evidence; never retries or moves the robot.

        Evidence is either a measured pose that is settled near sleep/neutral, or a
        confirmed STOP. Afterwards the sleep check runs again, so a robot recovered
        at neutral is asked to go to sleep before the next session.
        """
        if (bad := self._unknown(performer_id)) is not None:
            return bad
        sources = self._quarantine_sources(performer_id)
        if not sources:
            return {"outcome": "not_quarantined", **self.snapshot()}
        if self.ensemble.snapshot()["session_id"] is not None:
            return {"outcome": "denied", "reason": "conversation_session_active", **self.snapshot()}
        provider = self.readers[performer_id]
        evidence = await self._settled_near_standard_pose(performer_id)
        try:
            if evidence is not None:
                self.runtime.recover_unknown(performer_id, evidence)
            else:
                stop = await self.runtime.stop(performer_id)
                if not stop.confirmed:
                    return {"outcome": "not_recovered", "reason": "no_settled_pose_and_stop_unconfirmed",
                            "quarantine": sources,
                            "hint": "Not retrying. Check the robot; once it rests at sleep or neutral, run recover again.",
                            **self.snapshot()}
                evidence = f"confirmed STOP: {stop.evidence}"
            clear = getattr(provider, "clear_quarantine", None)
            if callable(clear):
                clear()
            self.runtime.release_any_control(performer_id, self.ensemble.caller_id)
        except ExecutionError as exc:
            return {"outcome": "not_recovered", "reason": exc.code, "quarantine": sources, **self.snapshot()}
        result = await self.ensemble.recover(performer_id)
        if result["outcome"] == "denied":
            return {"outcome": "not_recovered", "reason": result["reason"], "quarantine": sources, **self.snapshot()}
        await self._read(performer_id)
        return {"outcome": "recovered", "evidence": evidence, "was_quarantined_by": sources,
                **self.snapshot()}
