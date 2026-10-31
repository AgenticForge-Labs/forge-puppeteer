"""Software-only lifecycle and race tests; no SDK/hardware imports."""

import anyio
import anyio.lowlevel
import pytest

from forge_puppeteer.actions import GestureAction
from forge_puppeteer.embodiments import MockEmbodiment
from forge_puppeteer.events import EventType
from forge_puppeteer.execution import (
    ActionRequest,
    AmbientBehaviorControl,
    Completion,
    ExecutionError,
    ExecutionRuntime,
    StopResult,
)
from forge_puppeteer.performance import Cue
from forge_puppeteer.robot_contracts import ActionState
from forge_puppeteer.runtime import Character, PerformanceStage


class FakeProvider:
    def __init__(self):
        self.entered = anyio.Event()
        self.finish = anyio.Event()
        self.validations = 0
        self.executions = 0
        self.stops = 0
        self.allowed = True
        self.fail_execution = False
        self.confirm_stop = True
        self.evidence = "fake completion"

    async def validate(self, request):
        self.validations += 1
        await anyio.lowlevel.checkpoint()
        if not self.allowed or request.operation not in {"control", "face"}:
            raise ValueError("private backend detail")

    def resources_for(self, request):
        return (request.parameters.get("resource", "motion"),)

    async def execute(self, request):
        self.executions += 1
        self.entered.set()
        await self.finish.wait()
        if self.fail_execution:
            raise OSError("private backend detail")
        return Completion(self.evidence)

    async def stop(self):
        self.stops += 1
        return StopResult(self.confirm_stop, "fake stop")


def request(action_id="a", **kwargs):
    return ActionRequest(
        id=action_id,
        performer_id="robot",
        caller_id="caller",
        session_id="session",
        operation="control",
        **kwargs,
    )


def setup_runtime(**kwargs):
    stage = PerformanceStage()
    provider = FakeProvider()
    runtime = ExecutionRuntime(stage, **kwargs)
    runtime.register("robot", provider)
    return runtime, provider, stage


def test_lifecycle_snapshots_and_duplicate_identity():
    async def scenario():
        runtime, provider, stage = setup_runtime()
        events = []
        stage.emitter.subscribe(events.append)
        params = {"nested": {"value": 1}}
        original = request(
            parameters=params,
            take_id="take",
            cue_id="cue",
            asset_id="pack:pose",
            asset_revision="sha256:abc",
        )
        async with runtime.run():
            accepted = await runtime.submit(original)
            assert accepted.state == ActionState.QUEUED
            original.parameters["nested"]["value"] = 99
            await provider.entered.wait()
            duplicate = request(
                parameters={"nested": {"value": 1}},
                take_id="take",
                cue_id="cue",
                asset_id="pack:pose",
                asset_revision="sha256:abc",
            )
            assert (await runtime.submit(duplicate)).state == ActionState.RUNNING
            with pytest.raises(ExecutionError, match="request_id_conflict"):
                await runtime.submit(original)
            provider.finish.set()
            record = await runtime.wait("a")
            assert record.state == ActionState.COMPLETED
            assert record.parameters == {"nested": {"value": 1}}
            record.parameters["nested"]["value"] = 10
            assert runtime.lookup("a").parameters["nested"]["value"] == 1
            assert record.completion_evidence == "fake completion"
            assert set(record.timestamps) == {
                "accepted",
                "queued",
                "running",
                "completed",
            }
            assert record.take_id == "take" and record.asset_revision == "sha256:abc"
            await runtime.submit(duplicate)
        assert provider.executions == 1 and provider.validations == 3
        assert [e.type for e in events] == [
            EventType.ACTION_STARTED,
            EventType.ACTION_COMPLETED,
        ]
        assert all(e.performance == "take" and e.detail == "a" for e in events)

    anyio.run(scenario)


