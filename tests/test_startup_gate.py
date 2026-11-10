"""Sleep-pose startup gate and park, on the real runtime with fake robots."""
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage
from forge_puppeteer.standard_poses import MeasuredPose, load_standard_poses
from forge_puppeteer.startup_gate import StartupGate

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


SLEEP = load_standard_poses("soarm101", override_dir=None).pose("sleep").channels
SLEEP_CUE = BehaviorCue(at_s=0, operation="go_sleep")


class FakeArm:
    """Reports a joint pose; a 'go_sleep' action moves it (or not, or fails)."""

    def __init__(self, offset_deg=0.0, *, reaches_sleep=True, fail=False, commanded=False,
                 stop_confirms=True, pose=None, drift_deg=0.0):
        self.offset = offset_deg
        self.reaches_sleep, self.fail, self.commanded = reaches_sleep, fail, commanded
        self.stop_confirms, self.pose, self.drift_deg = stop_confirms, pose, drift_deg
        self.executed, self.stops, self._reads = [], 0, 0

    async def read_pose(self):
        if self.commanded:
            return MeasuredPose({}, "commanded")
        self._reads += 1
        if self.pose is not None:
            return MeasuredPose(dict(self.pose))
        return MeasuredPose({k: v + self.offset + self.drift_deg * self._reads for k, v in SLEEP.items()})

    async def validate(self, request):
        pass

    def resources_for(self, request):
        return ("motion",)

    async def execute(self, request):
        self.executed.append(request.operation)
        if self.fail:
            raise RuntimeError("sdk fault")
        if self.reaches_sleep:
            self.offset, self.pose = 0.0, None
        return Completion("done")

    async def stop(self):
        self.stops += 1
        return StopResult(self.stop_confirms, "stopped" if self.stop_confirms else "no acknowledgment")


def _pack(performer_id):
    hold = BehaviorPattern(source="procedural", mode="hold", cues=())
    return PerformerBehaviors.author(performer_id, {"idle": hold, "listening": hold, "speaking": hold})


@asynccontextmanager
async def room(arms, *, cues=None):
    runtime = ExecutionRuntime(PerformanceStage())
    for name, arm in arms.items():
        runtime.register(name, arm)
    ensemble = ConversationEnsemble(runtime, [_pack(n) for n in arms])
    poses = {n: load_standard_poses("soarm101", override_dir=None) for n in arms}
    gate = StartupGate(runtime, ensemble, arms, poses,
                       {n: SLEEP_CUE for n in arms} if cues is None else cues, settle_s=0.01)
    async with runtime.run(), ensemble.run():
        yield gate, ensemble
    runtime.close()


async def test_at_sleep_opens_the_gate():
    async with room({"arm": FakeArm(2.0)}) as (gate, ensemble):
        result = await gate.check()
        assert result["ready"] and result["performers"]["arm"]["status"] == "at_sleep"
        assert ensemble.snapshot()["performers"]["arm"]["status"] == "idle"


async def test_not_at_sleep_reports_deltas_asks_and_holds_still():
    arm = FakeArm(12.0)
    async with room({"arm": arm}) as (gate, ensemble):
        result = await gate.check()
        entry = result["performers"]["arm"]
        assert not result["ready"] and entry["status"] == "not_at_sleep"
        assert entry["deltas_deg"]["shoulder_lift"] == pytest.approx(12.0)
        assert "Move arm to its sleep position first?" in entry["message"]
        # A conversation starting now must not move the held robot.
        await ensemble.observe(_idle_event())
        assert ensemble.snapshot()["performers"]["arm"]["status"] == "unavailable"
        assert arm.executed == []


def _idle_event(sequence=0):
    from forge_puppeteer.behavior_contract import EnsemblePresence
    return EnsemblePresence(session_id="s1", event_id=f"e{sequence}", sequence=sequence,
                            state="idle", source="agent_state")


