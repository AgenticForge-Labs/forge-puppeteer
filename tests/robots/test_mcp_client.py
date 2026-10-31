"""Client planning and actual MCP stdio execution use fake server processes."""

import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from forge_puppeteer.mcp.client import (
    Cue,
    MCPClient,
    PreflightError,
    Script,
    ServerConfig,
    TakeExecutionError,
    ToolBinding,
    compile_script,
)


@pytest.fixture
def config():
    fixture = Path(__file__).with_name("mcp_client_fake_server.py")
    return ServerConfig(
        command=sys.executable,
        args=(str(fixture),),
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )


def device_config(name, tmp_path, authority="granted"):
    fixture = Path(__file__).with_name("mcp_client_fake_server.py")
    return ServerConfig(
        command=sys.executable,
        args=(str(fixture), name, authority, str(tmp_path / f"{name}.jsonl")),
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )


@pytest.fixture
def operations():
    return {"play": ToolBinding(server="robot", tool="perform")}


def test_compile_preserves_timeline_and_exact_asset_pin(config, operations):
    script = Script(
        take_id="take-7",
        cues=(
            Cue(
                id="second",
                at=2,
                operation="play",
                asset_id="fixture:pose",
                asset_revision="sha256:one",
                arguments={"action_id": "a2", "asset_id": "fixture:pose", "revision": "sha256:one"},
            ),
            Cue(
                id="first",
                at=1,
                operation="play",
                asset_id="fixture:pose",
                asset_revision="sha256:one",
                arguments={"action_id": "a1", "asset_id": "fixture:pose", "revision": "sha256:one"},
            ),
        ),
    )
    plan = compile_script(script, {"robot": config}, operations)
    assert [cue.id for cue in plan.cues] == ["first", "second"]
    assert plan.cues[0].asset_id == "fixture:pose"
    assert plan.cues[0].asset_revision == "sha256:one"
    assert plan.take_id == "take-7"


def test_compile_rejects_unknown_operation_and_partial_asset_pin(config, operations):
    with pytest.raises(PreflightError, match="unknown_operation"):
        compile_script(
            Script(take_id="t", cues=(Cue(id="c", at=0, operation="other"),)),
            {"robot": config},
            operations,
        )
    with pytest.raises(PreflightError, match="incomplete_asset_pin"):
        compile_script(
            Script(take_id="t", cues=(Cue(id="c", at=0, operation="play", asset_id="fixture:pose"),)),
            {"robot": config}, operations,
        )


def test_compile_validates_dependencies_and_timing_contract(config, operations):
    cues = (
        Cue(id="a", at=0, operation="play", start_tolerance=0.02),
        Cue(id="b", at=1, operation="play", depends_on=("a",)),
    )
    plan = compile_script(Script(take_id="deps", cues=cues), {"robot": config}, operations)
    assert plan.cues[0].start_tolerance == 0.02
    assert plan.cues[1].depends_on == ("a",)
    with pytest.raises(PreflightError, match="unknown_dependency"):
        compile_script(Script(take_id="bad", cues=(Cue(
            id="b", at=0, operation="play", depends_on=("missing",),
        ),)), {"robot": config}, operations)
    with pytest.raises(PreflightError, match="cyclic_dependency"):
        compile_script(Script(take_id="bad", cues=(
            Cue(id="a", at=0, operation="play", depends_on=("b",)),
            Cue(id="b", at=1, operation="play", depends_on=("a",)),
        )), {"robot": config}, operations)


@pytest.mark.anyio
async def test_stdio_dependency_waits_for_prerequisite_terminal_success(config):
    plan = compile_script(Script(take_id="ordered", cues=(
        Cue(id="first", at=0, operation="play", arguments={
            "action_id": "first", "asset_id": "fixture:pose", "revision": "sha256:one",
        }, asset_id="fixture:pose", asset_revision="sha256:one"),
        Cue(id="second", at=0, operation="play", depends_on=("first",),
            arguments={"action_id": "second", "asset_id": "fixture:pose", "revision": "sha256:one"},
            asset_id="fixture:pose", asset_revision="sha256:one"),
    )), {"robot": config}, {"play": ToolBinding(server="robot", tool="perform")})
    result = await MCPClient({"robot": config}).execute(plan)
    assert [item["cue_id"] for item in result] == ["first", "second"]
    assert all("requested_at" in item and "observed_start_at" in item for item in result)
