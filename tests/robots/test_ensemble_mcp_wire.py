"""Exercise real MCP tool schemas and shared room events through stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.anyio
async def test_opted_in_fallback_runs_and_interrupts_over_real_stdio(tmp_path):
    root = Path(__file__).resolve().parents[2]
    log = tmp_path / "fallback.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_speech_fallback_fake_server.py"), str(log)],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        started = await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "start", "sequence": 0,
            "state": "speaking", "source": "manual",
        })
        assert started.structured_content["outcome"] == "accepted"
        with anyio.fail_after(3):
            while not log.exists() or len(log.read_text().splitlines()) < 3:
                await anyio.sleep(0.02)
        assert all(line.startswith("speaking_fallback") for line in log.read_text().splitlines())
        ended = await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "stop", "sequence": 1,
            "state": "disconnected", "source": "manual", "interrupted": True,
        })
        assert ended.structured_content["state"] == "disconnected"
        count = len(log.read_text().splitlines())
        await anyio.sleep(0.2)
        assert len(log.read_text().splitlines()) == count


@pytest.mark.anyio
async def test_startup_speech_features_dispatch_through_real_stdio(tmp_path):
    root = Path(__file__).resolve().parents[2]
    log = tmp_path / "fake-actions.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_speech_resume_fake_server.py"), str(log)],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        event = await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "start", "sequence": 0,
            "state": "speaking", "source": "manual",
        })
        assert event.structured_content["outcome"] == "accepted"
        with anyio.fail_after(3):
            while not log.exists() or "startup" not in log.read_text():
                await anyio.sleep(0.01)
        await anyio.sleep(0.1)
        assert log.read_text().splitlines() == ["startup"]
        result = await session.call_tool("ensemble_speech_envelope", {
            "session_id": "room", "performer_id": "reachy", "frames": [{
                "schema": "forge.speech_envelope.v1", "stream_id": "speech",
                "sequence": n, "offset_ms": n * 50, "duration_ms": 50,
                "active": True, "intensity": 0.6, "rms_dbfs": -18.0,
                "timing_source": "generated_pcm", "acoustic_playout_measured": False,
            } for n in range(8)],  # 0.4 s of audio: motion follows playback, not arrival
        })
        assert result.structured_content["outcome"] == "observed"
        with anyio.fail_after(2):
            while "head_pose" not in log.read_text().splitlines():
                await anyio.sleep(0.01)
        assert "authored_speaking" not in log.read_text().splitlines()
        await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "end", "sequence": 1,
            "state": "disconnected", "source": "manual",
        })


@pytest.mark.anyio
async def test_real_stdio_mcp_one_room_controls_three_mock_devices():
    root = Path(__file__).resolve().parents[2]
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_fake_ensemble_server.py")],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"ensemble_state", "ensemble_capabilities", "ensemble_presence",
                "ensemble_expression", "ensemble_intent",
                "ensemble_presentation"} <= names
        capabilities = await session.call_tool("ensemble_capabilities", {})
        assert capabilities.structured_content["session_id"] is None
        assert capabilities.structured_content["motion_configured_enabled"] is True
        assert capabilities.structured_content["live_motion_authority"] == "unknown"
        assert capabilities.structured_content["session_ready_event"] == "ensemble_presence(state='idle')"
        for performer in capabilities.structured_content["performers"].values():
            assert performer["status"] == "idle"
            assert performer["live_readiness"] == "unknown"
            assert "yes" in performer["semantic_intents"]
        snapshot = await session.call_tool("ensemble_state", {})
        assert not snapshot.is_error
        assert snapshot.structured_content["state"] == "disconnected"
        assert snapshot.structured_content["presentation_presets_configured"] == {
            "stackchan": ["enlarged", "normal"],
        }
        accepted = await session.call_tool("ensemble_presence", {
            "session_id": "4g-room", "event_id": "e0",
            "sequence": 0, "state": "speaking",
            "source": "agent_state",
        })
        assert not accepted.is_error
        assert accepted.structured_content["outcome"] == "accepted"
        assert set(accepted.structured_content["performers"]) == {
            "reachy", "stackchan", "soarm101",
        }
        await anyio.sleep(0.04)
        display = await session.call_tool("ensemble_presentation", {
            "performer_id": "stackchan", "preset": "enlarged", "action_id": "scale-1",
        })
        assert not display.is_error
        assert display.structured_content["outcome"] == "accepted"
        missing_display = await session.call_tool("ensemble_presentation", {
            "performer_id": "reachy", "preset": "enlarged", "action_id": "bad-scale",
        })
        assert missing_display.structured_content["outcome"] == "unsupported"
        again = await session.call_tool("ensemble_presentation", {
            "performer_id": "stackchan", "preset": "enlarged", "action_id": "scale-1",
        })
        assert again.structured_content["outcome"] == "duplicate"
        yes = await session.call_tool("ensemble_intent", {
            "intent": "yes", "action_id": "yes-0",
        })
        assert not yes.is_error
        assert yes.structured_content["outcome"] == "accepted"
        duplicate = await session.call_tool("ensemble_expression", {
            "expression": "yes", "action_id": "yes-0",
        })
        assert duplicate.structured_content["outcome"] == "duplicate"
        conflict = await session.call_tool("ensemble_presence", {
            "session_id": "wrong-room", "event_id": "e1",
            "sequence": 1, "state": "listening",
        })
        assert not conflict.is_error
        assert conflict.structured_content["outcome"] == "denied"
        interrupted = await session.call_tool("ensemble_presence", {
            "session_id": "4g-room", "event_id": "interrupt", "sequence": 1,
            "state": "listening", "interrupted": True,
        })
        assert not interrupted.is_error
        assert interrupted.structured_content["outcome"] == "accepted"
        disconnected = await session.call_tool("ensemble_presence", {
            "session_id": "4g-room", "event_id": "e2",
            "sequence": 2, "state": "disconnected",
        })
        assert disconnected.structured_content["state"] == "disconnected"


@pytest.mark.anyio
async def test_original_idle_uses_one_real_stdio_child_and_stops(tmp_path):
    root = Path(__file__).resolve().parents[2]
    pytest.importorskip("reachy_mini")
    log = tmp_path / "fake-targets.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_original_idle_fake_server.py"), str(log)],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        accepted = await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "start", "sequence": 0,
            "state": "idle", "source": "manual",
        })
        assert accepted.structured_content["outcome"] == "accepted"
        with anyio.fail_after(3):
            while not log.exists() or len(log.read_text().splitlines()) < 2:
                await anyio.sleep(0.02)
        ended = await session.call_tool("ensemble_presence", {
            "session_id": "room", "event_id": "stop", "sequence": 1,
            "state": "disconnected", "source": "manual",
        })
        assert ended.structured_content["state"] == "disconnected"
        assert ended.structured_content["performers"]["reachy"]["status"] == "idle"
        count = len(log.read_text().splitlines())
        await anyio.sleep(0.1)
        assert len(log.read_text().splitlines()) == count


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_startup_gate_reports_asks_moves_to_sleep_and_parks_over_real_stdio(tmp_path):
    root = Path(__file__).resolve().parents[2]
    log = tmp_path / "gate.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_startup_gate_fake_server.py"), str(log), "15"],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        # The room checked the pose on startup: held still, with deltas reported.
        check = (await session.call_tool("ensemble_startup_check", {})).structured_content
        entry = check["performers"]["arm"]
        assert not check["ready"] and entry["status"] == "not_at_sleep"
        assert "Move arm to its sleep position first?" in entry["message"]
        presence = await session.call_tool("ensemble_presence", {
            "session_id": "s", "event_id": "e0", "sequence": 0, "state": "idle", "source": "manual"})
        assert presence.structured_content["performers"]["arm"]["status"] == "unavailable"
        assert not log.exists()
        await session.call_tool("ensemble_presence", {
            "session_id": "s", "event_id": "e1", "sequence": 1,
            "state": "disconnected", "source": "manual"})
        # The user said yes.
        moved = (await session.call_tool("ensemble_prepare", {
            "performer_id": "arm", "action": "move_to_sleep", "action_id": "sleep-1"})).structured_content
        assert moved["outcome"] == "at_sleep" and moved["ready"]
        assert log.read_text().splitlines() == ["go_sleep"]
        # Nothing is quarantined, so recovery reports that and does nothing.
        recovered = (await session.call_tool("ensemble_recover", {"performer_id": "arm"})).structured_content
        assert recovered["outcome"] == "not_quarantined"
        # Parking an already-parked robot makes no move.
        parked = (await session.call_tool("ensemble_park", {"action_id": "end-1"})).structured_content
        assert parked["outcome"] == "parked"
        assert parked["results"]["arm"]["outcome"] == "already_at_sleep"
        assert log.read_text().splitlines() == ["go_sleep"]