async def test_move_to_sleep_runs_once_rechecks_and_opens_the_gate():
    arm = FakeArm(12.0)
    async with room({"arm": arm}) as (gate, ensemble):
        await gate.check()
        result = await gate.move_to_sleep("arm", "sleep-1")
        assert result["outcome"] == "at_sleep" and result["ready"]
        assert arm.executed == ["go_sleep"]
        await ensemble.observe(_idle_event())
        assert ensemble.snapshot()["performers"]["arm"]["status"] != "unavailable"


async def test_move_that_does_not_reach_sleep_is_not_retried():
    arm = FakeArm(12.0, reaches_sleep=False)
    async with room({"arm": arm}) as (gate, _):
        await gate.check()
        result = await gate.move_to_sleep("arm", "sleep-1")
        assert result["outcome"] == "not_at_sleep_after_move" and not result["ready"]
        assert arm.executed == ["go_sleep"]


async def test_failed_move_reports_and_does_not_retry():
    arm = FakeArm(12.0, fail=True)
    async with room({"arm": arm}) as (gate, _):
        await gate.check()
        result = await gate.move_to_sleep("arm", "sleep-1")
        assert result["outcome"] == "failed" and "Not retrying" in result["hint"]
        assert arm.executed == ["go_sleep"] and not result["ready"]


async def test_already_at_sleep_makes_no_move():
    arm = FakeArm(1.0)
    async with room({"arm": arm}) as (gate, _):
        assert (await gate.move_to_sleep("arm", "sleep-1"))["outcome"] == "already_at_sleep"
        assert arm.executed == []


async def test_no_sleep_move_configured_is_reported_not_guessed():
    async with room({"arm": FakeArm(12.0)}, cues={}) as (gate, _):
        entry = (await gate.check())["performers"]["arm"]
        assert not entry["can_move_to_sleep"] and "by hand" in entry["message"]
        assert (await gate.move_to_sleep("arm", "x"))["outcome"] == "unsupported"


async def test_commanded_only_robot_needs_user_confirmation_once():
    async with room({"arm": FakeArm(commanded=True)}, cues={}) as (gate, _):
        entry = (await gate.check())["performers"]["arm"]
        assert entry["status"] == "needs_confirmation"
        assert (await gate.confirm_at_sleep("arm"))["outcome"] == "confirmed"
        assert gate.snapshot()["ready"]


async def test_user_cannot_confirm_over_a_measured_disagreement():
    async with room({"arm": FakeArm(12.0)}) as (gate, _):
        await gate.check()
        result = await gate.confirm_at_sleep("arm")
        assert result["outcome"] == "denied" and result["reason"] == "measured_pose_disagrees"
        assert not result["ready"]


async def test_unreadable_pose_keeps_the_robot_still():
    arm = FakeArm()

    async def boom():
        raise RuntimeError("daemon down")

    arm.read_pose = boom
    async with room({"arm": arm}) as (gate, ensemble):
        entry = (await gate.check())["performers"]["arm"]
        assert entry["status"] == "unreadable" and "RuntimeError" in entry["reason"]
        await ensemble.observe(_idle_event())
        assert ensemble.snapshot()["performers"]["arm"]["status"] == "unavailable"


async def test_park_moves_each_robot_to_sleep_and_reports():
    a, b = FakeArm(20.0), FakeArm(0.5)
    async with room({"a": a, "b": b}) as (gate, _):
        result = await gate.park("end")
        assert result["outcome"] == "parked"
        assert result["results"]["a"]["outcome"] == "at_sleep"
        assert result["results"]["b"]["outcome"] == "already_at_sleep"
        assert a.executed == ["go_sleep"] and b.executed == []


async def test_park_stops_at_the_first_fault():
    a, b = FakeArm(20.0, fail=True), FakeArm(20.0)
    async with room({"a": a, "b": b}) as (gate, _):
        result = await gate.park("end")
        assert result["outcome"] == "stopped_on_fault" and result["performer"] == "a"
        assert b.executed == []