def test_compile_routes_speech_locally_and_validates_its_timing(config):
    plan = compile_script(Script(take_id="voice", cues=(Cue(
        id="line", at=0.25, operation="speak",
        arguments={"text": "Hello", "sample_duration": 0.5, "speed": 1.2},
    ),)), {"robot": config}, {})
    assert plan.cues[0].server == "@local"
    assert plan.cues[0].arguments["sample_duration"] == 0.5
    assert plan.manifest == {}
    with pytest.raises(PreflightError, match="invalid_speech_cue"):
        compile_script(Script(take_id="bad", cues=(Cue(
            id="line", at=0, operation="speak", arguments={"text": "", "oops": True},
        ),)), {}, {})


@pytest.mark.anyio
async def test_stdio_script_schedules_local_speech_with_device_and_holds_sample_window(
    tmp_path, config
):
    class Voice:
        def __init__(self):
            self.calls = []
            self.stopped = 0

        async def speak(self, text, **options):
            self.calls.append((time.monotonic(), text, options))

        async def stop(self):
            self.stopped += 1

    voice = Voice()
    plan = compile_script(Script(take_id="take-with-speech", cues=(
        Cue(id="a-speech", at=0, operation="speak", arguments={
            "text": "Hello there", "sample_duration": 0.04, "voice_alias": "guide",
        }),
        Cue(id="z-device", at=0, operation="play", arguments={
            "action_id": "move", "asset_id": "fixture:pose", "revision": "sha256:one",
        },
            asset_id="fixture:pose", asset_revision="sha256:one"),
    )), {"robot": config}, {"play": ToolBinding(server="robot", tool="perform")})

    started = time.monotonic()
    result = await MCPClient({"robot": config}, voice_provider=voice).execute(plan)
    elapsed = time.monotonic() - started

    assert [item["cue_id"] for item in result] == ["a-speech", "z-device"]
    assert voice.calls[0][1:] == ("Hello there", {
        "audio_uri": None, "model_alias": None, "voice": None,
        "voice_alias": "guide", "output_device": None,
        "response_format": None, "speed": None,
    })
    assert result[0]["speech_finished_at"] >= 0.035
    assert elapsed >= 0.035
    assert result[0]["observed_at"] < 0.04


@pytest.mark.anyio
async def test_speech_failure_stops_voice_and_all_configured_devices(tmp_path):
    class FailingVoice:
        stopped = 0

        async def speak(self, text, **options):
            raise RuntimeError("voice output failed")

        async def stop(self):
            self.stopped += 1

    event_path = tmp_path / "speech-failure.jsonl"
    config = device_config("robot", tmp_path)
    config = config.model_copy(update={
        "args": (config.args[0], "cancelled", "granted", str(event_path)),
    })
    voice = FailingVoice()
    plan = compile_script(Script(take_id="voice-failure", cues=(
        Cue(id="a-speech", at=0, operation="speak", arguments={"text": "Hello"}),
        Cue(id="z-device", at=0, operation="play", arguments={
            "action_id": "move", "asset_id": "fixture:pose", "revision": "sha256:one",
        },
            asset_id="fixture:pose", asset_revision="sha256:one"),
    )), {"robot": config}, {"play": ToolBinding(server="robot", tool="perform")})

    with pytest.raises(TakeExecutionError, match="speech_failed"):
        await MCPClient({"robot": config}, voice_provider=voice).execute(plan)

    assert voice.stopped == 1
    events = [json.loads(line)["event"] for line in event_path.read_text().splitlines()]
    assert "stop" in events


@pytest.mark.anyio
async def test_shared_voice_resource_serializes_and_applies_late_skip_with_fake_clock():
    import anyio

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        async def sleep(self, seconds):
            self.now += seconds

    class Voice:
        def __init__(self, clock):
            self.clock = clock
            self.active = 0
            self.maximum_active = 0
            self.calls = []

        async def speak(self, text, **_options):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.calls.append(text)
            await self.clock.sleep(1.0)
            self.active -= 1

        async def stop(self):
            pass

    clock = Clock()
    voice = Voice(clock)
    client = MCPClient({}, voice_provider=voice, clock=clock)
    plan = compile_script(Script(take_id="voice-lock", cues=(
        Cue(id="first", at=0, operation="speak", arguments={"text": "first"}),
        Cue(id="second", at=0, operation="speak", late_policy="skip", late_tolerance=0,
            arguments={"text": "second"}),
    )), {}, {})
    results = []

    async with anyio.create_task_group() as tasks:
        for cue in plan.cues:
            tasks.start_soon(client._run_speech_cue, cue, plan.take_id, 0.0, {}, results)

    assert voice.maximum_active == 1
    assert voice.calls == ["first"]
    assert next(item for item in results if item["cue_id"] == "second")["skipped"] is True


