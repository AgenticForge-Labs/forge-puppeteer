"""Reachy packaged launcher lifecycle tests; SDK is always a fake."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from forge_puppeteer.mcp.launcher import ReachySDKProvider, ServerConfig

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class FakeRobot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.calls = []

    def __exit__(self, *_args):
        self.closed = True

    async def async_play_move(self, move, *, sound=False):
        self.calls.append((move.description, sound))

    def cancel_move(self):
        self.calls.append("cancel")


class Move:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]
        self.sound_path = sound_path


def make_snapshot(root):
    root.mkdir()
    payload = json.dumps({"description": "hello", "duration": 0.2}).encode()
    (root / "hello.json").write_bytes(payload)
    manifest = {
        "version": 1, "dataset_revision": "fixture", "sdk_version": "1.10.0rc5",
        "source_url": "fixture", "license": "fixture", "notices": "fixture",
        "files": {"hello.json": hashlib.sha256(payload).hexdigest()},
    }
    return manifest


def config(**overrides):
    values = {
        "backend": "reachy_sdk", "device": "reachy", "instance_id": "reachy",
        "asset_root": "/fixture", "manifest": "/fixture/manifest.json",
        "physical_authority": "I_AUTHORIZE_REACHY_CONTROL", "sdk_version": "1.10.0rc5",
        "host": "127.0.0.1", "daemon_port": 8000, "connection_mode": "localhost_only",
        "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup", "play_move", "device_stop"],
        "ownership_db": "/tmp/forge-puppeteer-test-ownership.sqlite3",
        "action_journal": "/tmp/forge-puppeteer-test-actions.sqlite3",
    }
    values.update(overrides)
    return ServerConfig(**values)


def test_reachy_launcher_rejects_implicit_or_unsafe_configuration(monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.10.0rc5")
    for changes, message in [
        ({"physical_authority": None}, "physical_authority"),
        ({"host": None}, "explicit host"),
        ({"connection_mode": "auto"}, "connection_mode"),
        ({"host": "reachy-mini.local"}, "loopback"),
        ({"allowed_tools": ["raw_command"]}, "unsupported Reachy"),
        ({"sdk_version": "1.10.0"}, "sdk_version"),
    ]:
        with pytest.raises(ValueError, match=message):
            ReachySDKProvider(config(**changes), catalog_factory=lambda *_: None)


def test_restricted_reachy_config_requires_exact_operator_asset_pins():
    revision = "sha256:" + "a" * 64
    base = {
        "restricted_actions": True,
        "approved_moves": [{"asset_id": "reachy/curious", "revision": revision}],
    }
    assert config(**base).restricted_actions is True
    for changes, message in [
        ({"restricted_actions": False}, "requires restricted_actions=true"),
        ({"approved_moves": []}, "requires at least one approved move"),
        ({"allowed_tools": ["device_info", "head_pose"]}, "play_move"),
        ({"allowed_tools": ["device_info", "list_assets", "play_move", "device_stop"]}, "action_lookup"),
        ({"allowed_tools": ["device_info", "list_assets", "action_lookup", "play_move"]}, "device_stop"),
        ({"allowed_tools": ["device_info", "action_lookup", "play_move", "device_stop"]}, "list_assets"),
        ({"allowed_tools": ["device_info", "list_assets", "action_lookup", "play_move", "device_stop", "head_pose"]}, "cannot enable arbitrary"),
        ({"ownership_db": None}, "absolute ownership_db"),
        ({"action_journal": None}, "absolute action_journal"),
        ({"ownership_db": "relative/owners.sqlite3"}, "absolute ownership_db"),
        ({"action_journal": "relative/actions.sqlite3"}, "absolute action_journal"),
        ({"approved_moves": [{"asset_id": "curious", "revision": revision}]}, "reachy/<asset>"),
        ({"approved_moves": [{"asset_id": "reachy/curious", "revision": "stale"}]}, "revision"),
        ({"approved_moves": [
            {"asset_id": "reachy/curious", "revision": revision},
            {"asset_id": "reachy/curious", "revision": revision},
        ]}, "duplicate asset pins"),
    ]:
        with pytest.raises(ValueError, match=message):
            config(**(base | changes))


@pytest.mark.anyio
async def test_reachy_lifecycle_uses_fixed_sdk_options_and_closes(monkeypatch, tmp_path):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.10.0rc5")
    robot = FakeRobot()
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return robot

    from forge_puppeteer.devices.reachy import ReachyCatalog

    root = tmp_path / "assets"
    manifest = make_snapshot(root)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    provider = ReachySDKProvider(config(asset_root=str(root), manifest=str(manifest_path)), robot_factory=factory,
        catalog_factory=lambda path, _manifest: ReachyCatalog(path, manifest, move_factory=Move))
    with pytest.raises(RuntimeError, match="not active"):
        await provider.describe()
    async with provider.connected():
        assert created == [{"host": "127.0.0.1", "port": 8000,
            "connection_mode": "localhost_only", "spawn_daemon": False,
            "use_sim": False, "media_backend": "no_media"}]
        assert await provider.permitted("play_move")
        assert not await provider.permitted("antenna_targets")
        assets = await provider.list_assets()
        from forge_puppeteer.execution import ActionRequest

        request = ActionRequest(id="a", performer_id="reachy", caller_id="test", session_id="s",
            operation="play_move", asset_id=assets[0].id, asset_revision=assets[0].revision)
        result = await provider.execute(request)
        assert "physical settling unmeasured" in result.evidence
        assert robot.calls == [("hello", False)]
    assert robot.closed
    with pytest.raises(RuntimeError, match="not active"):
        await provider.describe()


@pytest.mark.anyio
async def test_restricted_reachy_adapter_allows_only_exact_pinned_moves(monkeypatch, tmp_path):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.10.0rc5")
    assets = tmp_path / "assets"
    manifest = make_snapshot(assets)
    other_payload = json.dumps({"description": "not-approved", "duration": 0.1}).encode()
    (assets / "other.json").write_bytes(other_payload)
    manifest["files"]["other.json"] = hashlib.sha256(other_payload).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    from forge_puppeteer.devices.reachy import ReachyCatalog
    from forge_puppeteer.execution import ActionRequest

    catalog_factory = lambda path, locked: ReachyCatalog(path, locked, move_factory=Move)
    catalog = catalog_factory(assets, manifest)
    asset = (await catalog.list_assets())[0]
    robot = FakeRobot()
    provider = ReachySDKProvider(
        config(
            asset_root=str(assets), manifest=str(manifest_path),
            restricted_actions=True,
            approved_moves=[{"asset_id": asset.id, "revision": asset.revision}],
        ),
        robot_factory=lambda **_kwargs: robot,
        catalog_factory=catalog_factory,
    )

    async with provider.connected():
        assert await provider.permitted("play_move")
        assert not await provider.permitted("head_pose")
        assert [item.id for item in await provider.list_assets()] == [asset.id]
        approved = ActionRequest(
            id="approved", performer_id="reachy", caller_id="test", session_id="s",
            operation="play_move", asset_id=asset.id, asset_revision=asset.revision,
        )
        await provider.execute(approved)
        assert robot.calls == [("hello", False)]

        unapproved = approved.model_copy(update={
            "id": "unapproved", "asset_id": "reachy/other",
        })
        with pytest.raises(PermissionError, match="operator-approved"):
            await provider.execute(unapproved)

        arbitrary_head = approved.model_copy(update={
            "id": "head", "operation": "head_pose", "asset_id": None,
            "asset_revision": None,
        })
        with pytest.raises(PermissionError, match="only operator-approved"):
            await provider.execute(arbitrary_head)
        assert robot.calls == [("hello", False)]


def test_restricted_reachy_launcher_rejects_stale_pin_before_sdk_factory(monkeypatch, tmp_path):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.10.0rc5")
    assets = tmp_path / "assets"
    manifest = make_snapshot(assets)
    other_payload = json.dumps({"description": "not-approved", "duration": 0.1}).encode()
    (assets / "other.json").write_bytes(other_payload)
    manifest["files"]["other.json"] = hashlib.sha256(other_payload).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    opened = []

    from forge_puppeteer.devices.reachy import ReachyCatalog

    def robot_factory(**kwargs):
        opened.append(kwargs)
        return FakeRobot()

    with pytest.raises(ValueError, match="asset revision mismatch"):
        ReachySDKProvider(
            config(
                asset_root=str(assets), manifest=str(manifest_path),
                restricted_actions=True,
                approved_moves=[{"asset_id": "reachy/hello", "revision": "sha256:" + "0" * 64}],
            ),
            robot_factory=robot_factory,
            catalog_factory=lambda path, locked: ReachyCatalog(path, locked, move_factory=Move),
        )
    assert opened == []


@pytest.mark.anyio
async def test_restricted_reachy_runtimes_share_motion_arbitration(tmp_path):
    import anyio

    from forge_puppeteer.execution import (
        ActionRequest,
        Completion,
        ExecutionError,
        ExecutionRuntime,
        StopResult,
    )
    from forge_puppeteer.runtime import PerformanceStage

    class PendingProvider:
        def __init__(self, *, pending=False):
            self.entered = anyio.Event()
            self.finish = anyio.Event()
            self.pending = pending

        async def validate(self, _request):
            return None

        def resources_for(self, _request):
            return ("reachy.motion",)

        async def execute(self, _request):
            self.entered.set()
            if self.pending:
                await self.finish.wait()
            return Completion("fake completed")

        async def stop(self):
            return StopResult(True, "fake stop")

    owners = tmp_path / "shared-owners.sqlite3"
    journal = tmp_path / "shared-actions.sqlite3"
    first_provider = PendingProvider(pending=True)
    second_provider = PendingProvider()
    first = ExecutionRuntime(
        PerformanceStage(), ownership_path=str(owners), journal_path=str(journal),
    )
    second = ExecutionRuntime(
        PerformanceStage(), ownership_path=str(owners), journal_path=str(journal),
    )
    first.register("reachy", first_provider)
    second.register("reachy", second_provider)

    def request(action_id):
        return ActionRequest(
            id=action_id, performer_id="reachy", caller_id="conversation",
            session_id=action_id, operation="play_move",
            asset_id="reachy/approved", asset_revision="sha256:" + "a" * 64,
        )

    try:
        async with first.run(), second.run():
            await first.submit(request("first"))
            await first_provider.entered.wait()
            with pytest.raises(ExecutionError, match="performer_busy"):
                await second.submit(request("concurrent"))
            first_provider.finish.set()
            await first.wait("first")
            await second.submit(request("after-completion"))
            await second.wait("after-completion")
    finally:
        first.close()
        second.close()

@pytest.mark.anyio
async def test_reachy_lifecycle_closes_sdk_if_adapter_initialization_fails(monkeypatch, tmp_path):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.10.0rc5")
    root = tmp_path / "assets"
    manifest = make_snapshot(root)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    robot = FakeRobot()
    provider = ReachySDKProvider(
        config(asset_root=str(root), manifest=str(manifest_path)),
        robot_factory=lambda **_kwargs: robot,
        catalog_factory=lambda path, _manifest: __import__(
            "forge_puppeteer.devices.reachy", fromlist=["ReachyCatalog"]
        ).ReachyCatalog(path, manifest, move_factory=Move),
    )

    def fail_adapter(*_args, **_kwargs):
        raise RuntimeError("adapter setup failed")

    provider._provider_type = fail_adapter
    with pytest.raises(RuntimeError, match="adapter setup failed"):
        async with provider.connected():
            pass
    assert robot.closed
    assert provider._robot is None and provider._adapter is None
@pytest.mark.anyio
async def test_unconfirmed_stop_quarantines_reachy_adapter(tmp_path):
    from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider

    root = tmp_path / "assets"
    catalog = ReachyCatalog(root, make_snapshot(root), move_factory=Move)
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(catalog, robot, motion_authorized=True)
    result = await provider.stop()
    assert not result.confirmed
    assert "not confirmed" in result.evidence
    assert "cancel" in robot.calls
    state = await provider.state()
    assert state["quarantined"] is True
    from forge_puppeteer.execution import ActionRequest

    asset = (await catalog.list_assets())[0]
    request = ActionRequest(id="a", performer_id="reachy", caller_id="test", session_id="s",
        operation="play_move", asset_id=asset.id, asset_revision=asset.revision)
    with pytest.raises(RuntimeError, match="quarantined"):
        await provider.validate(request)


@pytest.mark.anyio
async def test_reachy_launcher_stdio_with_fake_sdk_only(tmp_path):
    """Exercise packaged tool filtering/lifecycle through stdio without hardware."""
    root = Path(__file__).resolve().parents[2]
    fake = tmp_path / "fake_sdk"
    (fake / "reachy_mini/motion").mkdir(parents=True)
    played_path = tmp_path / "played"
    (fake / "reachy_mini/__init__.py").write_text('''
class ReachyMini:
    def __init__(self, **kwargs):
        assert kwargs["spawn_daemon"] is False and kwargs["use_sim"] is False
        self.kwargs = kwargs
    def __exit__(self, *_args):
        open("'''+str(tmp_path / "closed")+'''", "w").write("closed")
    async def async_play_move(self, move, *, sound=False):
        with open("'''+str(played_path)+'''", "a") as output:
            output.write(move.description + "\\n")
    def cancel_move(self):
        pass
''')
    (fake / "reachy_mini/motion/__init__.py").write_text("")
    (fake / "reachy_mini/motion/recorded_move.py").write_text('''
class RecordedMove:
    def __init__(self, data, sound_path=None):
        self.description, self.duration, self.sound_path = data["description"], data["duration"], sound_path
''')
    dist = fake / "reachy_mini-1.10.0rc5.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: reachy-mini\nVersion: 1.10.0rc5\n")
    assets = tmp_path / "assets"
    manifest = make_snapshot(assets)
    other_payload = json.dumps({"description": "not-approved", "duration": 0.1}).encode()
    (assets / "other.json").write_bytes(other_payload)
    manifest["files"]["other.json"] = hashlib.sha256(other_payload).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    from forge_puppeteer.deployment import verify_reachy_snapshot

    revision = verify_reachy_snapshot(assets, manifest)
    cfg = tmp_path / "server.json"
    cfg.write_text(json.dumps({
        "backend": "reachy_sdk", "device": "reachy", "instance_id": "reachy",
        "physical_authority": "I_AUTHORIZE_REACHY_CONTROL", "sdk_version": "1.10.0rc5",
        "host": "127.0.0.1", "daemon_port": 8000, "connection_mode": "localhost_only",
        "asset_root": str(assets), "manifest": str(manifest_path),
        "ownership_db": str(tmp_path / "reachy-owners.sqlite3"),
        "action_journal": str(tmp_path / "reachy-actions.sqlite3"),
        "restricted_actions": True,
        "approved_moves": [{"asset_id": "reachy/hello", "revision": revision}],
        "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup", "play_move", "device_stop"],
    }))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(fake), str(root / "src")))}
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(cfg)], env=env)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"device_info", "list_assets", "action_lookup", "play_move", "device_stop"} <= names
        assert "head_pose" not in names and "antenna_targets" not in names
        info = (await session.call_tool("device_info", {})).structured_content
        assert info["sdk_version"] == "1.10.0rc5"
        assert "head_pose" not in info["supported_operations"]
        assert "antenna_targets" not in info["supported_operations"]
        listed = (await session.call_tool("list_assets", {})).structured_content["assets"]
        assert [(asset["id"], asset["revision"]) for asset in listed] == [
            ("reachy/hello", revision),
        ]
        result = await session.call_tool("play_move", {
            "action_id": "fake-move", "caller_id": "operator", "session_id": "stdio",
            "asset_id": listed[0]["id"], "revision": listed[0]["revision"],
        })
        assert not result.is_error
        lookup = await session.call_tool("action_lookup", {"action_id": "fake-move"})
        assert not lookup.is_error
        assert lookup.structured_content["id"] == "fake-move"
        denied = await session.call_tool("play_move", {
            "action_id": "not-approved", "caller_id": "operator", "session_id": "stdio",
            "asset_id": "reachy/other", "revision": listed[0]["revision"],
        })
        assert denied.is_error
        head = await session.call_tool("head_pose", {
            "action_id": "arbitrary-head", "caller_id": "operator", "session_id": "stdio",
            "parameters": {"pose": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]},
        })
        assert head.is_error
    assert (tmp_path / "closed").read_text() == "closed"
    assert played_path.read_text() == "hello\n"
