from types import SimpleNamespace

import anyio
import pytest

from forge_puppeteer.devices.soarm101 import SOARM101Provider
from forge_puppeteer.execution import ActionRequest, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeBroker:
    def __init__(self):
        self.visible = {"robot_capabilities", "robot_state", "list_assets", "go_pose",
                        "jog_cartesian", "sleep", "stop", "replay_recording", "replay_sequence"}
        self.calls = []
        self.asset_items = []

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in self.visible])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = {"authority": {"motion": "active"}, "assets": [],
                  "broker_session": {"mode": "simulation_actions", "id": "sdk-session"}}
        if name == "list_assets":
            result = {"items": self.asset_items, "next_offset": None}
        if name in {"go_pose", "jog_cartesian", "sleep", "replay_recording", "replay_sequence"}:
            result.update(accepted=True, completed=True)
        elif name == "stop":
            result.update(completed=True, holding=True)
        return SimpleNamespace(is_error=False, structured_content={"result": result})


async def test_controls_only_forward_to_live_visible_broker_tools():
    broker = FakeBroker()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    assert await provider.permitted("go_pose")
    await provider.go_pose("ready", speed_deg_s=10)
    assert broker.calls == [("go_pose", {"name": "ready", "speed_deg_s": 10.0})]
    broker.visible.remove("go_pose")
    assert not await provider.permitted("go_pose")
    with pytest.raises(PermissionError, match="broker_tool_unavailable"):
        await provider.go_pose("ready")
    assert len(broker.calls) == 1


async def test_typed_cartesian_and_stop_are_broker_only():
    broker = FakeBroker()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    await provider.jog_cartesian(frame="tool", x_mm=2, z_mm=-1)
    await provider.stop()
    assert broker.calls == [
        ("jog_cartesian", {"frame": "tool", "x_mm": 2.0, "y_mm": 0.0, "z_mm": -1.0}),
        ("stop", {}),
    ]
    with pytest.raises(ValueError):
        await provider.jog_cartesian(frame="camera", x_mm=2)


async def test_discovery_never_claims_authority_without_live_broker_tool():
    broker = FakeBroker()
    broker.visible.clear()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset({"robot_capabilities"}))
    descriptor = await provider.describe()
    assert not descriptor.connected and not descriptor.ready
    assert not broker.calls


async def test_asset_pages_follow_broker_next_offset_and_convert_schema():
    broker = FakeBroker()
    pages = [
        {"items": [{"id": "soarm101/pose/a", "kind": "pose", "source": "pose-library",
                    "revision": "r1", "description": "Saved pose", "duration_s": None,
                    "resources": ["arm.motion"], "provenance": [["robot_id", "so101"]],
                    "compatibility": "calibration_matched", "compatibility_detail": "matches"}],
         "offset": 0, "next_offset": 1},
        {"items": [{"id": "soarm101/sequence/b", "kind": "sequence", "source": "sequence-library",
                    "revision": "r2", "description": "Sequence", "duration_s": None,
                    "resources": ["arm.motion"], "provenance": [],
                    "compatibility": "unchecked", "compatibility_detail": "not checked"}],
         "offset": 1, "next_offset": None},
    ]

    async def call_tool(name, arguments):
        broker.calls.append((name, arguments))
        return SimpleNamespace(is_error=False, structured_content={"result": pages.pop(0)})

    broker.call_tool = call_tool
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    assets = await provider.list_assets()
    assert [asset.id for asset in assets] == ["soarm101/pose/a", "soarm101/sequence/b"]
    assert assets[0].device_compatibility == {
        "calibration": "calibration_matched", "detail": "matches", "provenance": {"robot_id": "so101"}
    }
    assert broker.calls == [
        ("list_assets", {"offset": 0, "limit": 100}),
        ("list_assets", {"offset": 1, "limit": 100}),
    ]


async def test_execution_runtime_uses_live_broker_authority_and_independent_stop():
    broker = FakeBroker()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    request = ActionRequest(
        id="pose-1", performer_id="arm", caller_id="caller", session_id="session",
        operation="go_pose", parameters={"name": "ready", "speed_deg_s": 10},
    )
    async with runtime.run():
        async with anyio.create_task_group() as group:
            result = {}

            async def execute():
                result["record"] = await runtime.submit(request)
                result["record"] = await runtime.wait(request.id)

            group.start_soon(execute)
            await anyio.sleep(0.01)
        assert result["record"].state.value == "completed"
        assert result["record"].completion_evidence == "broker_completed"
        assert await provider.stop() == StopResult(True, "broker_stop_completed_and_holding")
    broker.visible.remove("go_pose")
    with pytest.raises(PermissionError):
        await provider.validate(request)