@pytest.mark.anyio
async def test_explicit_start_tolerance_fails_closed_with_fake_clock():
    class Clock:
        def monotonic(self):
            return 0.25

        async def sleep(self, _seconds):
            pass

    class Voice:
        async def speak(self, *_args, **_kwargs):
            raise AssertionError("late cue must not start")

        async def stop(self):
            pass

    client = MCPClient({}, voice_provider=Voice(), clock=Clock())
    plan = compile_script(Script(take_id="late", cues=(Cue(
        id="cue", at=0, operation="speak", start_tolerance=0.05,
        arguments={"text": "must not play"},
    ),)), {}, {})
    with pytest.raises(TakeExecutionError, match="start_timing_unmet"):
        await client._run_speech_cue(plan.cues[0], plan.take_id, 0.0, {}, [])


@pytest.mark.anyio
async def test_cancelling_local_speech_stops_voice_and_configured_devices():
    import anyio

    class Voice:
        def __init__(self):
            self.started = anyio.Event()
            self.stopped = 0

        async def speak(self, _text, **_options):
            self.started.set()
            await anyio.sleep_forever()

        async def stop(self):
            self.stopped += 1

    class Session:
        def __init__(self):
            self.tools = []

        async def call_tool(self, name, _arguments):
            self.tools.append(name)

    voice = Voice()
    plan = compile_script(Script(take_id="cancelled", cues=(
        Cue(id="speech", at=0, operation="speak", arguments={"text": "long line"}),
    )), {}, {})
    session = Session()
    client = MCPClient({}, voice_provider=voice)
    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(client._run_speech_cue, plan.cues[0], plan.take_id,
                             0.0, {"device": session}, [])
            await voice.started.wait()
            scope.cancel()

    assert voice.stopped == 1
    assert session.tools == ["device_stop"]


@pytest.mark.anyio
async def test_actual_stdio_preflight_and_fake_action_execution(config, operations):
    plan = compile_script(
        Script(
            take_id="take-live-wire",
            cues=(
                Cue(
                    id="cue-1",
                    at=0,
                    operation="play",
                    asset_id="fixture:pose",
                    asset_revision="sha256:one",
                    arguments={
                        "action_id": "action-1",
                        "asset_id": "fixture:pose",
                        "revision": "sha256:one",
                    },
                ),
            ),
        ),
        {"robot": config},
        operations,
    )
    result = await MCPClient({"robot": config}).execute(plan)
    assert result[0]["take_id"] == "take-live-wire"
    assert result[0]["cue_id"] == "cue-1"
    assert result[0]["result"] == {
        "action_id": "action-1",
        "asset_id": "fixture:pose",
        "revision": "sha256:one",
    }


@pytest.mark.anyio
async def test_stdio_script_waits_for_action_completion_evidence(config):
    plan = compile_script(
        Script(take_id="take-completion", cues=(Cue(
            id="cue-completion", at=0, operation="play_async",
            arguments={"action_id": "async-1"},
        ),)),
        {"robot": config},
        {"play_async": ToolBinding(server="robot", tool="perform_async")},
    )
    result = await MCPClient({"robot": config}).execute(plan)
    assert result[0]["result"]["state"] == "completed"
    assert result[0]["result"]["completion_evidence"] == "fixture_done"


@pytest.mark.anyio
async def test_stdio_completion_timeout_stops_server(tmp_path):
    event_path = tmp_path / "timeout.jsonl"
    config = device_config("robot", tmp_path)
    config = config.model_copy(update={"args": (*config.args[:3], str(event_path))})
    plan = compile_script(
        Script(take_id="take-timeout", cues=(Cue(
            id="cue-timeout", at=0, operation="play_async",
            arguments={"action_id": "never"},
        ),)),
        {"robot": config},
        {"play_async": ToolBinding(server="robot", tool="perform_async")},
    )
    with pytest.raises(TakeExecutionError, match="action_completion_timeout"):
        await MCPClient({"robot": config}, completion_timeout=0.01,
                        completion_poll_interval=0.005).execute(plan)
    events = (
        [json.loads(line) for line in event_path.read_text().splitlines()]
        if event_path.exists()
        else []
    )
    assert any(event["event"] == "stop" for event in events)


