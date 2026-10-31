"""Packaged Reachy tools roundtrip through actual MCP stdio with a fake SDK."""

import hashlib
import json
import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from forge_puppeteer.mcp.client import (
    Cue,
    MCPClient,
    PreflightError,
    Script,
    ServerConfig,
    ToolBinding,
    compile_script,
)


def fixture_pack(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "hello.json").write_text(json.dumps({"description": "hello", "duration": 1.0}))
    (assets / "hello.wav").write_bytes(b"fixture-audio")
    manifest = {
        "version": 1, "dataset_revision": "fixture", "sdk_version": "1.10.0rc5",
        "source_url": "https://example.org", "license": "fixture", "notices": "fixture",
        "files": {
            name: hashlib.sha256((assets / name).read_bytes()).hexdigest()
            for name in ("hello.json", "hello.wav")
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))


async def server_session(tmp_path, authorization="authorized", failure=""):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    params = StdioServerParameters(command=sys.executable, env=env, args=[
        str(Path(__file__).with_name("reachy_mcp_fake_server.py")), str(tmp_path), authorization, failure,
    ])
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        yield session


async def wait_action(session, action_id):
    with anyio.fail_after(3):
        while True:
            action = (await session.call_tool("action_lookup", {"action_id": action_id})).structured_content
            if action["state"] in {"completed", "failed", "unknown", "cancelled"}:
                return action
            await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_reachy_stdio_discovery_playback_native_stop_and_unknown_stop(tmp_path):
    fixture_pack(tmp_path)
    async for session in server_session(tmp_path):
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {"device_info", "list_assets", "play_move", "head_pose", "antenna_targets", "device_stop"} <= tools
        info = (await session.call_tool("device_info", {})).structured_content
        assert "play_move" in info["supported_operations"]
        assets = (await session.call_tool("list_assets", {})).structured_content["assets"]
        assert [a["id"] for a in assets] == ["reachy/hello"]
        move = await session.call_tool("play_move", {"action_id": "play", "caller_id": "c", "session_id": "s",
            "asset_id": "reachy/hello", "revision": assets[0]["revision"]})
        assert not move.is_error
        action = await wait_action(session, "play")
        assert action["state"] == "completed"
        assert "settling unmeasured" in action["completion_evidence"]
        pose = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        native = await session.call_tool("head_pose", {"action_id": "head", "caller_id": "c", "session_id": "s",
            "parameters": {"pose": pose}})
        assert not native.is_error
        assert (await wait_action(session, "head"))["state"] == "completed"
        antennas = await session.call_tool("antenna_targets", {"action_id": "ant", "caller_id": "c", "session_id": "s",
            "parameters": {"angles_rad": [0.2, -0.2]}})
        assert not antennas.is_error
        assert (await wait_action(session, "ant"))["state"] == "completed"
        stopped = (await session.call_tool("device_stop", {})).structured_content
        assert stopped["confirmed"] is False
        assert "not confirmed" in stopped["evidence"]


@pytest.mark.anyio
async def test_reachy_behavior_pack_executes_exact_staged_asset_over_stdio(tmp_path):
    fixture_pack(tmp_path)
    async for session in server_session(tmp_path):
        asset = (await session.call_tool("list_assets", {})).structured_content["assets"][0]
    example = Path(__file__).resolve().parents[2] / "examples/robots/reachy/behavior-packs.json"
    document = json.loads(example.read_text())
    document["packs"][0]["cues"][0]["asset_revision"] = asset["revision"]
    manifest = tmp_path / "behavior-packs.json"
    manifest.write_text(json.dumps(document))
    revision = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[2]
    config = ServerConfig(command=sys.executable, args=(
        str(Path(__file__).with_name("reachy_mcp_fake_server.py")), str(tmp_path),
    ), env={**os.environ, "PYTHONPATH": str(root / "src")})
    client = MCPClient({"reachy": config}, behavior_packs_path=manifest)
    plan = client.compile_behavior_pack(
        "curious-recording", revision, "reachy-pack-take", server="reachy",
    )
    assert plan.cues[0].asset_id == "reachy/hello"
    assert plan.cues[0].asset_revision == asset["revision"]
    results = await client.execute(plan)
    assert results[0]["cue_id"] == "curious-recording:0"
    assert not results[0]["is_error"]


@pytest.mark.anyio
async def test_reachy_independent_catalogs_audio_sidecars_and_mutation_over_stdio(tmp_path):
    """Each subprocess discovers its own lock; stale pins and sidecar drift fail closed."""
    first = tmp_path / "upstream-fixture"
    second = tmp_path / "custom-fixture"
    first.mkdir()
    second.mkdir()
    fixture_pack(first)
    fixture_pack(second)
    (second / "assets" / "hello.json").unlink()
    (second / "assets" / "hello.wav").unlink()
    (second / "assets" / "custom.json").write_text(
        json.dumps({"description": "custom", "duration": 2.0})
    )
    (second / "assets" / "custom.wav").write_bytes(b"custom-audio")
    lock_path = second / "manifest.json"
    lock = json.loads(lock_path.read_text())
    lock["files"] = {
        path.relative_to(second / "assets").as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (second / "assets").iterdir()
    }
    lock["dataset_revision"] = "custom-fixture-v2"
    lock["source_url"] = "file:///operator-staged/custom"
    lock_path.write_text(json.dumps(lock))

    observed = []
    for root in (first, second):
        async for session in server_session(root):
            assets = (await session.call_tool("list_assets", {})).structured_content["assets"]
            observed.append(assets)
            assert assets[0]["audio_association"].endswith(".wav")
            assert assets[0]["source"] == json.loads((root / "manifest.json").read_text())["source_url"]
            # The exact snapshot revision is accepted; a stale revision cannot dispatch.
            good = await session.call_tool("play_move", {
                "action_id": "exact", "caller_id": "c", "session_id": "s",
                "asset_id": assets[0]["id"], "revision": assets[0]["revision"],
            })
            assert not good.is_error
            stale = await session.call_tool("play_move", {
                    "action_id": "stale", "caller_id": "c", "session_id": "s",
                    "asset_id": assets[0]["id"], "revision": "sha256:" + "0" * 64,
                })
            assert stale.is_error
            # A sidecar is part of the locked inventory, even when playback sound is off.
            (root / "assets" / (assets[0]["id"].split("/")[-1] + ".wav")).write_bytes(b"mutated-audio")
            mutated = await session.call_tool("play_move", {
                "action_id": "mutated", "caller_id": "c", "session_id": "s",
                "asset_id": assets[0]["id"], "revision": assets[0]["revision"],
            })
            assert mutated.is_error

    assert [asset[0]["id"] for asset in observed] == ["reachy/hello", "reachy/custom"]
    assert observed[0][0]["revision"] != observed[1][0]["revision"]