@pytest.mark.parametrize(
    "result",
    [
        {"accepted": True, "completed": False, "completion_evidence": "still running"},
        {"accepted": True, "completion_evidence": "command accepted"},
        {"status": "failed", "completed": True, "completion_evidence": "failed"},
    ],
)
async def test_action_evidence_without_successful_terminal_result_is_not_completion(result):
    broker = FakeBroker()

    async def call_tool(name, arguments):
        broker.calls.append((name, arguments))
        return SimpleNamespace(is_error=False, structured_content={"result": result})

    broker.call_tool = call_tool
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    request = ActionRequest(
        id="pose-unconfirmed", performer_id="arm", caller_id="caller", session_id="session",
        operation="go_pose", parameters={"name": "ready"},
    )
    with pytest.raises(RuntimeError, match="broker_completion_unconfirmed"):
        await provider.execute(request)


async def test_runtime_cancellation_waits_for_broker_stop_confirmation():
    broker = FakeBroker()
    broker.started = anyio.Event()
    broker.release = anyio.Event()

    async def delayed_call_tool(name, arguments):
        broker.calls.append((name, arguments))
        if name == "go_pose":
            broker.started.set()
            await broker.release.wait()
            result = {"accepted": True, "completed": True}
        elif name == "stop":
            result = {"completed": True, "holding": True}
        else:
            result = {"authority": {"motion": "active"}}
        return SimpleNamespace(is_error=False, structured_content={"result": result})

    broker.call_tool = delayed_call_tool
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    request = ActionRequest(
        id="pose-cancel", performer_id="arm", caller_id="caller", session_id="session",
        operation="go_pose", parameters={"name": "ready"},
    )
    async with runtime.run():
        accepted = await runtime.submit(request)
        assert accepted.state.value == "queued"
        await broker.started.wait()
        stopped = await runtime.stop("arm")
        assert stopped.confirmed
        assert (await runtime.wait(request.id)).state.value == "cancelled"


async def test_pinned_replay_requires_simulation_and_forwards_exact_revision():
    broker = FakeBroker()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible),
                                simulation_only=True)
    revision = "sha256:" + "a" * 64
    broker.asset_items = [{"id": "soarm101/sequence/demo", "kind": "sequence",
                           "source": "sequence-library", "revision": revision,
                           "resources": ["arm.motion"], "provenance": [],
                           "compatibility": "unchecked", "compatibility_detail": ""}]
    request = ActionRequest(
        id="replay-1", performer_id="arm", caller_id="caller", session_id="forge-session",
        operation="replay_sequence", parameters={"speed_scale": 0.5},
        asset_id="soarm101/sequence/demo", asset_revision=revision,
    )
    await provider.validate(request)
    completion = await provider.execute(request)
    assert completion.evidence == "broker_replay_completed"
    assert broker.calls[-2:] == [
        ("robot_state", {}),
        ("replay_sequence", {"request_id": "replay-1", "session_id": "sdk-session",
         "asset_id": "soarm101/sequence/demo", "revision": revision, "speed_scale": 0.5}),
    ]
    physical = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    with pytest.raises(PermissionError, match="simulation-only"):
        await physical.validate(request)


async def test_recording_replay_accepts_sdk_nested_terminal_completion():
    broker = FakeBroker()
    revision = "sha256:" + "c" * 64
    asset_id = "soarm101/recording/raw/demo"
    broker.asset_items = [{"id": asset_id, "kind": "recording",
                           "source": "trajectory-library/raw", "revision": revision,
                           "resources": ["arm.motion"], "provenance": [],
                           "compatibility": "unchecked", "compatibility_detail": ""}]

    async def recording_result(name, arguments):
        broker.calls.append((name, arguments))
        if name == "robot_state":
            result = {"broker_session": {"mode": "simulation_actions", "id": "sdk-session"}}
        elif name == "list_assets":
            result = {"items": broker.asset_items, "next_offset": None}
        elif name == "replay_recording":
            result = {"asset_id": asset_id, "revision": revision,
                      "completion": {"accepted": True, "completed": True,
                                     "final_positions": {}}}
        else:
            raise AssertionError(name)
        return SimpleNamespace(is_error=False, structured_content={"result": result})

    broker.call_tool = recording_result
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible),
                                simulation_only=True)
    request = ActionRequest(
        id="recording-replay", performer_id="arm", caller_id="caller",
        session_id="forge-session", operation="replay_recording",
        parameters={"speed_scale": 1.0}, asset_id=asset_id, asset_revision=revision,
    )
    completion = await provider.execute(request)
    assert completion.evidence == "broker_recording_replay_completed"