@pytest.mark.anyio
async def test_stdio_owner_denies_conversational_caller_during_scripted_take(config):
    from contextlib import AsyncExitStack

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=config.command, args=list(config.args),
                                   env={**__import__("os").environ, **config.env})
    async with AsyncExitStack() as stack:
        reader, writer = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(reader, writer))
        await session.initialize()
        take_owner = {"caller_id": "scripted-take", "session_id": "take-live"}
        acquired = await session.call_tool("control_acquire", take_owner)
        assert not acquired.is_error
        denied = await session.call_tool("perform", {
            "action_id": "chat-action", "asset_id": "fixture:pose",
            "revision": "sha256:one", "caller_id": "conversation",
            "session_id": "chat-1",
        })
        assert denied.is_error
        assert "ownership_busy" in str(denied.content)
        released = await session.call_tool("control_release", take_owner)
        assert not released.is_error
        allowed = await session.call_tool("conversational_perform", {"action_id": "chat-after"})
        assert not allowed.is_error


@pytest.mark.anyio
async def test_preflight_rejects_stale_asset_before_dispatch(config, operations):
    plan = compile_script(
        Script(
            take_id="t",
            cues=(
                Cue(
                    id="c",
                    at=0,
                    operation="play",
                    asset_id="fixture:pose",
                    asset_revision="sha256:stale",
                    arguments={"action_id": "must-not-run", "asset_id": "fixture:pose", "revision": "sha256:stale"},
                ),
            ),
        ),
        {"robot": config},
        operations,
    )
    with pytest.raises(PreflightError, match="stale_or_missing_asset"):
        await MCPClient({"robot": config}).preflight(plan)


@pytest.mark.anyio
async def test_preflight_rejects_later_invalid_tool_arguments_before_any_dispatch(tmp_path):
    event_path = tmp_path / "schema-preflight.jsonl"
    config = device_config("robot", tmp_path)
    config = config.model_copy(update={"args": (*config.args[:3], str(event_path))})
    plan = compile_script(
        Script(take_id="schema-preflight", cues=(
            Cue(id="valid-first", at=0, operation="play", arguments={
                "action_id": "must-not-run", "asset_id": "fixture:pose", "revision": "sha256:one",
            }),
            Cue(id="invalid-later", at=1, operation="play_async", arguments={}),
        )),
        {"robot": config},
        {
            "play": ToolBinding(server="robot", tool="perform"),
            "play_async": ToolBinding(server="robot", tool="perform_async"),
        },
    )

    with pytest.raises(PreflightError, match="tool_arguments_invalid:invalid-later:perform_async"):
        await MCPClient({"robot": config}).execute(plan)
    events = (
        [json.loads(line) for line in event_path.read_text().splitlines()]
        if event_path.exists()
        else []
    )
    assert not any(event["event"] in {"perform", "acquire"} for event in events)


@pytest.mark.anyio
async def test_mid_take_failure_independently_stops_device(config, operations):
    plan = compile_script(
        Script(take_id="take-fail", cues=(Cue(id="bad-cue", at=0, operation="play",
            arguments={"action_id": "fail", "asset_id": "fixture:pose", "revision": "sha256:one"}),)),
        {"robot": config}, operations)
    with pytest.raises(TakeExecutionError, match="cue_failed:bad-cue") as caught:
        await MCPClient({"robot": config}).execute(plan)
    assert caught.value.stops["robot"]["confirmed"] is True


@pytest.mark.anyio
async def test_late_fail_stops_server_after_an_earlier_action_started(config, operations):
    plan = compile_script(
        Script(take_id="take-late", cues=(
            Cue(id="started", at=0, operation="play",
                arguments={"action_id": "already-started", "asset_id": "fixture:pose", "revision": "sha256:one"}),
            Cue(id="late", at=0.000001, operation="play", late_policy="fail", late_tolerance=0,
                arguments={"action_id": "must-not-run", "asset_id": "fixture:pose", "revision": "sha256:one"}),
        )),
        {"robot": config}, operations,
    )
    with pytest.raises(TakeExecutionError, match="cue_late:late") as caught:
        await MCPClient({"robot": config}).execute(plan)
    assert caught.value.stops["robot"]["confirmed"] is True