def test_journal_recovers_action_identity_and_quarantines_interrupted_work(tmp_path):
    async def scenario():
        journal = tmp_path / "actions.sqlite3"
        runtime, provider, _ = setup_runtime(journal_path=str(journal))
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
        assert runtime.lookup("a").state == ActionState.UNKNOWN
        runtime.close()

        recovered = ExecutionRuntime(PerformanceStage(), journal_path=str(journal))
        provider2 = FakeProvider()
        recovered.register("robot", provider2)
        assert recovered.lookup("a").state == ActionState.UNKNOWN
        async with recovered.run():
            # Same identity cannot be replayed after restart, and other work is
            # held until the adapter independently confirms stop.
            assert (await recovered.submit(request())).state == ActionState.UNKNOWN
            with pytest.raises(ExecutionError, match="stop_unconfirmed"):
                await recovered.submit(request("b"))
            assert (await recovered.stop("robot")).confirmed
            provider2.finish.set()
            assert (await recovered.submit(request("b"))).state == ActionState.QUEUED
            assert (await recovered.wait("b")).state == ActionState.COMPLETED
        recovered.close()

    anyio.run(scenario)


def test_journal_preserves_terminal_identity_across_restart(tmp_path):
    async def scenario():
        journal = tmp_path / "actions.sqlite3"
        runtime, provider, _ = setup_runtime(journal_path=str(journal))
        provider.finish.set()
        async with runtime.run():
            await runtime.submit(request())
            assert (await runtime.wait("a")).state == ActionState.COMPLETED
        runtime.close()

        recovered = ExecutionRuntime(PerformanceStage(), journal_path=str(journal))
        recovered.register("robot", FakeProvider())
        async with recovered.run():
            assert (await recovered.submit(request())).state == ActionState.COMPLETED
            with pytest.raises(ExecutionError, match="request_id_conflict"):
                await recovered.submit(request("a").model_copy(update={"operation": "different"}))
        recovered.close()

    anyio.run(scenario)


def test_conflict_rejection_independent_resources_and_ownership():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        runtime.acquire_control("robot", "caller", "session")
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            with pytest.raises(ExecutionError, match="resource_busy"):
                await runtime.submit(request("conflict"))
            await runtime.submit(request("face", parameters={"resource": "face.eyes"}))
            outsider = request("outsider").model_copy(update={"session_id": "other"})
            with pytest.raises(ExecutionError, match="ownership_busy"):
                await runtime.submit(outsider)
            with pytest.raises(ExecutionError, match="ownership_busy"):
                runtime.acquire_control("robot", "other", "session")
            with pytest.raises(ExecutionError, match="performer_busy"):
                runtime.release_control("robot", "caller", "session")
            provider.finish.set()
            await runtime.wait("a")
            await runtime.wait("face")
            runtime.release_control("robot", "caller", "session")
            runtime.acquire_control("robot", "other", "new")

    anyio.run(scenario)


def test_shared_control_ownership_arbitrates_across_processes(tmp_path):
    import os
    import subprocess
    import sys

    db = tmp_path / "owners.sqlite3"
    source = r'''
import sys
import anyio
from forge_puppeteer.execution import ActionRequest, Completion, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage
class Provider:
 async def validate(self, request): pass
 def resources_for(self, request): return ("motion",)
 async def execute(self, request): return Completion("done")
 async def stop(self): return StopResult(True, "stopped")
r = ExecutionRuntime(PerformanceStage(), ownership_path=sys.argv[1])
r.register("robot", Provider())
try:
    if sys.argv[3] == "motion":
        async def submit():
            async with r.run():
                try:
                    await r.submit(ActionRequest(id="motion", performer_id="robot", caller_id=sys.argv[2], session_id="session", operation="control"))
                    print("submitted", flush=True)
                except Exception as exc: print(getattr(exc, "code", type(exc).__name__), flush=True)
        anyio.run(submit)
    else:
        r.acquire_control("robot", sys.argv[2], "session")
        print("acquired", flush=True)
        if sys.argv[3] == "hold": input()
except Exception as exc:
    print(getattr(exc, "code", type(exc).__name__), flush=True)
finally:
    r.close()
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")
    first = subprocess.Popen(
        [sys.executable, "-c", source, str(db), "first", "hold"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert first.stdout.readline().strip() == "acquired"
        impersonation = subprocess.run(
            [sys.executable, "-c", source, str(db), "first", "motion"],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert impersonation.stdout.strip() == "ownership_busy"
        second = subprocess.run(
            [sys.executable, "-c", source, str(db), "second", "once"],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert second.stdout.strip() == "ownership_busy"
    finally:
        first.stdin.write("\n")
        first.stdin.flush()
        first.wait(timeout=10)
    recovered = subprocess.run(
        [sys.executable, "-c", source, str(db), "third", "once"],
        capture_output=True, text=True, env=env, timeout=10, check=False,
    )
    assert recovered.stdout.strip() == "ownership_busy"


def test_shared_motion_admission_is_atomic_and_crash_stays_quarantined(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    db = tmp_path / "shared.sqlite3"
    release = tmp_path / "release"
    source = r'''