async def test_park_and_move_are_refused_during_a_conversation():
    arm = FakeArm(20.0)
    async with room({"arm": arm}) as (gate, ensemble):
        await ensemble.observe(_idle_event())
        result = await gate.park("end")
        assert result["results"]["arm"]["reason"] == "conversation_session_active"
        assert arm.executed == []


def test_gate_requires_matching_configured_performers():
    runtime = ExecutionRuntime(PerformanceStage())
    arm = FakeArm()
    runtime.register("arm", arm)
    ensemble = ConversationEnsemble(runtime, [_pack("arm")])
    with pytest.raises(ValueError):
        StartupGate(runtime, ensemble, {"arm": arm, "ghost": arm}, {"arm": None, "ghost": None})
    with pytest.raises(ValueError):
        StartupGate(runtime, ensemble, {"arm": arm}, {"arm": None}, {"ghost": SLEEP_CUE})
    runtime.close()
    assert math.isfinite(SLEEP["shoulder_pan"])


# --- quarantine recovery ------------------------------------------------------

NEUTRAL = load_standard_poses("soarm101", override_dir=None).pose("neutral").channels


async def _quarantine(runtime, ensemble, arm, name="arm"):
    """A real unknown-outcome quarantine: failed action, then an unconfirmed STOP."""
    from forge_puppeteer.execution import ActionRequest
    arm.fail, arm.stop_confirms = True, False
    await runtime.submit(ActionRequest(id="boom", performer_id=name, caller_id="c",
                                       session_id="s", operation="go_sleep"))
    await runtime.wait("boom")
    await runtime.stop(name)
    arm.fail = False
    ensemble._blocked.add(name)  # what a failed ambient slot would have recorded
    assert runtime.is_inhibited(name) and ensemble.is_blocked(name)


async def test_recover_from_settled_pose_near_sleep_clears_every_fence():
    arm = FakeArm(1.0)
    async with room({"arm": arm}) as (gate, ensemble):
        await _quarantine(gate.runtime, ensemble, arm)
        result = await gate.recover("arm")
        assert result["outcome"] == "recovered" and "settled near sleep" in result["evidence"]
        assert result["was_quarantined_by"] == ["ensemble", "runtime"]
        assert not gate.runtime.is_inhibited("arm") and not ensemble.is_blocked("arm")
        assert result["ready"]
        # The original action stays recorded as unknown, with the recovery evidence.
        record = gate.runtime.lookup("boom")
        assert str(record.state) == "unknown" and record.cancellation_outcome.startswith("recovered:")
        # And the performer can be used again by a new conversation.
        await ensemble.observe(_idle_event())
        assert ensemble.snapshot()["performers"]["arm"]["status"] != "blocked"


async def test_recover_without_settled_pose_falls_back_to_a_confirmed_stop():
    arm = FakeArm(pose={k: v + 40.0 for k, v in SLEEP.items()})  # mid-air, near nothing
    async with room({"arm": arm}) as (gate, ensemble):
        await _quarantine(gate.runtime, ensemble, arm)
        arm.stop_confirms = True
        result = await gate.recover("arm")
        assert result["outcome"] == "recovered" and result["evidence"].startswith("confirmed STOP")
        assert not gate.runtime.is_inhibited("arm")
        # Not at sleep, so the gate now asks the user.
        assert result["performers"]["arm"]["status"] == "not_at_sleep" and not result["ready"]


async def test_recover_with_moving_pose_and_unconfirmed_stop_stays_quarantined_and_does_not_retry():
    arm = FakeArm(drift_deg=1.5)  # still moving between reads
    async with room({"arm": arm}) as (gate, ensemble):
        await _quarantine(gate.runtime, ensemble, arm)
        stops = arm.stops
        result = await gate.recover("arm")
        assert result["outcome"] == "not_recovered"
        assert result["reason"] == "no_settled_pose_and_stop_unconfirmed" and "Not retrying" in result["hint"]
        assert arm.stops == stops + 1  # exactly one STOP attempt
        assert gate.runtime.is_inhibited("arm") and ensemble.is_blocked("arm")