async def test_replay_without_terminal_success_stays_unconfirmed():
    broker = FakeBroker()

    async def failed_replay(name, arguments):
        broker.calls.append((name, arguments))
        result = {"broker_session": {"mode": "simulation_actions", "id": "sdk-session"}}
        if name == "list_assets":
            result = {"items": broker.asset_items, "next_offset": None}
        elif name == "replay_sequence":
            result = {"completed": False}
        return SimpleNamespace(is_error=False, structured_content={"result": result})

    broker.call_tool = failed_replay
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible),
                                simulation_only=True)
    request = ActionRequest(
        id="replay-fail", performer_id="arm", caller_id="caller", session_id="forge-session",
        operation="replay_sequence", parameters={"speed_scale": 0.5},
        asset_id="soarm101/sequence/demo", asset_revision="sha256:" + "b" * 64,
    )
    broker.asset_items = [{"id": request.asset_id, "kind": "sequence",
                           "source": "sequence-library", "revision": request.asset_revision,
                           "resources": ["arm.motion"], "provenance": [],
                           "compatibility": "unchecked", "compatibility_detail": ""}]
    with pytest.raises(RuntimeError, match="broker_completion_unconfirmed"):
        await provider.execute(request)


async def test_camera_image_digest_is_checked_before_puppeteer_mcp_can_expose_pixels():
    import base64
    import hashlib

    broker = FakeBroker()
    broker.visible.add("capture_camera")
    expected = b"synthetic-test-only-jpeg"
    encoded = base64.b64encode(expected).decode("ascii")
    digest = hashlib.sha256(expected).hexdigest()
    altered = False

    async def capture(name, arguments):
        nonlocal altered
        broker.calls.append((name, arguments))
        return SimpleNamespace(is_error=False, structured_content={
            "request_id": "camera-request-1",
            "result": {
                "name": "overhead", "sha256": "0" * 64 if altered else digest,
                "image_base64": encoded, "width": 10, "height": 10,
                "source_path": "/private/camera",
            },
        })

    broker.call_tool = capture
    provider = SOARM101Provider(
        "arm", broker, allowed_tools=frozenset(broker.visible),
    )
    meta, image = await provider.capture_camera("overhead")
    assert image == encoded
    assert meta == {
        "name": "overhead", "sha256": digest,
        "width": 10, "height": 10, "request_id": "camera-request-1",
    }
    assert "source_path" not in meta
    assert broker.calls[-1] == ("capture_camera", {"camera": "overhead"})
    altered = True
    with pytest.raises(RuntimeError, match="digest_mismatch"):
        await provider.capture_camera("overhead")


async def test_signed_joint_moves_and_native_gripper_sleepup_use_broker_only():
    broker = FakeBroker()
    broker.visible.update({"jog_joint", "move_gripper", "sleep_up"})
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    assert await provider.permitted("move_gripper")
    with pytest.raises(ValueError):
        await provider.jog_joint(joint="shoulder_pan", delta_deg=3)
    await provider.jog_joint(joint="shoulder_pan", delta_deg=-0.5)
    await provider.move_gripper("open", gripper_speed_raw=100)
    await provider.sleep_up()
    assert broker.calls == [
        ("jog_joint", {"joint": "shoulder_pan", "delta_deg": -0.5}),
        ("move_gripper", {"target": "open", "gripper_speed_raw": 100}),
        ("sleep_up", {}),
    ]


async def test_stop_status_label_without_holding_does_not_release_motion_quarantine():
    broker = FakeBroker()

    async def weak_stop(name, arguments):
        broker.calls.append((name, arguments))
        return SimpleNamespace(is_error=False, structured_content={
            "result": {"status": "stopped", "completed": True, "holding": False},
        })

    broker.call_tool = weak_stop
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    result = await provider.stop()
    assert result.confirmed is False
    assert "stopped" in result.evidence


async def test_simulation_mode_cannot_be_cached_across_broker_restart():
    broker = FakeBroker()
    current_mode = "simulation_actions"

    async def dynamic_broker(name, arguments):
        broker.calls.append((name, arguments))
        if name == "robot_state":
            result = {"broker_session": {"mode": current_mode, "id": "session-rotated"}}
        elif name == "robot_capabilities":
            result = {"authority": {"armed": False}}
        else:
            result = {"completed": True}
        return SimpleNamespace(is_error=False, structured_content={"result": result})

    broker.call_tool = dynamic_broker
    provider = SOARM101Provider(
        "arm", broker, allowed_tools=frozenset(broker.visible),
        simulation_only=True,
    )
    await provider.describe()
    current_mode = "physical"
    action = ActionRequest(
        id="restart-guard", performer_id="arm", caller_id="agent",
        session_id="session", operation="go_pose", parameters={"name": "ready"},
    )
    with pytest.raises(PermissionError, match="did not prove simulation_actions"):
        await provider.validate(action)
    assert not any(name == "go_pose" for name, _ in broker.calls)