def test_upstream_and_custom_reachy_pack_fixtures_compile_with_exact_pins(tmp_path):
    fixture_pack(tmp_path)
    path = Path(__file__).resolve().parents[2] / "examples/robots/reachy/behavior-packs.upstream-custom.json"
    document = json.loads(path.read_text())
    revision = "sha256:" + "a" * 64
    for pack in document["packs"]:
        pack["cues"][0]["asset_revision"] = revision
    path = tmp_path / "packs.json"
    path.write_text(json.dumps(document))
    pack_revision = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    client = MCPClient({}, behavior_packs_path=path)
    upstream = client.compile_behavior_pack(
        "upstream-curious", pack_revision, "upstream-take", server="reachy",
    )
    custom = client.compile_behavior_pack(
        "custom-wave", pack_revision, "custom-take", server="reachy",
    )
    assert upstream.cues[0].asset_id == "reachy/curious1"
    assert custom.cues[0].asset_id == "reachy/custom-wave"
    assert upstream.cues[0].asset_revision == custom.cues[0].asset_revision == revision


@pytest.mark.anyio
async def test_reachy_stdio_denies_motion_without_authority(tmp_path):
    fixture_pack(tmp_path)
    async for session in server_session(tmp_path, "denied"):
        result = await session.call_tool("head_pose", {"action_id": "denied", "caller_id": "c", "session_id": "s",
            "parameters": {"pose": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}})
        assert result.is_error


@pytest.mark.anyio
async def test_reachy_stdio_preflight_resolves_namespaced_control_ids(tmp_path):
    fixture_pack(tmp_path)
    root = Path(__file__).resolve().parents[2]
    config = ServerConfig(
        command=sys.executable,
        args=(str(Path(__file__).with_name("reachy_mcp_fake_server.py")), str(tmp_path)),
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    plan = compile_script(
        Script(take_id="reachy-preflight", cues=(Cue(
            id="head", at=0, operation="head", arguments={
                "action_id": "head-1",
                "parameters": {"pose": [[1, 0, 0, 0], [0, 1, 0, 0],
                                       [0, 0, 1, 0], [0, 0, 0, 1]]},
            },
        ),)),
        {"reachy": config},
        {"head": ToolBinding(server="reachy", tool="head_pose")},
    )
    await MCPClient({"reachy": config}).preflight(plan)


@pytest.mark.anyio
async def test_reachy_stdio_preflight_rejects_same_resource_cues(tmp_path):
    fixture_pack(tmp_path)
    root = Path(__file__).resolve().parents[2]
    config = ServerConfig(
        command=sys.executable,
        args=(str(Path(__file__).with_name("reachy_mcp_fake_server.py")), str(tmp_path)),
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    plan = compile_script(
        Script(take_id="reachy-conflict", cues=(
            Cue(id="head-1", at=0, operation="head", arguments={
                "action_id": "a1",
                "parameters": {"pose": [[1, 0, 0, 0], [0, 1, 0, 0],
                                       [0, 0, 1, 0], [0, 0, 0, 1]]},
            }),
            Cue(id="head-2", at=0, operation="head", arguments={
                "action_id": "a2",
                "parameters": {"pose": [[1, 0, 0, 0], [0, 1, 0, 0],
                                       [0, 0, 1, 0], [0, 0, 0, 1]]},
            }),
        )),
        {"reachy": config},
        {"head": ToolBinding(server="reachy", tool="head_pose")},
    )
    with pytest.raises(PreflightError, match="resource_conflict:head-1:head-2"):
        await MCPClient({"reachy": config}).preflight(plan)


@pytest.mark.anyio
async def test_reachy_stdio_preserves_unknown_execution_outcome(tmp_path):
    fixture_pack(tmp_path)
    async for session in server_session(tmp_path, failure="fail"):
        asset = (await session.call_tool("list_assets", {})).structured_content["assets"][0]
        result = await session.call_tool("play_move", {"action_id": "uncertain", "caller_id": "c", "session_id": "s",
            "asset_id": asset["id"], "revision": asset["revision"]})
        assert not result.is_error
        action = await wait_action(session, "uncertain")
        assert action["state"] == "unknown"
        assert action["error"] == "execution_outcome_unknown"
        stopped = (await session.call_tool("device_stop", {})).structured_content
        assert stopped["confirmed"] is False
        # An unconfirmed stop must leave the performer quarantined.
        retry = await session.call_tool("play_move", {"action_id": "retry", "caller_id": "c", "session_id": "s",
            "asset_id": asset["id"], "revision": asset["revision"]})
        assert retry.is_error