@pytest.mark.anyio
async def test_expected_take_error_is_unwrapped_from_mcp_cleanup_group(config, operations, monkeypatch):
    client = MCPClient({"robot": config})
    expected = TakeExecutionError("bad-cue", {"robot": {"confirmed": True}})

    async def fail_during_execution(_plan):
        raise ExceptionGroup("stdio cleanup", [RuntimeError("cleanup failed"), expected])

    monkeypatch.setattr(client, "_execute", fail_during_execution)
    with pytest.raises(TakeExecutionError, match="cue_failed:bad-cue") as caught:
        await client.execute(None)
    assert caught.value is expected


@pytest.mark.anyio
async def test_preflight_rejects_concurrent_resource_conflict(config, operations):
    plan = compile_script(Script(take_id="t", cues=(
        Cue(id="a", at=0, operation="play", arguments={
            "action_id": "a", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
        Cue(id="b", at=0, operation="play", arguments={
            "action_id": "b", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
    )), {"robot": config}, operations)
    with pytest.raises(PreflightError, match="resource_conflict:a:b"):
        await MCPClient({"robot": config}).preflight(plan)


@pytest.mark.anyio
async def test_multi_device_script_uses_two_real_stdio_servers(tmp_path):
    servers = {
        "stackchan": device_config("stackchan", tmp_path),
        "reachy": device_config("reachy", tmp_path),
    }
    operations = {
        "face": ToolBinding(server="stackchan", tool="perform"),
        "gesture": ToolBinding(server="reachy", tool="perform"),
    }
    plan = compile_script(Script(take_id="multi-take", cues=(
        Cue(id="face-1", at=0, operation="face", arguments={
            "action_id": "face-action", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
        Cue(id="gesture-1", at=0, operation="gesture", arguments={
            "action_id": "gesture-action", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
    )), servers, operations)

    results = await MCPClient(servers).execute(plan)

    assert {item["cue_id"] for item in results} == {"face-1", "gesture-1"}
    assert all(item["take_id"] == "multi-take" for item in results)
    assert {name for name in plan.manifest} == {"stackchan", "reachy"}
    events = [json.loads(line) for name in servers
              for line in (tmp_path / f"{name}.jsonl").read_text().splitlines()]
    actions = [event for event in events if event["event"] == "perform"]
    assert {(event["device"], event["action_id"]) for event in actions} == {
        ("stackchan", "face-action"), ("reachy", "gesture-action"),
    }


@pytest.mark.anyio
async def test_operator_behavior_pack_expands_and_runs_over_fake_stdio(tmp_path):
    manifest = Path(__file__).resolve().parents[2] / "examples/robots/stackchan/behavior-packs.json"
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    config = device_config("stackchan", tmp_path)
    plan = MCPClient({"robot": config}, behavior_packs_path=manifest).compile_behavior_pack(
        "greeting", revision, "greet-take", server="robot"
    )
    assert [cue.operation for cue in plan.cues] == ["head_move", "face_update", "lights_update"]
    assert plan.manifest["behavior:greeting"] == revision
    results = await MCPClient({"robot": config}).execute(plan)
    assert [item["cue_id"] for item in results] == ["greeting:0", "greeting:1", "greeting:2"]
    events = [json.loads(line) for line in (tmp_path / "stackchan.jsonl").read_text().splitlines()]
    action_events = [event for event in events if event["event"] not in {"acquire", "release"}]
    assert [event["event"] for event in action_events] == ["head_move", "face_update", "lights_update"]
    assert action_events[0]["parameters"]["yaw"] == {"angle": 120, "speed": 250}


def test_soarm_behavior_pack_compiles_exact_bounded_recording_replay():
    manifest = Path(__file__).resolve().parents[2] / "examples/robots/soarm101/behavior-packs.json"
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    plan = MCPClient({}, behavior_packs_path=manifest).compile_behavior_pack(
        "inspection-recording", revision, "inspection-take", server="arm"
    )

    cue, = plan.cues
    assert cue.operation == "replay_recording"
    assert cue.server == "arm" and cue.tool == "replay_recording"
    assert cue.asset_id == "soarm101/recording/replace-with-approved-id"
    assert cue.asset_revision.startswith("sha256:")
    assert cue.arguments == {
        "action_id": "inspection-take.inspection-recording.0",
        "parameters": {
            "asset_id": cue.asset_id,
            "revision": cue.asset_revision,
            "speed_scale": 0.5,
        },
    }
    assert plan.manifest["behavior:inspection-recording"] == revision


def test_soarm_behavior_pack_replay_requires_exact_asset_pin(tmp_path):
    manifest = tmp_path / "unpinned.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "performer": "soarm101", "packs": [{
            "id": "bad", "description": "un-pinned replay", "cues": [{
                "at": 0, "operation": "replay_sequence",
                "parameters": {"speed_scale": 0.25},
            }],
        }],
    }))
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(PreflightError, match="invalid_behavior_parameters:bad:0"):
        MCPClient({}, behavior_packs_path=manifest).compile_behavior_pack(
            "bad", revision, "t", server="arm"
        )


@pytest.mark.anyio
async def test_behavior_pack_speech_cue_runs_locally_with_device_action(tmp_path):
    class Voice:
        def __init__(self):
            self.calls = []

        async def speak(self, text, **options):
            self.calls.append((text, options))

        async def stop(self):
            pass

    manifest = tmp_path / "speech-pack.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "performer": "stackchan", "packs": [{
            "id": "greeting", "description": "speech and movement", "cues": [
                {"at": 0, "operation": "speak", "parameters": {
                    "text": "Welcome", "sample_duration": 0.02,
                    "voice_alias": "guide",
                }},
                {"at": 0, "operation": "head_move", "parameters": {
                    "yaw": {"angle": 10, "speed": 20},
                }},
            ],
        }],
    }))
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    config = device_config("stackchan", tmp_path)
    voice = Voice()
    client = MCPClient({"robot": config}, behavior_packs_path=manifest, voice_provider=voice)
    plan = client.compile_behavior_pack("greeting", revision, "pack-take", server="robot")

    assert [cue.server for cue in plan.cues] == ["@local", "robot"]
    result = await client.execute(plan)

    assert voice.calls == [("Welcome", {
        "audio_uri": None, "model_alias": None, "voice": None,
        "voice_alias": "guide", "output_device": None,
        "response_format": None, "speed": None,
    })]
    assert {item["cue_id"] for item in result} == {"greeting:0", "greeting:1"}


