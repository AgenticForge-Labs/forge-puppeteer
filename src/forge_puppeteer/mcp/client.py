"""Deterministic plans over operator-configured MCP stdio servers.

The configuration owns subprocess argv and the operation-to-tool allowlist.
Plans contain only operation keys and JSON arguments; they cannot select a
command, import, filesystem path, or arbitrary MCP method.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..voice import VoiceProvider


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    # Simulation is an explicit caller-side boundary. The provider still has
    # to prove the downstream broker is in simulation_actions mode.
    simulation_only: bool = False


class ToolBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str = Field(min_length=1)
    tool: str = Field(min_length=1)


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    id: str = Field(min_length=1)
    at: float = Field(ge=0, allow_inf_nan=False)
    operation: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)
    asset_id: str | None = None
    asset_revision: str | None = None
    late_policy: Literal["run", "skip", "fail"] = "run"
    late_tolerance: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    depends_on: tuple[str, ...] = ()
    # Maximum permitted start error. None means report timing without a hard
    # guarantee; a specified value is enforced fail-closed.
    start_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    take_id: str = Field(min_length=1)
    cues: tuple[Cue, ...]


@dataclass(frozen=True)
class CompiledCue:
    id: str
    at: float
    operation: str
    server: str
    tool: str
    arguments: Mapping[str, object]
    asset_id: str | None
    asset_revision: str | None
    late_policy: str = "run"
    late_tolerance: float = 0.1
    depends_on: tuple[str, ...] = ()
    start_tolerance: float | None = None


@dataclass(frozen=True)
class CompiledPlan:
    take_id: str
    cues: tuple[CompiledCue, ...]
    manifest: Mapping[str, str]


class PreflightError(ValueError):
    """The plan cannot safely execute against the current MCP servers."""


class BehaviorCue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    at: float = Field(ge=0, allow_inf_nan=False)
    operation: Literal[
        "head_move", "face_update", "lights_update", "play_move", "head_pose",
        "antenna_targets", "go_pose", "jog_cartesian", "sleep", "speak",
        "replay_recording", "replay_sequence",
    ]
    parameters: dict[str, object] = Field(default_factory=dict)
    asset_id: str | None = None
    asset_revision: str | None = None


class BehaviorPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    description: str
    cues: tuple[BehaviorCue, ...]


class BehaviorPackFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    performer: Literal["stackchan", "reachy", "soarm101"]
    packs: tuple[BehaviorPack, ...]


def _compile_behavior_pack(
    pack_id: str,
    expected_revision: str,
    take_id: str,
    *,
    manifest_path: str | Path,
    server: str,
) -> CompiledPlan:
    """Expand an operator-selected behavior pack; caller input has no path or tool."""
    path = Path(manifest_path)
    try:
        raw = path.read_bytes()
        document = BehaviorPackFile.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        raise PreflightError("behavior_manifest_invalid") from exc
    revision = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected_revision != revision:
        raise PreflightError(f"stale_behavior_pack:{pack_id}")
    matches = [pack for pack in document.packs if pack.id == pack_id]
    if not matches:
        raise PreflightError(f"unknown_behavior_pack:{pack_id}")
    if len(matches) != 1:
        raise PreflightError(f"duplicate_behavior_pack:{pack_id}")
    from ..actions import SpeakAction
    from .reachy import AntennaParameters, PlayParameters
    from .reachy import HeadParameters as ReachyHeadParameters
    from .soarm101 import CartesianParameters, PoseParameters, ReplayParameters
    from .stackchan import FaceParameters, HeadParameters, LightParameters

    schemas = {
        "head_move": HeadParameters, "face_update": FaceParameters,
        "lights_update": LightParameters, "play_move": PlayParameters,
        "head_pose": ReachyHeadParameters, "antenna_targets": AntennaParameters,
        "go_pose": PoseParameters, "jog_cartesian": CartesianParameters,
        "sleep": None, "speak": SpeakAction,
        "replay_recording": ReplayParameters, "replay_sequence": ReplayParameters,
    }
    operations_by_performer = {
        "stackchan": {"head_move", "face_update", "lights_update", "speak"},
        "reachy": {"play_move", "head_pose", "antenna_targets", "speak"},
        "soarm101": {"go_pose", "jog_cartesian", "sleep", "speak",
                      "replay_recording", "replay_sequence"},
    }
    tools = {operation: operation for operation in schemas}
    cues = []
    for index, cue in enumerate(matches[0].cues):
        try:
            if cue.operation not in operations_by_performer[document.performer]:
                raise ValueError("operation is not supported by this performer")
            schema = schemas[cue.operation]
            raw_parameters = dict(cue.parameters)
            if cue.operation in {"replay_recording", "replay_sequence"}:
                if not cue.asset_id or not cue.asset_revision:
                    raise ValueError("replay requires a pinned asset")
                raw_parameters.update(asset_id=cue.asset_id, revision=cue.asset_revision)
            parameters = (
                schema.model_validate(raw_parameters).model_dump(exclude_none=True)
                if schema is not None else {}
            )
            if cue.operation == "speak":
                cues.append(Cue(
                    id=f"{pack_id}:{index}", at=cue.at, operation="speak",
                    arguments=parameters,
                ))
                continue
            if cue.operation == "play_move":
                if not cue.asset_id or not cue.asset_revision:
                    raise ValueError("play_move requires a pinned asset")
            elif cue.operation not in {"replay_recording", "replay_sequence"} and (
                cue.asset_id is not None or cue.asset_revision is not None
            ):
                raise ValueError("asset pins only apply to play_move")
        except ValueError as exc:
            raise PreflightError(f"invalid_behavior_parameters:{pack_id}:{index}") from exc
        # SDK broker request IDs are restricted to portable filename-safe
        # characters. Period separators preserve lineage and satisfy that gate.
        arguments: dict[str, object] = {"action_id": f"{take_id}.{pack_id}.{index}"}
        if cue.operation == "play_move":
            arguments.update({"asset_id": cue.asset_id, "revision": cue.asset_revision})
            if parameters:
                arguments["parameters"] = parameters
        elif cue.operation != "sleep":
            arguments["parameters"] = parameters
        cues.append(Cue(
            id=f"{pack_id}:{index}", at=cue.at, operation=cue.operation,
            arguments=arguments, asset_id=cue.asset_id, asset_revision=cue.asset_revision,
        ))
    compiled = tuple(CompiledCue(
        cue.id, cue.at, cue.operation,
        "@local" if cue.operation == "speak" else server,
        "speak" if cue.operation == "speak" else tools[cue.operation],
        MappingProxyType(json.loads(json.dumps(cue.arguments, allow_nan=False))),
        cue.asset_id, cue.asset_revision,
    ) for cue in sorted(cues, key=lambda item: (item.at, item.id)))
    return CompiledPlan(
        take_id, compiled,
        MappingProxyType({server: revision, f"behavior:{pack_id}": revision}),
    )


class TakeExecutionError(PreflightError):
    """Cue failure with independent stop evidence for every contacted server."""

    def __init__(self, cue_id: str, stops: Mapping[str, object], reason: str = "cue_failed"):
        self.cue_id, self.stops = cue_id, dict(stops)
        super().__init__(f"{reason}:{cue_id}; stop_results={self.stops}")


class _SystemClock:
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    async def sleep(seconds: float) -> None:
        import anyio
        await anyio.sleep(seconds)


def _result_object(result: object) -> dict[str, object] | None:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", ())
    if len(content) == 1 and getattr(content[0], "type", None) == "text":
        try:
            parsed = json.loads(content[0].text)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def compile_script(
    script: Script,
    servers: Mapping[str, ServerConfig],
    operations: Mapping[str, ToolBinding],
) -> CompiledPlan:
    """Resolve authored operation keys while preserving cue order and asset pins."""
    if len({cue.id for cue in script.cues}) != len(script.cues):
        raise PreflightError("duplicate_cue_id")
    ids = {cue.id for cue in script.cues}
    graph = {cue.id: cue.depends_on for cue in script.cues}
    for cue in script.cues:
        if len(set(cue.depends_on)) != len(cue.depends_on):
            raise PreflightError(f"duplicate_dependency:{cue.id}")
        unknown = set(cue.depends_on) - ids
        if unknown:
            raise PreflightError(f"unknown_dependency:{cue.id}:{min(unknown)}")
        if cue.id in cue.depends_on:
            raise PreflightError(f"cyclic_dependency:{cue.id}")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(cue_id: str) -> None:
        if cue_id in visiting:
            raise PreflightError(f"cyclic_dependency:{cue_id}")
        if cue_id in visited:
            return
        visiting.add(cue_id)
        for dependency in graph[cue_id]:
            visit(dependency)
        visiting.remove(cue_id)
        visited.add(cue_id)
    for cue_id in graph:
        visit(cue_id)
    ordered_ids = [cue.id for cue in sorted(script.cues, key=lambda item: (item.at, item.id))]
    positions = {cue_id: index for index, cue_id in enumerate(ordered_ids)}
    for cue_id, dependencies in graph.items():
        if any(positions[dependency] >= positions[cue_id] for dependency in dependencies):
            raise PreflightError(f"dependency_must_precede_cue:{cue_id}")
    result = []
    for cue in sorted(script.cues, key=lambda item: (item.at, item.id)):
        if cue.operation == "speak":
            # Speech is local to the scripted executor. It is never forwarded
            # as an arbitrary device MCP tool.
            from ..actions import SpeakAction

            try:
                speech = SpeakAction.model_validate(cue.arguments)
            except ValueError:
                raise PreflightError(f"invalid_speech_cue:{cue.id}") from None
            result.append(CompiledCue(
                cue.id, cue.at, cue.operation, "@local", "speak",
                MappingProxyType(json.loads(speech.model_dump_json())), None, None,
                cue.late_policy, cue.late_tolerance,
                cue.depends_on, cue.start_tolerance,
            ))
            continue
        binding = operations.get(cue.operation)
        if binding is None:
            raise PreflightError(f"unknown_operation:{cue.operation}")
        if binding.server not in servers:
            raise PreflightError(f"unknown_server:{binding.server}")
        if (cue.asset_id is None) != (cue.asset_revision is None):
            raise PreflightError(f"incomplete_asset_pin:{cue.id}")
        pinned_arguments = cue.arguments
        if cue.operation in {"replay_recording", "replay_sequence"}:
            pinned_arguments = cue.arguments.get("parameters", {})
        if cue.asset_id is not None and (
            not isinstance(pinned_arguments, Mapping)
            or pinned_arguments.get("asset_id") != cue.asset_id
            or pinned_arguments.get("revision") != cue.asset_revision
        ):
            raise PreflightError(f"asset_argument_pin_mismatch:{cue.id}")
        result.append(
            CompiledCue(
                cue.id,
                cue.at,
                cue.operation,
                binding.server,
                binding.tool,
                MappingProxyType(json.loads(json.dumps(cue.arguments, allow_nan=False))),
                cue.asset_id,
                cue.asset_revision,
                cue.late_policy,
                cue.late_tolerance,
                cue.depends_on,
                cue.start_tolerance,
            )
        )
    used_servers = {cue.server for cue in result if cue.server != "@local"}
    manifest = {
        name: "sha256:" + hashlib.sha256(servers[name].model_dump_json().encode()).hexdigest()
        for name in sorted(used_servers)
    }
    return CompiledPlan(script.take_id, tuple(result), MappingProxyType(manifest))


class MCPClient:
    """Own configured MCP subprocesses for preflight and bounded cue execution."""

    def __init__(
        self, servers: Mapping[str, ServerConfig], *, behavior_packs_path: str | Path | None = None,
        completion_timeout: float = 60.0, completion_poll_interval: float = 0.05,
        voice_provider: VoiceProvider | None = None,
        clock=None,
    ):
        if completion_timeout <= 0 or completion_poll_interval <= 0:
            raise ValueError("completion polling intervals must be positive")
        self._servers = dict(servers)
        self._behavior_packs_path = Path(behavior_packs_path) if behavior_packs_path else None
        self._completion_timeout = completion_timeout
        self._completion_poll_interval = completion_poll_interval
        self._voice_provider = voice_provider
        self._clock = clock or _SystemClock()
        # A configured VoiceProvider represents one output resource. Speech
        # cues share it even when their authored start times coincide.
        import anyio
        self._voice_lock = anyio.Lock()

    def compile_behavior_pack(
        self, pack_id: str, expected_revision: str, take_id: str, *, server: str
    ) -> CompiledPlan:
        """Compile a pack selected by ID from the operator-configured manifest."""
        if self._behavior_packs_path is None:
            raise PreflightError("behavior_packs_not_configured")
        return _compile_behavior_pack(
            pack_id, expected_revision, take_id,
            manifest_path=self._behavior_packs_path, server=server,
        )

    async def _sessions(self, stack: AsyncExitStack, names: set[str]):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("Install forge-puppeteer[mcp] to use MCP clients") from exc
        sessions = {}
        for name in sorted(names):
            config = self._servers.get(name)
            if config is None:
                raise PreflightError(f"server_not_configured:{name}")
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={**os.environ, **config.env},
            )
            reader, writer = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            sessions[name] = session
        return sessions

    async def preflight(self, plan: CompiledPlan) -> None:
        """Check live server tools, device readiness, and pinned asset revisions."""
        try:
            await self._preflight(plan)
        except BaseExceptionGroup as group:
            error = _find_preflight_error(group)
            if error is not None:
                raise error
            raise

    async def _preflight(self, plan: CompiledPlan) -> None:
        speech_cues = [cue for cue in plan.cues if cue.server == "@local"]
        if speech_cues and self._voice_provider is None:
            raise PreflightError("speech_provider_unavailable")
        if any(cue.operation != "speak" for cue in speech_cues):
            raise PreflightError("unsupported_local_operation")
        referenced = {cue.server for cue in plan.cues}
        referenced.discard("@local")
        async with AsyncExitStack() as stack:
            sessions = await self._sessions(stack, referenced)
            for server in sorted(referenced):
                if server not in sessions:
                    raise PreflightError(f"server_not_configured:{server}")
                session = sessions[server]
                tool_list = (await session.list_tools()).tools
                tools = {tool.name for tool in tool_list}
                tool_schemas = {tool.name: tool.input_schema for tool in tool_list}
                if not {"control_acquire", "control_release"}.issubset(tools):
                    raise PreflightError(f"ownership_tools_unavailable:{server}")
                info = await session.call_tool("device_info", {})
                device = _result_object(info)
                if info.is_error or device is None:
                    raise PreflightError(f"device_info_failed:{server}")
                if device.get("connected") is not True or device.get("ready") is not True:
                    raise PreflightError(f"device_not_ready:{server}")
                authority = device.get("authority")
                authority_active = isinstance(authority, dict) and any(
                    value in {"granted", "active"} for value in authority.values()
                )
                simulation_authority = (
                    self._servers[server].simulation_only
                    and isinstance(authority, dict)
                    and authority.get("armed") is False
                )
                if simulation_authority:
                    state_result = await session.call_tool("device_state", {})
                    state = _result_object(state_result)
                    broker_state = state.get("result", state) if state else None
                    broker_session = (
                        broker_state.get("broker_session")
                        if isinstance(broker_state, dict) else None
                    )
                    simulation_authority = (
                        not state_result.is_error
                        and isinstance(broker_session, dict)
                        and broker_session.get("mode") == "simulation_actions"
                    )
                if not authority_active and not simulation_authority:
                    raise PreflightError(f"authority_not_active:{server}")
                controls = device.get("controls", ())
                # Provider control IDs are namespaced (for example,
                # ``reachy.play_move``), while MCP tool names are the stable
                # operation suffix. Accept exact IDs and an unambiguous
                # namespace suffix; never guess when a provider publishes
                # multiple controls for one MCP tool.
                resources_by_tool: dict[str, set[str]] = {}
                ambiguous_controls: set[str] = set()
                for control in controls:
                    if not isinstance(control, dict):
                        continue
                    control_id = control.get("id")
                    resources = control.get("resources", ())
                    if not isinstance(control_id, str) or not isinstance(resources, (list, tuple)):
                        continue
                    for tool in tools:
                        if control_id == tool or control_id.endswith("." + tool):
                            if tool in resources_by_tool:
                                ambiguous_controls.add(tool)
                            resources_by_tool[tool] = set(resources)
                for cue in (c for c in plan.cues if c.server == server):
                    if cue.tool not in tools:
                        raise PreflightError(f"tool_unavailable:{server}:{cue.tool}")
                    # MCP's advertised input schema is the provider's typed
                    # contract. Check every authored cue before any performer
                    # ownership or action dispatch occurs.
                    arguments = dict(cue.arguments)
                    arguments.update({"caller_id": "forge-puppeteer-script", "session_id": "preflight"})
                    try:
                        from jsonschema import (
                            Draft202012Validator,
                            SchemaError,
                            ValidationError,
                        )

                        Draft202012Validator(tool_schemas[cue.tool]).validate(arguments)
                    except ImportError as exc:
                        raise RuntimeError(
                            "Install forge-puppeteer[mcp] with JSON Schema support"
                        ) from exc
                    except (SchemaError, ValidationError):
                        # Keep provider/schema details bounded; callers receive
                        # a stable cue-local error and no untrusted schema text.
                        raise PreflightError(
                            f"tool_arguments_invalid:{cue.id}:{cue.tool}"
                        ) from None
                    if cue.tool in ambiguous_controls:
                        raise PreflightError(f"resource_metadata_ambiguous:{server}:{cue.tool}")
                    if cue.tool not in resources_by_tool:
                        raise PreflightError(f"resource_metadata_unsupported:{server}:{cue.tool}")
                groups = {}
                for cue in (c for c in plan.cues if c.server == server):
                    groups.setdefault(cue.at, []).append(cue)
                for at, group in groups.items():
                    for index, left in enumerate(group):
                        for right in group[index + 1:]:
                            if left.id in right.depends_on or right.id in left.depends_on:
                                continue
                            left_resources = resources_by_tool.get(left.tool)
                            right_resources = resources_by_tool.get(right.tool)
                            if left_resources is None or right_resources is None:
                                raise PreflightError(f"resource_metadata_unsupported:{server}:{at}")
                            if left_resources & right_resources:
                                raise PreflightError(f"resource_conflict:{left.id}:{right.id}")
                for cue in (c for c in plan.cues if c.server == server and c.asset_id is not None):
                    found = False
                    offset = 0
                    while True:
                        page = await session.call_tool(
                            "list_assets", {"offset": offset, "limit": 100}
                        )
                        page_data = _result_object(page)
                        if page.is_error or page_data is None:
                            raise PreflightError(f"asset_catalog_failed:{server}")
                        assets = page_data.get("assets")
                        if not isinstance(assets, list):
                            raise PreflightError(f"asset_catalog_invalid:{server}")
                        for asset in assets:
                            if asset.get("id") == cue.asset_id:
                                found = asset.get("revision") == cue.asset_revision
                                break
                        if found or page_data.get("next_offset") is None:
                            break
                        next_offset = page_data.get("next_offset")
                        if type(next_offset) is not int or next_offset <= offset:
                            raise PreflightError(f"asset_catalog_invalid:{server}")
                        offset = next_offset
                    if not found:
                        raise PreflightError(f"stale_or_missing_asset:{cue.id}")


    async def execute(self, plan: CompiledPlan) -> tuple[dict[str, object], ...]:
        """Preflight then dispatch complete MCP actions on a monotonic cue timeline."""
        try:
            return await self._execute(plan)
        except BaseExceptionGroup as group:
            error = _find_exception(group, TakeExecutionError)
            if error is None:
                error = _find_exception(group, PreflightError)
            if error is not None:
                raise error
            raise

    async def _execute(self, plan: CompiledPlan) -> tuple[dict[str, object], ...]:
        import anyio

        await self.preflight(plan)
        results = []
        async with AsyncExitStack() as stack:
            sessions = await self._sessions(
                stack, {cue.server for cue in plan.cues if cue.server != "@local"}
            )
            # A take ID identifies authored lineage and may be reused for a
            # retry. Ownership identifies this execution attempt and must be
            # unique so overlapping attempts cannot masquerade as one owner.
            owner = {
                "caller_id": "forge-puppeteer-script",
                "session_id": f"{plan.take_id}:{uuid.uuid4().hex}",
            }
            acquired = []
            release_failures = []
            take_started = False
            take_completed = False
            cue_events = {cue.id: anyio.Event() for cue in plan.cues}
            cue_success: dict[str, bool] = {}
            stop_attempted = False
            stop_results: dict[str, dict[str, object]] = {}

            async def stop_once() -> dict[str, dict[str, object]]:
                nonlocal stop_attempted, stop_results
                if not stop_attempted:
                    stop_attempted = True
                    stop_results = await self._stop_sessions(sessions)
                return stop_results
            try:
                # Acquire every performer before dispatching the first cue. Roll
                # back partial acquisition if a later server is contended.
                for name, session in sorted(sessions.items()):
                    result = await session.call_tool("control_acquire", owner)
                    if result.is_error:
                        raise PreflightError(f"ownership_busy:{name}")
                    acquired.append((name, session))
                take_started = True
                started = self._clock.monotonic()
                async with anyio.create_task_group() as speech_tasks:
                    for cue in plan.cues:
                        for dependency in cue.depends_on:
                            await cue_events[dependency].wait()
                            if not cue_success.get(dependency, False):
                                raise TakeExecutionError(cue.id, await stop_once(),
                                                         reason=f"dependency_failed:{dependency}")
                        delay = started + cue.at - self._clock.monotonic()
                        if delay > 0:
                            await self._clock.sleep(delay)
                        lateness = max(0.0, self._clock.monotonic() - (started + cue.at))
                        observed_start = self._clock.monotonic() - started
                        if (cue.start_tolerance is not None
                                and lateness > cue.start_tolerance):
                            stops = await stop_once()
                            raise TakeExecutionError(cue.id, stops, reason="start_timing_unmet")
                        if cue.late_policy not in {"run", "skip", "fail"}:
                            raise PreflightError(f"unsupported_late_policy:{cue.id}")
                        # Speech may wait for the shared voice resource. Apply
                        # its late policy after that wait, immediately before
                        # output, rather than approving a cue that starts late.
                        if cue.server != "@local" and lateness > cue.late_tolerance:
                            if cue.late_policy == "skip":
                                results.append({"cue_id": cue.id, "take_id": plan.take_id,
                                                "requested_at": cue.at, "observed_at": self._clock.monotonic() - started,
                                                "observed_start_at": observed_start,
                                                "is_error": False, "skipped": True, "result": None})
                                cue_success[cue.id] = True
                                cue_events[cue.id].set()
                                continue
                            if cue.late_policy == "fail":
                                stops = await stop_once()
                                raise TakeExecutionError(cue.id, stops, reason="cue_late")
                        if cue.server == "@local":
                            speech_tasks.start_soon(
                                self._run_speech_cue_guarded, cue, plan.take_id, started,
                                sessions, results, cue_events, cue_success
                            )
                            continue
                        arguments = dict(cue.arguments)
                        arguments.update(owner)
                        response = await sessions[cue.server].call_tool(cue.tool, arguments)
                        response_data = _result_object(response)
                        if not response.is_error and response_data is not None:
                            state = response_data.get("state")
                            if state in {"accepted", "queued", "submitted", "running", "in_progress"}:
                                action_id = response_data.get("id", arguments.get("action_id"))
                                if not isinstance(action_id, str) or not action_id:
                                    raise TakeExecutionError(cue.id, await stop_once(),
                                                             reason="completion_id_missing")
                                response_data = await self._await_completion(
                                    sessions[cue.server], action_id,
                                    cue_id=cue.id, sessions=sessions,
                                )
                                state = response_data.get("state")
                                if state != "completed":
                                    stops = await stop_once()
                                    raise TakeExecutionError(cue.id, stops,
                                                             reason=f"action_{state or 'unknown'}")
                        results.append(
                            {
                                "cue_id": cue.id,
                                "take_id": plan.take_id,
                                "requested_at": cue.at,
                                "observed_at": self._clock.monotonic() - started,
                                "observed_start_at": observed_start,
                                "observed_finish_at": self._clock.monotonic() - started,
                                "is_error": response.is_error,
                                "result": response_data,
                            }
                        )
                        if response.is_error:
                            stops = await stop_once()
                            raise TakeExecutionError(cue.id, stops)
                        cue_success[cue.id] = True
                        cue_events[cue.id].set()
                take_completed = True
            except BaseException as exc:
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    with anyio.CancelScope(shield=True):
                        await stop_once()
                raise
            finally:
                import anyio
                with anyio.CancelScope(shield=True):
                    if take_started and not take_completed:
                        await stop_once()
                    for name, session in reversed(acquired):
                        try:
                            # A terminal lookup becomes visible just before the
                            # runtime drops its reservation. Retry that narrow
                            # cleanup window instead of losing ownership release
                            # to a benign performer_busy response.
                            deadline = self._clock.monotonic() + 2.0
                            while True:
                                released = await session.call_tool("control_release", owner)
                                if not released.is_error:
                                    break
                                error_text = " ".join(
                                    getattr(item, "text", "") for item in released.content
                                )
                                if "performer_busy" not in error_text or self._clock.monotonic() >= deadline:
                                    break
                                await self._clock.sleep(min(
                                    self._completion_poll_interval,
                                    max(0.0, deadline - self._clock.monotonic()),
                                ))
                            if released.is_error:
                                release_failures.append(name)
                        except Exception:  # noqa: BLE001 - continue releasing other sessions
                            release_failures.append(name)
            if release_failures:
                raise PreflightError(f"ownership_release_failed:{','.join(release_failures)}")
        order = {cue.id: index for index, cue in enumerate(plan.cues)}
        return tuple(sorted(results, key=lambda item: order[item["cue_id"]]))

    async def _run_speech_cue(self, cue, take_id, started, sessions, results):
        """Run authored local speech on the same monotonic take timeline."""
        import anyio

        from ..actions import SpeakAction

        action = SpeakAction.model_validate(dict(cue.arguments))
        target = started + cue.at
        await self._clock.sleep(max(0.0, target - self._clock.monotonic()))
        try:
            async with self._voice_lock:
                lateness = max(0.0, self._clock.monotonic() - target)
                observed = self._clock.monotonic() - started
                if cue.start_tolerance is not None and lateness > cue.start_tolerance:
                    stops = await self._stop_sessions(sessions)
                    raise TakeExecutionError(cue.id, stops, reason="start_timing_unmet")
                if lateness > cue.late_tolerance:
                    if cue.late_policy == "skip":
                        results.append({
                            "cue_id": cue.id, "take_id": take_id,
                            "requested_at": cue.at, "observed_at": self._clock.monotonic() - started,
                            "is_error": False, "skipped": True, "result": None,
                        })
                        return
                    if cue.late_policy == "fail":
                        stops = await self._stop_sessions(sessions)
                        raise TakeExecutionError(cue.id, stops, reason="cue_late")
                options = action.model_dump(exclude={"type", "text", "sample_duration"})
                await self._voice_provider.speak(action.text, **options)
                if action.sample_duration is not None:
                    await self._clock.sleep(max(0.0, target + action.sample_duration - self._clock.monotonic()))
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                await self._voice_provider.stop()
                await self._stop_sessions(sessions)
            raise
        except TakeExecutionError:
            raise
        except Exception as exc:
            with anyio.CancelScope(shield=True):
                try:
                    await self._voice_provider.stop()
                finally:
                    stops = await self._stop_sessions(sessions)
            raise TakeExecutionError(cue.id, stops, reason="speech_failed") from exc
        results.append({
            "cue_id": cue.id, "take_id": take_id, "requested_at": cue.at,
            "observed_at": observed,
            "observed_start_at": observed,
            "observed_finish_at": self._clock.monotonic() - started,
            "speech_finished_at": self._clock.monotonic() - started,
            "is_error": False, "result": {"state": "completed", "text": action.text},
        })

    async def _run_speech_cue_guarded(self, cue, take_id, started, sessions, results,
                                      cue_events, cue_success):
        try:
            await self._run_speech_cue(cue, take_id, started, sessions, results)
            cue_success[cue.id] = True
        finally:
            cue_events[cue.id].set()

    async def _await_completion(
        self, session, action_id: str, *, cue_id: str, sessions
    ):
        """Resolve explicit accepted/running records to terminal action evidence."""
        deadline = self._clock.monotonic() + self._completion_timeout
        while self._clock.monotonic() < deadline:
            lookup = await session.call_tool("action_lookup", {"action_id": action_id})
            record = _result_object(lookup)
            if lookup.is_error or record is None:
                raise TakeExecutionError(cue_id, await self._stop_sessions(sessions),
                                         reason="action_completion_lookup_failed")
            state = record.get("state")
            if state not in {"accepted", "queued", "submitted", "running", "in_progress"}:
                return record
            await self._clock.sleep(min(self._completion_poll_interval,
                                        max(0.0, deadline - self._clock.monotonic())))
        raise TakeExecutionError(cue_id, await self._stop_sessions(sessions),
                                 reason="action_completion_timeout")

    @staticmethod
    async def _stop_sessions(sessions) -> dict[str, dict[str, object]]:
        """Attempt every independent stop, retaining evidence even on failures."""
        stops = {}
        for name, session in sessions.items():
            try:
                stop = await session.call_tool("device_stop", {})
                stops[name] = {"confirmed": (_result_object(stop) or {}).get("confirmed") is True,
                               "is_error": stop.is_error, "result": _result_object(stop)}
            except Exception as exc:  # noqa: BLE001 - continue independent stops
                stops[name] = {"confirmed": False, "error": type(exc).__name__}
        return stops


def _find_preflight_error(group: BaseExceptionGroup) -> PreflightError | None:
    for error in group.exceptions:
        if isinstance(error, PreflightError):
            return error
        if isinstance(error, BaseExceptionGroup):
            nested = _find_preflight_error(error)
            if nested is not None:
                return nested
    return None


def _find_exception(group: BaseExceptionGroup, kind):
    for error in group.exceptions:
        if isinstance(error, kind):
            return error
        if isinstance(error, BaseExceptionGroup):
            nested = _find_exception(error, kind)
            if nested is not None:
                return nested
    return None