async def test_recovered_at_neutral_is_then_asked_to_go_to_sleep():
    arm = FakeArm(pose=NEUTRAL)
    async with room({"arm": arm}) as (gate, ensemble):
        await _quarantine(gate.runtime, ensemble, arm)
        result = await gate.recover("arm")
        assert result["outcome"] == "recovered" and "settled near neutral" in result["evidence"]
        assert result["performers"]["arm"]["status"] == "not_at_sleep"
        moved = await gate.move_to_sleep("arm", "sleep-after-recover")
        assert moved["outcome"] == "at_sleep" and moved["ready"]


async def test_recover_is_refused_during_a_conversation_and_when_nothing_is_quarantined():
    arm = FakeArm(1.0)
    async with room({"arm": arm}) as (gate, ensemble):
        assert (await gate.recover("arm"))["outcome"] == "not_quarantined"
        await _quarantine(gate.runtime, ensemble, arm)
        await ensemble.observe(_idle_event())
        result = await gate.recover("arm")
        assert result["outcome"] == "denied" and result["reason"] == "conversation_session_active"
        assert gate.runtime.is_inhibited("arm")
        assert (await gate.recover("ghost"))["reason"] == "unknown_performer"


async def test_runtime_refuses_to_recover_while_an_action_is_in_flight():
    import anyio

    from forge_puppeteer.execution import ActionRequest, ExecutionError

    release = anyio.Event()

    class Slow(FakeArm):
        async def execute(self, request):
            await release.wait()
            return Completion("done")

    arm = Slow()
    async with room({"arm": arm}) as (gate, _):
        await gate.runtime.submit(ActionRequest(id="slow", performer_id="arm", caller_id="c",
                                                session_id="s", operation="go_sleep"))
        with pytest.raises(ExecutionError) as excinfo:
            gate.runtime.recover_unknown("arm", "pose looked fine")
        assert excinfo.value.code == "action_in_flight"
        release.set()
        await gate.runtime.wait("slow")


def test_reachy_provider_quarantine_can_be_inspected_and_lifted():
    from forge_puppeteer.devices.reachy import ReachyPlaybackProvider

    provider = ReachyPlaybackProvider(SimpleNamespace(), SimpleNamespace())
    assert not provider.is_quarantined()
    provider._quarantined = True
    assert provider.is_quarantined()
    provider.clear_quarantine()
    assert not provider.is_quarantined()


async def test_a_held_robot_can_be_moved_to_sleep_during_a_conversation_without_disturbing_the_others():
    from forge_puppeteer.behavior_contract import EnsemblePresence

    held, active = FakeArm(12.0), FakeArm(0.5)
    async with room({"held": held, "active": active}) as (gate, ensemble):
        await gate.check()  # "held" is not at sleep, "active" is
        await ensemble.observe(_idle_event())  # the conversation starts without the held robot
        assert ensemble.snapshot()["performers"]["held"]["status"] == "unavailable"
        assert ensemble.is_idle_held("held") and not ensemble.is_idle_held("active")

        result = await gate.move_to_sleep("held", "sleep-held")
        assert result["outcome"] == "at_sleep" and result["ready"]
        assert held.executed == ["go_sleep"] and active.executed == []

        # It joins the conversation at the next presence transition.
        await ensemble.observe(EnsemblePresence(
            session_id="s1", event_id="e1", sequence=1, state="listening", source="agent_state"))
        assert ensemble.snapshot()["performers"]["held"]["status"] != "unavailable"


async def test_only_a_held_robot_may_move_during_a_conversation_and_park_stays_refused():
    arm = FakeArm(20.0)
    async with room({"arm": arm}) as (gate, ensemble):
        await ensemble.observe(_idle_event())  # no startup check ran, so nothing is held
        denied = await gate.move_to_sleep("arm", "sleep-1")
        assert denied["outcome"] == "denied" and denied["reason"] == "conversation_session_active"
        await gate.check()  # now held, but park never moves anything mid-conversation
        parked = await gate.park("end")
        assert parked["results"]["arm"]["reason"] == "conversation_session_active"
        assert arm.executed == []