def test_behavior_pack_rejects_unknown_stale_and_invalid_ids(tmp_path):
    manifest = Path(__file__).resolve().parents[2] / "examples/robots/stackchan/behavior-packs.json"
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(PreflightError, match="unknown_behavior_pack"):
        MCPClient({}, behavior_packs_path=manifest).compile_behavior_pack(
            "missing", revision, "t", server="robot"
        )
    with pytest.raises(PreflightError, match="stale_behavior_pack"):
        MCPClient({}, behavior_packs_path=manifest).compile_behavior_pack(
            "greeting", "sha256:old", "t", server="robot"
        )
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version":1,"performer":"stackchan","packs":[{"id":"x","description":"x","cues":[{"at":0,"operation":"head_move","parameters":{"unknown":1}}]}]}')
    invalid_revision = "sha256:" + __import__("hashlib").sha256(invalid.read_bytes()).hexdigest()
    with pytest.raises(PreflightError, match="invalid_behavior_parameters"):
        MCPClient({}, behavior_packs_path=invalid).compile_behavior_pack(
            "x", invalid_revision, "t", server="robot"
        )


def test_behavior_pack_rejects_operation_from_another_performer(tmp_path):
    manifest = tmp_path / "wrong-performer.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "performer": "soarm101",
        "packs": [{"id": "bad", "description": "bad", "cues": [
            {"at": 0, "operation": "head_move", "parameters": {}}
        ]}],
    }))
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(PreflightError, match="invalid_behavior_parameters:bad:0"):
        MCPClient({}, behavior_packs_path=manifest).compile_behavior_pack(
            "bad", revision, "t", server="robot"
        )


