"""Typed SO-ARM adapter that forwards exclusively to the existing broker MCP.

The supplied session is an already initialized MCP client session. This module
never imports the SO-ARM SDK, opens a port, or creates an alternate controller.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..execution import ActionRequest, Completion, StopResult
from ..robot_contracts import AssetDescriptor, ControlDescriptor, DeviceDescriptor


class BrokerSession(Protocol):
    async def list_tools(self): ...
    async def call_tool(self, name: str, arguments: dict[str, object]): ...


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PoseArgs(_Args):
    name: str = Field(min_length=1)
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)


class CartesianArgs(_Args):
    frame: Literal["world", "tool"]
    x_mm: float = 0
    y_mm: float = 0
    z_mm: float = 0
    speed_mm_s: float | None = Field(default=None, gt=0)
    acceleration_mm_s2: float | None = Field(default=None, gt=0)


class SleepArgs(_Args):
    pass


class JointJogArgs(_Args):
    joint: Literal["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
    delta_deg: float = Field(ge=-2, le=2)

    @field_validator("delta_deg")
    @classmethod
    def nonzero_delta(cls, value: float) -> float:
        if value == 0:
            raise ValueError("joint delta must not be zero")
        return value
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)


class GripperArgs(_Args):
    target: Literal["open", "close"]
    gripper_speed_raw: int | None = Field(default=None, ge=0)
    gripper_acceleration_raw: int | None = Field(default=None, ge=0)


class ReplayArgs(_Args):
    asset_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    speed_scale: float = Field(gt=0, le=1)


class SOARM101Provider:
    """Broker-backed discovery and bounded controls; broker permission is live."""

    _TOOLS = frozenset({"robot_health", "robot_capabilities", "robot_state", "list_assets",
                        "replay_recording", "replay_sequence", "go_pose", "jog_joint",
                        "jog_cartesian", "move_gripper", "sleep", "sleep_up",
                        "capture_camera", "stop"})

    def __init__(self, instance_id: str, session: BrokerSession, *, allowed_tools: frozenset[str],
                 simulation_only: bool = False):
        if not instance_id or allowed_tools - self._TOOLS:
            raise ValueError("invalid SO-ARM instance or broker tool grant")
        self.instance_id, self.session = instance_id, session
        self.allowed_tools = allowed_tools
        self.simulation_only = simulation_only
        self._simulation_verified = False

    async def _verify_simulation(self) -> None:
        if not self.simulation_only or self._simulation_verified:
            return
        if "robot_state" not in self.allowed_tools or "robot_state" not in await self._visible():
            raise PermissionError("simulation broker state is unavailable")
        body = await self._call("robot_state")
        result = body.get("result", body)
        evidence = result.get("broker_session") if isinstance(result, Mapping) else None
        if not isinstance(evidence, Mapping) or evidence.get("mode") != "simulation_actions":
            raise PermissionError("configured simulation broker did not prove simulation_actions mode")
        self._simulation_verified = True

    async def _visible(self) -> frozenset[str]:
        response = await self.session.list_tools()
        return frozenset(tool.name for tool in response.tools)

    async def permitted(self, tool: str) -> bool:
        # Refresh on every request so startup discovery never becomes authority.
        broker_tool = {
            "device_info": "robot_capabilities",
            "device_state": "robot_state",
            "device_stop": "stop",
            "action_cancel": "stop",
            "action_lookup": "robot_state",
        }.get(tool, tool)
        return broker_tool in self.allowed_tools and broker_tool in await self._visible()

    async def _call(self, tool: str, args: dict[str, object] | None = None) -> dict[str, object]:
        if not await self.permitted(tool):
            raise PermissionError("broker_tool_unavailable")
        response = await self.session.call_tool(tool, args or {})
        body = response.structured_content
        if body is None and not response.is_error:
            # MCP servers may return JSON text rather than structuredContent.
            # Accept only one JSON object; never infer success from prose.
            texts = [item.text for item in response.content if getattr(item, "type", None) == "text"]
            if len(texts) == 1:
                try:
                    body = json.loads(texts[0])
                except (TypeError, ValueError):
                    body = None
        if response.is_error or not isinstance(body, Mapping):
            raise RuntimeError("broker_request_failed")
        return dict(body)

    async def describe(self) -> DeviceDescriptor:
        await self._verify_simulation()
        if not await self.permitted("robot_capabilities"):
            return DeviceDescriptor(id=self.instance_id, provider="soarm101", connected=False,
                                    ready=False, authority={"source": "broker", "status": "unavailable"})
        caps = await self._call("robot_capabilities")
        body = caps.get("result", caps)
        if not isinstance(body, Mapping):
            raise TypeError("invalid_broker_capabilities")
        tools = tuple(sorted(self.allowed_tools & await self._visible()))
        controls = tuple(ControlDescriptor(id=name, description=f"SO-ARM broker {name}",
            effect="stop" if name == "stop" else "read" if name in {"robot_health", "robot_state", "robot_capabilities", "list_assets", "capture_camera"} else "motion",
            parameters_schema={"type": "object"}, resources=("arm.motion",) if name in {
                "go_pose", "jog_joint", "jog_cartesian", "move_gripper", "sleep", "sleep_up",
                "replay_recording",
                "replay_sequence",
            } else ())
            for name in tools)
        authority = body.get("authority", {})
        return DeviceDescriptor(id=self.instance_id, provider="soarm101", connected=True,
            ready=True, supported_operations=tools, controls=controls, feedback="measured",
            authority=dict(authority) if isinstance(authority, Mapping) else {"source": "broker"})

    async def state(self) -> dict[str, object]:
        return await self._call("robot_state")

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        # The broker returns a page and next_offset. Follow that wire contract
        # instead of assuming the catalog fits in a single page.
        assets: list[AssetDescriptor] = []
        offset = 0
        while True:
            response = await self._call("list_assets", {"offset": offset, "limit": 100})
            body = response.get("result", response)
            # soarm101-motion-sdk's /v1/assets MCP result is
            # {items, offset, next_offset}; retain that exact contract here.
            items = body.get("items") if isinstance(body, Mapping) else None
            next_offset = body.get("next_offset") if isinstance(body, Mapping) else None
            if not isinstance(items, list):
                raise TypeError("invalid_broker_asset_catalog")
            for item in items:
                if not isinstance(item, Mapping):
                    raise TypeError("invalid_broker_asset")
                # Convert the broker's compatibility/provenance vocabulary into
                # the device-neutral contract instead of passing unknown fields
                # through Pydantic (which intentionally forbids extras).
                compatibility = {
                    "calibration": item.get("compatibility", "unchecked"),
                    "detail": item.get("compatibility_detail", ""),
                    "provenance": dict(item.get("provenance", ())),
                }
                normalized = {
                    key: item[key]
                    for key in ("id", "kind", "source", "revision", "description", "duration_s", "resources")
                    if key in item
                }
                normalized["device_compatibility"] = compatibility
                assets.append(AssetDescriptor.model_validate(normalized))
            if next_offset is None:
                break
            if type(next_offset) is not int or next_offset <= offset or next_offset > offset + len(items):
                raise TypeError("invalid_broker_asset_pagination")
            offset = next_offset
        return tuple(assets)

    async def go_pose(self, name: str, *, speed_deg_s: float | None = None,
                      acceleration_deg_s2: float | None = None) -> dict[str, object]:
        args = PoseArgs(name=name, speed_deg_s=speed_deg_s,
                        acceleration_deg_s2=acceleration_deg_s2)
        return await self._call("go_pose", args.model_dump(exclude_none=True))

    async def jog_cartesian(self, *, frame: Literal["world", "tool"], x_mm: float = 0,
                            y_mm: float = 0, z_mm: float = 0,
                            speed_mm_s: float | None = None,
                            acceleration_mm_s2: float | None = None) -> dict[str, object]:
        args = CartesianArgs(frame=frame, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm,
                             speed_mm_s=speed_mm_s, acceleration_mm_s2=acceleration_mm_s2)
        return await self._call("jog_cartesian", args.model_dump(exclude_none=True))

    async def sleep(self) -> dict[str, object]:
        return await self._call("sleep")

    async def sleep_up(self) -> dict[str, object]:
        return await self._call("sleep_up")

    async def move_gripper(
        self, target: Literal["open", "close"],
        *, gripper_speed_raw: int | None = None,
        gripper_acceleration_raw: int | None = None,
    ) -> dict[str, object]:
        args = GripperArgs(
            target=target, gripper_speed_raw=gripper_speed_raw,
            gripper_acceleration_raw=gripper_acceleration_raw,
        )
        return await self._call("move_gripper", args.model_dump(exclude_none=True))

    async def capture_camera(self, camera: Literal["overhead", "wrist"]) -> tuple[dict[str, object], str]:
        """Verify the broker's original pixel SHA-256 before returning MCP image bytes."""
        if camera not in {"overhead", "wrist"}:
            raise ValueError("unknown camera")
        response = await self._call("capture_camera", {"camera": camera})
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("invalid_broker_camera_result")
        encoded, claimed = result.get("image_base64"), result.get("sha256")
        if not isinstance(encoded, str) or not isinstance(claimed, str) or not claimed:
            raise RuntimeError("broker_camera_image_or_digest_missing")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error):
            raise RuntimeError("invalid_broker_camera_image") from None
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), claimed):
            raise RuntimeError("broker_camera_digest_mismatch")
        meta = {
            key: result[key]
            for key in ("name", "timestamp", "width", "height", "sha256")
            if key in result
        }
        meta["request_id"] = response.get("request_id")
        return meta, encoded

    async def jog_joint(self, *, joint: str, delta_deg: float,
                        speed_deg_s: float | None = None,
                        acceleration_deg_s2: float | None = None) -> dict[str, object]:
        args = JointJogArgs(joint=joint, delta_deg=delta_deg, speed_deg_s=speed_deg_s,
                            acceleration_deg_s2=acceleration_deg_s2)
        return await self._call("jog_joint", args.model_dump(exclude_none=True))

    async def stop(self) -> StopResult:
        response = await self._call("stop")
        body = response.get("result", response)
        if not isinstance(body, Mapping):
            return StopResult(False, "invalid_broker_stop_response")
        confirmed = (
            body.get("confirmed") is True
            or body.get("status") == "stopped"
            or body.get("completed") is True and body.get("holding") is True
        )
        evidence = body.get("evidence") or body.get("completion_evidence") or body.get("status")
        if evidence is None and confirmed:
            evidence = "broker_stop_completed_and_holding"
        return StopResult(confirmed, str(evidence or "broker_stop_unconfirmed"))

    # ExecutionRuntime adapter surface. Every validation and dispatch refreshes
    # broker tool visibility; the configured grant is only an upper bound.
    async def validate(self, request: ActionRequest) -> None:
        await self._verify_simulation()
        if request.performer_id != self.instance_id:
            raise ValueError("invalid SO-ARM action target")
        replay = request.operation in {"replay_recording", "replay_sequence"}
        if replay:
            if (not self.simulation_only or not request.asset_id or not request.asset_revision
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request.id)):
                raise PermissionError("pinned replay requires simulation-only mode and exact identity")
            expected_prefix = ("soarm101/recording/", "soarm101/sequence/")
            if not request.asset_id.startswith(expected_prefix[request.operation == "replay_sequence"]):
                raise ValueError("asset kind does not match replay operation")
            ReplayArgs.model_validate({**request.parameters, "asset_id": request.asset_id,
                                       "revision": request.asset_revision})
            if "list_assets" not in self.allowed_tools or "list_assets" not in await self._visible():
                raise PermissionError("replay_catalog_unavailable")
            catalog = await self.list_assets()
            if not any(asset.id == request.asset_id and asset.revision == request.asset_revision
                       for asset in catalog):
                raise PermissionError("replay_asset_revision_denied")
            tool = request.operation
        elif request.asset_id or request.asset_revision:
            raise ValueError("asset identity is only valid for pinned replay")
        elif request.operation == "go_pose":
            PoseArgs.model_validate(request.parameters)
            tool = "go_pose"
        elif request.operation == "jog_cartesian":
            CartesianArgs.model_validate(request.parameters)
            tool = "jog_cartesian"
        elif request.operation in {"sleep", "sleep_up"}:
            SleepArgs.model_validate(request.parameters)
            tool = request.operation
        elif request.operation == "move_gripper":
            GripperArgs.model_validate(request.parameters)
            tool = "move_gripper"
        elif request.operation == "jog_joint":
            JointJogArgs.model_validate(request.parameters)
            tool = "jog_joint"
        else:
            raise ValueError("unsupported SO-ARM operation")
        if not await self.permitted(tool):
            raise PermissionError("broker_tool_unavailable")

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        if request.operation not in {
            "go_pose", "jog_joint", "jog_cartesian", "sleep", "sleep_up",
            "move_gripper", "replay_recording", "replay_sequence",
        }:
            raise ValueError("unsupported SO-ARM operation")
        return ("arm.motion",)

    async def execute(self, request: ActionRequest) -> Completion:
        await self.validate(request)
        if request.operation in {"replay_recording", "replay_sequence"}:
            if not await self.permitted("robot_state"):
                raise PermissionError("broker_session_unavailable")
            state = await self._call("robot_state")
            state_body = state.get("result", state)
            broker_session = state_body.get("broker_session") if isinstance(state_body, Mapping) else None
            broker_session_id = broker_session.get("id") if isinstance(broker_session, Mapping) else None
            if not isinstance(broker_session_id, str) or not broker_session_id:
                raise RuntimeError("broker_session_identity_unavailable")
            args = {"request_id": request.id, "session_id": broker_session_id,
                    "asset_id": request.asset_id, "revision": request.asset_revision,
                    **request.parameters}
        else:
            args = dict(request.parameters)
        response = await self._call(request.operation, args)
        body = response.get("result", response)
        if not isinstance(body, Mapping):
            raise TypeError("invalid_broker_completion")
        # Only explicit broker completion evidence can close the shared action.
        status = body.get("status")
        evidence = body.get("completion_evidence") or body.get("evidence")
        if request.operation in {"replay_recording", "replay_sequence"}:
            # SDK recording replay wraps its MotionResult under `completion`,
            # while sequence replay reports `completed` at the response root.
            # Both are authoritative broker evidence; an accepted/queued ACK
            # remains insufficient to complete the shared Forge action.
            replay_completion = body.get("completion")
            completed = body.get("completed") is True or (
                isinstance(replay_completion, Mapping)
                and replay_completion.get("completed") is True
            )
            if not completed:
                raise RuntimeError("broker_completion_unconfirmed")
            if evidence is None and isinstance(replay_completion, Mapping):
                evidence = replay_completion.get("message") or "broker_recording_replay_completed"
            return Completion(str(evidence or "broker_replay_completed"))
        successful_status = isinstance(status, str) and status in {"completed", "succeeded"}
        completed = successful_status or (
            body.get("completed") is True and status is None
        )
        if not completed:
            raise RuntimeError("broker_completion_unconfirmed")
        return Completion(str(evidence or status or "broker_completed"))