import anyio, sys, time
from forge_puppeteer.execution import ActionRequest, Completion, ExecutionError, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage
class Provider:
 async def validate(self, request): pass
 def resources_for(self, request): return ("motion",)
 async def execute(self, request):
  print("entered", flush=True)
  while not __import__("os").path.exists(sys.argv[3]): await anyio.sleep(.02)
  return Completion("done")
 async def stop(self): return StopResult(True, "confirmed")
async def main():
 r=ExecutionRuntime(PerformanceStage(), ownership_path=sys.argv[1]); r.register("robot", Provider())
 try:
  if sys.argv[2] == "claim":
   try:
    r.acquire_control("robot", "caller", "session"); print("acquired", flush=True)
   except ExecutionError as exc: print(exc.code, flush=True)
  elif sys.argv[2] == "stop":
   try: await r.stop("robot"); print("stopped", flush=True)
   except ExecutionError as exc: print(exc.code, flush=True)
  else:
   async with r.run():
    try:
     await r.submit(ActionRequest(id="action", performer_id="robot", caller_id="caller", session_id="session", operation="control"))
     await r.wait("action"); print("completed", flush=True)
    except ExecutionError as exc: print(exc.code, flush=True)
 finally: r.close()
anyio.run(main)
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    first = subprocess.Popen(
        [sys.executable, "-c", source, str(db), "motion", str(release)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert first.stdout.readline().strip() == "entered"
        denied = subprocess.run(
            [sys.executable, "-c", source, str(db), "motion", str(release)],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert denied.stdout.strip() == "performer_busy"
        claim = subprocess.run(
            [sys.executable, "-c", source, str(db), "claim", str(release)],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert claim.stdout.strip() == "performer_busy"
        remote_stop = subprocess.run(
            [sys.executable, "-c", source, str(db), "stop", str(release)],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert remote_stop.stdout.strip() == "cross_process_stop_unsupported"
        first.kill()
        first.wait(timeout=10)
        stale = subprocess.run(
            [sys.executable, "-c", source, str(db), "motion", str(release)],
            capture_output=True, text=True, env=env, timeout=10, check=False,
        )
        assert stale.stdout.strip() == "performer_busy"
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)


def test_ambient_behavior_is_opt_in_bounded_and_uses_deterministic_ids():
    async def scenario():
        runtime, provider, stage = setup_runtime()
        stage.clock = __import__("forge_puppeteer.runtime", fromlist=["FakeClock"]).FakeClock()
        provider.finish.set()
        async with runtime.run():
            await anyio.lowlevel.checkpoint()
            assert provider.executions == 0
            await runtime.set_ambient_behavior(
                "robot",
                AmbientBehaviorControl(
                    enabled=True,
                    interval_seconds=2,
                    max_events=2,
                    max_duration_seconds=4,
                ),
            )
            with anyio.fail_after(1):
                while runtime._ambient_scopes:
                    await anyio.lowlevel.checkpoint()
            assert provider.executions == 4  # two close/restore pairs
            assert set(runtime._entries) == {
                "ambient-robot-0-1-0", "ambient-robot-0-1-0-restore",
                "ambient-robot-0-1-1", "ambient-robot-0-1-1-restore",
            }
            assert stage.clock.now() == 4

    anyio.run(scenario)


def test_scripted_owner_yields_ambient_and_stop_invalidates_schedule():
    async def scenario():
        runtime, provider, stage = setup_runtime()
        stage.clock = __import__("forge_puppeteer.runtime", fromlist=["FakeClock"]).FakeClock()
        provider.finish.set()
        runtime.acquire_control("robot", "script", "take")
        async with runtime.run():
            await runtime.set_ambient_behavior(
                "robot", AmbientBehaviorControl(enabled=True, max_events=5)
            )
            with anyio.fail_after(1):
                while runtime._ambient_scopes:
                    await anyio.lowlevel.checkpoint()
            assert provider.executions == 0
            runtime.release_control("robot", "script", "take")
            await runtime.set_ambient_behavior(
                "robot", AmbientBehaviorControl(enabled=True, max_events=5)
            )
            # Stop changes the generation and cancels future scheduled events.
            await runtime.stop("robot")
            await anyio.lowlevel.checkpoint()
            assert provider.executions == 0
            assert not runtime._ambient_scopes

    anyio.run(scenario)


def test_confirmed_cancel_stops_siblings_without_waiting_for_execution():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            await runtime.submit(request("face", parameters={"resource": "face.eyes"}))
            with anyio.fail_after(1):
                result = await runtime.cancel("a")
            assert result.confirmed and provider.stops == 1
            assert (await runtime.wait("a")).state == ActionState.CANCELLED
            assert (await runtime.wait("face")).state == ActionState.CANCELLED
            provider.finish.set()
            await anyio.lowlevel.checkpoint()
            assert runtime.lookup("a").state == ActionState.CANCELLED

    anyio.run(scenario)


def test_unconfirmed_stop_inhibits_all_resources_until_confirmed_stop():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            provider.confirm_stop = False
            assert not (await runtime.stop("robot")).confirmed
            assert (await runtime.wait("a")).state == ActionState.UNKNOWN
            assert runtime.lookup("a").cancellation_outcome == "stop_unconfirmed"
            with pytest.raises(ExecutionError, match="stop_unconfirmed"):
                await runtime.submit(
                    request("face", parameters={"resource": "face.eyes"})
                )
            provider.confirm_stop = True
            await runtime.stop("robot")
            await anyio.lowlevel.checkpoint()
            provider.finish.set()
            await runtime.submit(request("new"))
            assert (await runtime.wait("new")).state == ActionState.COMPLETED

    anyio.run(scenario)


@pytest.mark.parametrize("failure", ["exception", "missing_evidence", "timeout"])
def test_unknown_outcome_quarantines_claims_and_never_retries(failure):
    async def scenario():
        runtime, provider, _ = setup_runtime(execution_timeout=0.02)
        if failure != "timeout":
            provider.finish.set()
        provider.fail_execution = failure == "exception"
        provider.evidence = "" if failure == "missing_evidence" else "done"
        async with runtime.run():
            await runtime.submit(request())
            result = await runtime.wait("a")
            assert result.state == ActionState.UNKNOWN
            assert result.error == "execution_outcome_unknown"
            assert (await runtime.submit(request())).state == ActionState.UNKNOWN
            with pytest.raises(ExecutionError, match="resource_busy"):
                await runtime.submit(request("b"))
            assert provider.executions == 1
            await runtime.stop("robot")
            assert runtime.lookup("a").state == ActionState.CANCELLED

    anyio.run(scenario)


def test_revalidation_denial_and_initial_denial_never_execute():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            await runtime.submit(request())
            provider.allowed = False
            assert (await runtime.wait("a")).state == ActionState.FAILED
            assert provider.executions == 0
            with pytest.raises(ExecutionError, match="validation_failed"):
                await runtime.submit(request("denied"))
            with pytest.raises(ExecutionError, match="unknown_action"):
                runtime.lookup("denied")

    anyio.run(scenario)


def test_concurrent_duplicate_submissions_execute_once():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            async with anyio.create_task_group() as tasks:
                for _ in range(10):
                    tasks.start_soon(runtime.submit, request())
            await provider.entered.wait()
            provider.finish.set()
            assert (await runtime.wait("a")).state == ActionState.COMPLETED
            assert provider.executions == 1

    anyio.run(scenario)


def test_stop_during_validation_invalidates_submission():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        validating, proceed = anyio.Event(), anyio.Event()

        async def validate(request):
            validating.set()
            await proceed.wait()

        provider.validate = validate

        async def submit():
            with pytest.raises(ExecutionError, match="stop_invalidated_submission"):
                await runtime.submit(request())

        async with runtime.run(), anyio.create_task_group() as tasks:
            tasks.start_soon(submit)
            await validating.wait()
            await runtime.stop("robot")
            proceed.set()
        assert provider.executions == 0

    anyio.run(scenario)


def test_legacy_stage_resource_locks_are_shared():
    async def scenario():
        runtime, provider, stage = setup_runtime()
        embodiment = MockEmbodiment()
        stage.register(Character("robot", "Robot", embodiment))
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            cue = Cue(at=0, character="robot", action=GestureAction(name="wave"))
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(stage.execute_cue, "legacy", cue)
                await anyio.lowlevel.checkpoint()
                assert not embodiment.actions
                provider.finish.set()
            assert len(embodiment.actions) == 1

    anyio.run(scenario)


def test_shutdown_marks_running_work_unknown_without_physical_stop():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
        assert runtime.lookup("a").state == ActionState.UNKNOWN
        assert provider.stops == 0
        async with runtime.run():
            with pytest.raises(ExecutionError, match="resource_busy"):
                await runtime.submit(request("b"))
            await runtime.stop("robot")

    anyio.run(scenario)


def test_completion_during_pending_stop_cannot_report_success():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        stopping, finish_stop = anyio.Event(), anyio.Event()

        async def stop():
            stopping.set()
            await finish_stop.wait()
            return StopResult(False, "unconfirmed")

        provider.stop = stop
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(runtime.stop, "robot")
                await stopping.wait()
                provider.finish.set()
                assert (await runtime.wait("a")).state == ActionState.UNKNOWN
                finish_stop.set()
            assert runtime.lookup("a").state == ActionState.UNKNOWN

    anyio.run(scenario)


@pytest.mark.parametrize("stop_mode", ["timeout", "exception", "missing_evidence"])
def test_stop_failure_is_bounded_and_does_not_claim_cancellation(stop_mode):
    async def scenario():
        runtime, provider, _ = setup_runtime(stop_timeout=0.02)

        async def stop():
            if stop_mode == "timeout":
                await anyio.sleep_forever()
            if stop_mode == "exception":
                raise OSError("private error")
            return StopResult(True, "")

        provider.stop = stop
        async with runtime.run():
            await runtime.submit(request())
            await provider.entered.wait()
            with anyio.fail_after(1):
                outcome = await runtime.cancel("a")
            assert not outcome.confirmed
            assert (await runtime.wait("a")).state == ActionState.UNKNOWN
            assert runtime.lookup("a").cancellation_outcome == "stop_unconfirmed"

    anyio.run(scenario)


def test_resource_changes_after_acceptance_fail_before_execution():
    async def scenario():
        runtime, provider, _ = setup_runtime()

        def resources(request):
            return ("motion",) if provider.validations < 2 else ("voice",)

        provider.resources_for = resources
        async with runtime.run():
            await runtime.submit(request())
            assert (await runtime.wait("a")).state == ActionState.FAILED
            assert provider.executions == 0

    anyio.run(scenario)


def test_control_event_failure_prevents_submission():
    async def scenario():
        runtime, provider, stage = setup_runtime()

        def deny(event):
            if event.type == EventType.ACTION_STARTED:
                raise RuntimeError("control hook denied")

        stage.emitter.subscribe_control(deny)
        async with runtime.run():
            await runtime.submit(request())
            assert (await runtime.wait("a")).state == ActionState.FAILED
            assert provider.executions == 0

    anyio.run(scenario)


def test_stop_from_start_hook_prevents_device_submission():
    async def scenario():
        runtime, provider, stage = setup_runtime()

        async def stop_on_start(event):
            if event.type == EventType.ACTION_STARTED:
                await runtime.stop("robot")

        stage.emitter.subscribe_control(stop_on_start)
        async with runtime.run():
            await runtime.submit(request())
            assert (await runtime.wait("a")).state == ActionState.CANCELLED
            assert provider.executions == 0

    anyio.run(scenario)


def test_cancel_finished_action_does_not_stop_later_work():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        async with runtime.run():
            provider.finish.set()
            await runtime.submit(request())
            await runtime.wait("a")
            provider.finish = anyio.Event()
            provider.entered = anyio.Event()
            await runtime.submit(request("later"))
            await provider.entered.wait()
            with pytest.raises(ExecutionError, match="action_not_active"):
                await runtime.cancel("a")
            assert provider.stops == 0
            assert runtime.lookup("later").state == ActionState.RUNNING
            await runtime.cancel("later")

    anyio.run(scenario)


def test_submission_during_unconfirmed_stop_is_rejected_after_validation():
    async def scenario():
        runtime, provider, _ = setup_runtime()
        stopping, finish_stop = anyio.Event(), anyio.Event()
        validating, finish_validation = anyio.Event(), anyio.Event()

        async def delayed_stop():
            stopping.set()
            await finish_stop.wait()
            return StopResult(False, "unconfirmed")

        async def delayed_validation(_request):
            validating.set()
            await finish_validation.wait()

        async def submit():
            with pytest.raises(ExecutionError, match="stop_unconfirmed"):
                await runtime.submit(request())

        provider.stop = delayed_stop
        provider.validate = delayed_validation
        async with runtime.run(), anyio.create_task_group() as tasks:
            tasks.start_soon(runtime.stop, "robot")
            await stopping.wait()
            tasks.start_soon(submit)
            await validating.wait()
            finish_stop.set()
            while "robot" in runtime._stopping:
                await anyio.lowlevel.checkpoint()
            finish_validation.set()
        assert provider.executions == 0

    anyio.run(scenario)


def test_start_event_authority_revocation_prevents_dispatch():
    async def scenario():
        runtime, provider, stage = setup_runtime()

        def revoke(event):
            if event.type == EventType.ACTION_STARTED:
                provider.allowed = False

        stage.emitter.subscribe_control(revoke)
        async with runtime.run():
            await runtime.submit(request())
            assert (await runtime.wait("a")).state == ActionState.FAILED
            assert provider.executions == 0

    anyio.run(scenario)


def test_blocked_failure_event_does_not_block_shutdown_or_hold_lock():
    async def scenario():
        runtime, provider, stage = setup_runtime(stop_timeout=0.02)
        failed_event = anyio.Event()
        provider.finish.set()
        provider.fail_execution = True

        async def block(event):
            if event.type == EventType.ACTION_FAILED:
                failed_event.set()
                await anyio.sleep_forever()

        stage.emitter.subscribe_control(block)
        with anyio.fail_after(1):
            async with runtime.run():
                await runtime.submit(request())
                await failed_event.wait()
        assert not stage._resource_locks[("robot", "motion")].locked()
        assert runtime.event_errors == ["failure_event_timeout"]

    anyio.run(scenario)