@pytest.mark.anyio
async def test_multi_device_preflight_denial_prevents_all_dispatch(tmp_path):
    servers = {
        "ready": device_config("ready", tmp_path),
        "denied": device_config("denied", tmp_path, authority="denied"),
    }
    operations = {
        "first": ToolBinding(server="ready", tool="perform"),
        "second": ToolBinding(server="denied", tool="perform"),
    }
    plan = compile_script(Script(take_id="denied-take", cues=(
        Cue(id="first", at=0, operation="first", arguments={
            "action_id": "must-not-run", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
        Cue(id="second", at=1, operation="second", arguments={
            "action_id": "also-must-not-run", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
    )), servers, operations)

    with pytest.raises(PreflightError, match="authority_not_active:denied"):
        await MCPClient(servers).execute(plan)

    assert all(not (tmp_path / f"{name}.jsonl").exists() for name in servers)


@pytest.mark.anyio
async def test_multi_device_action_failure_stops_every_configured_server(tmp_path):
    servers = {
        "first": device_config("first", tmp_path),
        "second": device_config("second", tmp_path),
    }
    operations = {
        "ok": ToolBinding(server="first", tool="perform"),
        "fail": ToolBinding(server="second", tool="perform"),
    }
    plan = compile_script(Script(take_id="failure-take", cues=(
        Cue(id="a-good", at=0, operation="ok", arguments={
            "action_id": "completed", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
        Cue(id="z-bad", at=0, operation="fail", arguments={
            "action_id": "fail", "asset_id": "fixture:pose", "revision": "sha256:one",
        }),
    )), servers, operations)

    with pytest.raises(TakeExecutionError, match="cue_failed:z-bad") as caught:
        await MCPClient(servers).execute(plan)

    assert set(caught.value.stops) == {"first", "second"}
    assert all(result["confirmed"] for result in caught.value.stops.values())
    events = [json.loads(line) for name in servers
              for line in (tmp_path / f"{name}.jsonl").read_text().splitlines()]
    assert {(event["event"], event["device"]) for event in events if event["event"] == "stop"} == {
        ("stop", "first"), ("stop", "second"),
    }
    assert any(event == {"event": "perform", "device": "first", "action_id": "completed"}
               for event in events)


@pytest.mark.anyio
async def test_take_owner_session_is_unique_across_retries(tmp_path):
    server = device_config("robot", tmp_path)
    plan = compile_script(Script(take_id="reusable-take", cues=(Cue(
        id="cue", at=0, operation="play", arguments={
            "action_id": "action", "asset_id": "fixture:pose", "revision": "sha256:one",
        },
    ),)), {"robot": server}, {"play": ToolBinding(server="robot", tool="perform")})

    await MCPClient({"robot": server}).execute(plan)
    await MCPClient({"robot": server}).execute(plan)

    events = [json.loads(line) for line in (tmp_path / "robot.jsonl").read_text().splitlines()]
    owner_sessions = [event["session_id"] for event in events if event["event"] == "acquire"]
    assert len(owner_sessions) == 2
    assert len(set(owner_sessions)) == 2
    assert all(session.startswith("reusable-take:") for session in owner_sessions)


@pytest.mark.anyio
async def test_multi_device_ownership_denial_releases_prior_claim_without_dispatch(tmp_path):
    servers = {
        "available": device_config("available", tmp_path),
        "z-busy": device_config("busy", tmp_path),
    }
    operations = {
        "first-op": ToolBinding(server="available", tool="perform"),
        "busy-op": ToolBinding(server="z-busy", tool="perform"),
    }
    plan = compile_script(Script(take_id="contended-take", cues=(
            Cue(id="first-cue", at=0, operation="first-op", arguments={
                "action_id": "first", "asset_id": "fixture:pose", "revision": "sha256:one",
            }),
            Cue(id="busy-cue", at=1, operation="busy-op", arguments={
                "action_id": "busy", "asset_id": "fixture:pose", "revision": "sha256:one",
            }),
    )), servers, operations)

    with pytest.raises(PreflightError, match="ownership_busy:z-busy"):
        await MCPClient(servers).execute(plan)

    events = [json.loads(line) for name in ("available", "busy")
              for line in (tmp_path / f"{name}.jsonl").read_text().splitlines()]
    assert {(event["event"], event["device"]) for event in events} == {
        ("acquire", "available"), ("release", "available"), ("acquire", "busy"),
    }
