"""Offline and real-stdio regression coverage for fake embodiment v1."""

import os
import sys
from pathlib import Path

import anyio
import pytest

from forge_puppeteer.embodiment import (
    SCHEMA,
    ExpressionRequest,
    FakeEmbodiment,
    PresenceObservation,
)
from forge_puppeteer.mcp.launcher import ServerConfig, create_execution
from forge_puppeteer.mcp.server import Gateway


def make_fake(device="reachy"):
    cfg = ServerConfig(
        backend="fake", device=device, instance_id=f"{device}-sim",
        allowed_tools=[
            "performer_discover", "conversation_presence", "expression_request",
            "action_lookup", "device_info",
        ],
        assets=["emotion:curious", "emotion:happy", "dance:hello", "gesture:wave"],
    )
    provider, runtime = create_execution(cfg)
    return cfg, provider, runtime, Gateway(runtime, provider, cfg.instance_id)


def presence(device, event_id, sequence, state, *, session="room", source="agent_state",
             interrupted=False):
    return PresenceObservation(
        event_id=event_id, performer_id=f"{device}-sim", sequence=sequence,
        session_id=session, state=state, source=source, interrupted=interrupted,
    )


@pytest.mark.parametrize("device", ["reachy", "stackchan", "soarm101"])
def test_fake_performer_discovery_and_ordered_presence(device):
    async def run():
        _cfg, _provider, runtime, gateway = make_fake(device)
        now = [100.0]
        service = FakeEmbodiment(gateway, ttl_s=10, clock=lambda: now[0])
        try:
            catalog = await service.discover()
            assert catalog["schema"] == SCHEMA
            assert catalog["execution_mode"] == "fake"
            assert catalog["physical_qualification"] == "not_qualified"
            assert all(c["status"] == "placeholder" for c in catalog["controls"].values())
            assert {a["name"] for a in catalog["expressions"]} == {
                "curious", "happy", "hello", "wave",
            }
            for seq, state in enumerate(("listening", "speaking", "idle")):
                event = presence(device, f"e{seq}", seq, state)
                result = await service.observe(event)
                assert result["outcome"] == "accepted"
                assert result["presence"]["state"] == state
                assert (await service.observe(event))["outcome"] == "duplicate"
            assert (await service.observe(presence(device, "old", 0, "speaking")))[
                "outcome"
            ] == "stale"
            assert (await service.observe(presence(
                device, "conflict", 2, "speaking"
            )))["outcome"] == "denied"
            assert (await service.observe(presence(
                device, "other", 0, "idle", session="other"
            )))["reason"] == "session_busy"
            now[0] += 11
            assert (await service.discover())["presence"]["stale"] is True
            assert (await service.observe(presence(
                device, "new-session", 0, "listening", session="other"
            )))["outcome"] == "accepted"
        finally:
            runtime.close()

    anyio.run(run)


def test_conflicts_denials_and_no_replay_after_ambiguous_status():
    async def run():
        cfg, provider, runtime, gateway = make_fake()
        service = FakeEmbodiment(gateway)
        asset = next(a for a in await provider.list_assets() if a.id == "reachy:emotion:curious")

        def expression(action_id="curious-1", name="curious", revision=None):
            return ExpressionRequest(
                action_id=action_id, caller_id="agent", session_id="room",
                performer_id=cfg.instance_id, kind="emotion", name=name,
                asset_revision=revision or asset.revision,
            )

        try:
            async with runtime.run():
                await service.observe(presence("reachy", "listen", 0, "listening"))
                busy = await service.express(expression())
                assert busy == {"outcome": "denied", "reason": "resource_conflict"}
                await service.observe(presence("reachy", "idle", 1, "idle"))
                assert (await service.express(expression(name="not_a_move")))[
                    "outcome"
                ] == "unsupported"
                assert (await service.express(expression(revision="stale")))[
                    "reason"
                ] == "stale_asset_revision"
                accepted = await service.express(expression())
                assert accepted["outcome"] == "accepted"
                assert accepted["execution_mode"] == "fake"
                assert accepted["physical_qualification"] == "not_qualified"
                # No second execution even if a caller repeats the action ID.
                repeated = await service.express(expression())
                assert repeated["outcome"] != "accepted"
                assert len(provider.actions) <= 1
                await service.observe(presence("reachy", "disconnect", 2, "disconnected"))
                assert (await service.express(expression("new")))[
                    "reason"
                ] == "presence_disconnected_or_expired"
        finally:
            runtime.close()

    anyio.run(run)


@pytest.mark.anyio
@pytest.mark.parametrize("device", ["reachy", "stackchan", "soarm101"])
async def test_real_packaged_mcp_stdio_boundary_for_each_fake_performer(tmp_path, device):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cfg, _provider, runtime, _gateway = make_fake(device)
    runtime.close()
    config_path = tmp_path / f"{device}.json"
    config_path.write_text(cfg.model_dump_json(), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(config_path)],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"performer_discover", "conversation_presence", "expression_request",
                "action_lookup"} <= tools
        assert "head_pose" not in tools
        discovery = await session.call_tool("performer_discover", {})
        assert not discovery.is_error
        d = discovery.structured_content
        assert d["execution_mode"] == "fake"
        assert d["performer_id"] == f"{device}-sim"
        event = presence(device, "event-1", 0, "idle")
        result = await session.call_tool(
            "conversation_presence", {"event": event.model_dump()}
        )
        assert result.structured_content["outcome"] == "accepted"
        rev = next(a["revision"] for a in d["expressions"] if a["name"] == "curious")
        expression = ExpressionRequest(
            action_id=f"{device}-action", caller_id="script", session_id="room",
            performer_id=f"{device}-sim", kind="emotion", name="curious",
            asset_revision=rev,
        )
        requested = await session.call_tool(
            "expression_request", {"request": expression.model_dump()}
        )
        assert not requested.is_error
        assert requested.structured_content["outcome"] == "accepted"
        invalid = await session.call_tool("expression_request", {
            "request": {**expression.model_dump(), "action_id": "unknown", "name": "fake_dance"}
        })
        assert invalid.structured_content["outcome"] == "unsupported"
        lookup = await session.call_tool(
            "action_lookup", {"action_id": f"{device}-action"}
        )
        assert not lookup.is_error
        assert lookup.structured_content["state"] in {
            "accepted", "queued", "running", "completed"
        }
