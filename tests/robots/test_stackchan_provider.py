import threading
from types import SimpleNamespace

import anyio
import pytest

pytest.importorskip("stackchan_control.external_control")

from forge_puppeteer.devices.stackchan import StackChanProvider
from forge_puppeteer.execution import ActionRequest

pytestmark = pytest.mark.anyio

ALL_TOOLS = frozenset({
    "device_info", "device_state", "list_assets", "action_lookup",
    "head_move", "face_update", "lights_update", "neohex_update",
    "play_asset", "device_stop", "action_cancel",
})


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Fake:
    def __init__(self):
        self.calls = []
        self.capabilities = [
            "system.state",
            "avatar.update",
            "motion.update.position_only",
            "lights.update",
            "external_lights.update",
        ]
        self.terminal = "completed"
        self.generation = 0

    def _generation(self):
        return self.generation, threading.Event()

    def discover(self):
        return {"capabilities": self.capabilities}

    def state(self):
        return {"native_angle_limits_tenths": {"yaw": [-500, 500], "pitch": [30, 870]}}

    def _event(self, name, update=None):
        self.calls.append((name, update))
        return (SimpleNamespace(event=self.terminal),)

    def update_face(self, update, *, _generation=None):
        if _generation != self.generation:
            raise RuntimeError("action invalidated by stop")
        return self._event("face", update)

    def move_head(self, update, *, _generation=None):
        if _generation != self.generation:
            raise RuntimeError("action invalidated by stop")
        return self._event("head", update)

    def update_lights(self, update, *, _generation=None):
        if _generation != self.generation:
            raise RuntimeError("action invalidated by stop")
        return self._event("lights", update)

    def update_neohex(self, frame):
        return self._event("neohex", frame)

    def stop(self):
        self.generation += 1
        return self._event("stop")


class Client:
    closed = False

    def close(self):
        self.closed = True


def request(operation, parameters=None, **kw):
    return ActionRequest(
        id="a",
        performer_id="robot",
        caller_id="c",
        session_id="s",
        operation=operation,
        parameters=parameters or {},
        **kw,
    )


async def test_connection_controls_and_completion():
    fake, client = Fake(), Client()
    provider = StackChanProvider("robot", connector=lambda *a, **k: (client, fake),
        allowed_tools=ALL_TOOLS)
    assert not (await provider.describe()).ready
    await provider.connect("fake")
    assert (await provider.describe()).ready
    face = request("face", {"mouth": {"size": 12}})
    await provider.validate(face)
    assert "stackchan.serialized" in provider.resources_for(face)
    result = await provider.execute(face)
    assert "not measured" in result.evidence
    assert [c[0] for c in fake.calls] == ["face"]
    await provider.close()
    assert client.closed
    with pytest.raises(RuntimeError):
        await provider.validate(face)


async def test_validation_fail_closed_and_stop():
    fake = Fake()
    provider = StackChanProvider("robot", connector=lambda *a, **k: (Client(), fake),
        allowed_tools=ALL_TOOLS)
    await provider.connect("fake")
    for action in [
        request("head", {"yaw": {"angle": 501, "speed": 1}}),
        request("face", {"mouth": {"size": True}}),
        request("face", {"mouth": {"size": 1}, "yaw": {}}),
        request("unknown"),
    ]:
        with pytest.raises((ValueError, TypeError)):
            await provider.validate(action)
    fake.capabilities.remove("avatar.update")
    with pytest.raises(ValueError):
        await provider.validate(request("face", {"mouth": {"size": 1}}))
    fake.terminal = "failed"
    assert not (await provider.stop()).confirmed
    fake.terminal = "completed"
    assert (await provider.stop()).confirmed
    assert [c[0] for c in fake.calls] == ["stop", "stop"]


async def test_asset_revision_and_pack_mutation(tmp_path):
    path = tmp_path / "pose.json"
    path.write_text(
        '{"schema_version":1,"id":"smile","kind":"pose","steps":[{"at_s":0,"operation":"face","parameters":{"mouth":{"size":2}}}]}'
    )
    fake = Fake()
    provider = StackChanProvider(
        "robot", asset_directory=tmp_path, connector=lambda *a, **k: (Client(), fake),
        allowed_tools=ALL_TOOLS,
    )
    (asset,) = await provider.list_assets()
    await provider.connect("fake")
    action = request("play_asset", asset_id=asset.id, asset_revision=asset.revision)
    await provider.validate(action)
    assert "face.mouth" in provider.resources_for(action)
    path.write_text(path.read_text().replace('"size":2', '"size":3'))
    with pytest.raises(ValueError):
        await provider.execute(action)
    assert fake.calls == []


async def test_stop_during_provider_preparation_invalidates_dispatch():
    fake, client = Fake(), Client()
    provider = StackChanProvider("robot", connector=lambda *a, **k: (client, fake),
        allowed_tools=ALL_TOOLS)
    await provider.connect("fake")
    started, proceed = anyio.Event(), anyio.Event()
    errors = []

    async def slow_validation(_request):
        started.set()
        await proceed.wait()

    provider.validate = slow_validation

    async def execute():
        try:
            await provider.execute(request("face", {"mouth": {"size": 12}}))
        except RuntimeError as exc:
            errors.append(exc)

    async with anyio.create_task_group() as group:
        group.start_soon(execute)
        await started.wait()
        assert (await provider.stop()).confirmed
        proceed.set()
    assert len(errors) == 1 and "invalidated by stop" in str(errors[0])
    assert not any(call[0] == "face" for call in fake.calls)
    await provider.close()


async def test_operator_grants_and_live_capabilities_are_both_required():
    fake, client = Fake(), Client()
    provider = StackChanProvider(
        "robot", connector=lambda *a, **k: (client, fake),
        allowed_tools=frozenset({"head_move", "device_stop"}),
    )
    assert not await provider.permitted("head_move")
    await provider.connect("fake")
    assert await provider.permitted("head_move")
    assert not await provider.permitted("face_update")
    fake.capabilities.remove("motion.update.position_only")
    assert not await provider.permitted("head_move")
    assert await provider.permitted("device_stop")
    await provider.close()


async def test_close_rejects_abandoned_worker_until_it_really_finishes():
    fake, client = Fake(), Client()
    provider = StackChanProvider("robot", connector=lambda *a, **k: (client, fake),
        allowed_tools=ALL_TOOLS)
    await provider.connect("fake")
    entered, release = threading.Event(), threading.Event()
    scope = anyio.CancelScope()

    def blocked_face(update, *, _generation=None):
        entered.set()
        assert release.wait(2)
        return (SimpleNamespace(event="completed"),)

    fake.update_face = blocked_face

    async def run_action():
        with scope:
            await provider.execute(request("face", {"mouth": {"size": 12}}))

    async with anyio.create_task_group() as group:
        group.start_soon(run_action)
        assert await anyio.to_thread.run_sync(entered.wait, 1)
        scope.cancel()
    assert (await provider.stop()).confirmed
    with pytest.raises(RuntimeError, match="in progress"):
        await provider.close()
    assert not client.closed
    release.set()
    with anyio.fail_after(1):
        while provider._active_operations:
            await anyio.sleep(0.01)
    await provider.close()
    assert client.closed


async def test_configured_grants_are_enforced_for_python_callers():
    fake = Fake()
    provider = StackChanProvider(
        "robot", connector=lambda *a, **k: (Client(), fake),
        allowed_tools=frozenset({"device_info", "device_state"}),
    )
    await provider.connect("fake")
    with pytest.raises(PermissionError, match="operator grants"):
        await provider.validate(request("head", {"yaw": {"angle": 10, "speed": 1}}))
    with pytest.raises(PermissionError, match="operator grants"):
        await provider.execute(request("face", {"mouth": {"size": 10}}))
    assert fake.calls == []
    await provider.close()


async def test_effective_discovery_uses_firmware_and_operator_grants():
    fake = Fake()
    provider = StackChanProvider(
        "robot", connector=lambda *a, **k: (Client(), fake),
        allowed_tools=frozenset({"device_info", "head_move", "neohex_update"}),
    )
    await provider.connect("fake")
    description = await provider.describe()
    controls = {control.id: control for control in description.controls}
    assert set(controls) == {"head", "neohex"}
    assert "yaw" in controls["head"].parameters_schema["properties"]
    assert "yawServo" not in controls["head"].parameters_schema["properties"]
    assert controls["neohex"].resources == ("lights.neohex",)
    assert not await provider.permitted("face_update")
    assert await provider.permitted("neohex_update")
    fake.capabilities.remove("external_lights.update")
    assert not await provider.permitted("neohex_update")
    assert "neohex" not in (await provider.describe()).supported_operations
    await provider.close()


async def test_neohex_exact_frame_and_firmware_completion():
    fake = Fake()
    provider = StackChanProvider(
        "robot", connector=lambda *a, **k: (Client(), fake),
        allowed_tools=ALL_TOOLS,
    )
    await provider.connect("fake")
    action = request("neohex", {
        "pixels": ["#00ff55"] * 37, "brightness": 12,
    })
    await provider.validate(action)
    await provider.execute(action)
    assert fake.calls[-1][0] == "neohex"
    assert fake.calls[-1][1].brightness == 12
    assert len(fake.calls[-1][1].pixels) == 37
    bad = request("neohex", {"pixels": ["#ffffff"] * 36, "brightness": 12})
    with pytest.raises(ValueError, match="37"):
        await provider.validate(bad)
    await provider.close()


async def test_real_sdk_asset_player_accepts_puppeteer_broker_call(tmp_path):
    path = tmp_path / "pose.json"
    path.write_text(
        '{"schema_version":1,"id":"smile","kind":"pose","steps":'
        '[{"at_s":0,"operation":"face","parameters":{"mouth":{"size":2}}}]}'
    )
    fake = Fake()
    provider = StackChanProvider(
        "robot", asset_directory=tmp_path,
        connector=lambda *a, **k: (Client(), fake),
        allowed_tools=ALL_TOOLS,
    )
    (asset,) = await provider.list_assets()
    await provider.connect("fake")
    result = await provider.execute(request(
        "play_asset", asset_id=asset.id, asset_revision=asset.revision,
    ))
    assert "not measured" in result.evidence
    assert [name for name, _ in fake.calls] == ["face"]
    await provider.close()
