"""Typed SO-ARM broker operations exposed through the shared MCP lifecycle."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..execution import ActionRequest
from .server import Gateway
from .soarm_guidance import GUIDANCE_RESOURCES, task_prompt

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from ..devices.soarm101 import SOARM101Provider


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PoseParameters(_Parameters):
    name: str = Field(min_length=1)
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)
    gripper_speed_raw: int | None = Field(default=None, ge=0)
    gripper_acceleration_raw: int | None = Field(default=None, ge=0)


class CartesianParameters(_Parameters):
    frame: Literal["world", "tool"]
    x_mm: float = 0
    y_mm: float = 0
    z_mm: float = 0
    speed_mm_s: float | None = Field(default=None, gt=0)
    acceleration_mm_s2: float | None = Field(default=None, gt=0)


class JointParameters(_Parameters):
    joint: Literal["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
    delta_deg: float = Field(ge=-2, le=2)
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)

    @field_validator("delta_deg")
    @classmethod
    def nonzero_delta(cls, value: float) -> float:
        if value == 0:
            raise ValueError("joint delta must not be zero")
        return value


class ReplayParameters(_Parameters):
    asset_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    speed_scale: float = Field(gt=0, le=1)


def register_soarm101_tools(server: MCPServer, gateway: Gateway) -> None:
    """Expose only fixed broker operations; operation names are never caller-selected."""
    provider = cast("SOARM101Provider", gateway.provider)
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    @server.tool()
    async def go_pose(
        action_id: str,
        caller_id: str,
        session_id: str,
        parameters: PoseParameters,
    ) -> dict[str, object]:
        """Move to a named broker pose with optional bounded speed settings."""
        return await gateway.submit(
            "go_pose",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="go_pose",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def jog_cartesian(
        action_id: str,
        caller_id: str,
        session_id: str,
        parameters: CartesianParameters,
    ) -> dict[str, object]:
        """Apply a bounded Cartesian offset in world or tool coordinates."""
        return await gateway.submit(
            "jog_cartesian",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="jog_cartesian",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def jog_joint(
        action_id: str,
        caller_id: str,
        session_id: str,
        parameters: JointParameters,
    ) -> dict[str, object]:
        """Apply a profile-bounded joint offset through the broker."""
        return await gateway.submit(
            "jog_joint",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="jog_joint",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def sleep(
        action_id: str, caller_id: str, session_id: str,
        speed_deg_s: float | None = None,
        acceleration_deg_s2: float | None = None,
        gripper_speed_raw: int | None = None,
        gripper_acceleration_raw: int | None = None,
    ) -> dict[str, object]:
        """Request the broker's saved Sleep pose through shared action ownership."""
        parameters = {
            key: value for key, value in {
                "speed_deg_s": speed_deg_s,
                "acceleration_deg_s2": acceleration_deg_s2,
                "gripper_speed_raw": gripper_speed_raw,
                "gripper_acceleration_raw": gripper_acceleration_raw,
            }.items() if value is not None
        }
        return await gateway.submit("sleep", ActionRequest(
            id=action_id, performer_id=gateway.performer_id,
            caller_id=caller_id, session_id=session_id,
            operation="sleep", parameters=parameters,
        ))

    @server.tool()
    async def replay_recording(
        action_id: str, caller_id: str, session_id: str, parameters: ReplayParameters,
    ) -> dict[str, object]:
        """Replay an exact operator-pinned recording in SDK simulation only."""
        return await gateway.submit("replay_recording", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="replay_recording",
            parameters={"speed_scale": parameters.speed_scale}, asset_id=parameters.asset_id,
            asset_revision=parameters.revision,
        ))

    @server.tool()
    async def replay_sequence(
        action_id: str, caller_id: str, session_id: str, parameters: ReplayParameters,
    ) -> dict[str, object]:
        """Replay an exact operator-pinned sequence in SDK simulation only."""
        return await gateway.submit("replay_sequence", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="replay_sequence",
            parameters={"speed_scale": parameters.speed_scale}, asset_id=parameters.asset_id,
            asset_revision=parameters.revision,
        ))

    @server.tool(annotations=read_only)
    async def robot_health() -> dict[str, object]:
        """Read authenticated SO-ARM broker health; this does not arm the robot."""
        from mcp.server.mcpserver.exceptions import ToolError
        await gateway.check("robot_health")
        try:
            return await provider._call("robot_health")
        except Exception:
            raise ToolError("broker_read_failed") from None

    @server.tool(annotations=read_only)
    async def robot_capabilities() -> dict[str, object]:
        """Read live bounded capabilities, calibration and authority evidence."""
        from mcp.server.mcpserver.exceptions import ToolError
        await gateway.check("robot_capabilities")
        try:
            return await provider._call("robot_capabilities")
        except Exception:
            raise ToolError("broker_read_failed") from None

    @server.tool(annotations=read_only)
    async def robot_state() -> dict[str, object]:
        """Read fresh broker state rather than infer motion success."""
        from mcp.server.mcpserver.exceptions import ToolError
        await gateway.check("robot_state")
        try:
            return await provider.state()
        except Exception:
            raise ToolError("broker_read_failed") from None

    @server.tool(annotations=read_only, structured_output=False)
    async def capture_camera(camera: Literal["overhead", "wrist"]):
        """Return verified broker JPEG pixels and separate non-sensitive metadata."""
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ImageContent, TextContent

        await gateway.check("capture_camera")
        try:
            metadata, image = await provider.capture_camera(camera)
        except Exception:
            raise ToolError("broker_camera_unavailable_or_unverified") from None
        return [
            TextContent(type="text", text=json.dumps(metadata, sort_keys=True)),
            ImageContent(type="image", data=image, mime_type="image/jpeg"),
        ]

    @server.tool()
    async def move_gripper(
        action_id: str, caller_id: str, session_id: str,
        target: Literal["open", "close"],
        gripper_speed_raw: int | None = None,
        gripper_acceleration_raw: int | None = None,
    ) -> dict[str, object]:
        """Move the gripper through the SDK broker, under shared runtime ownership."""
        return await gateway.submit("move_gripper", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="move_gripper",
            parameters={k: v for k, v in {
                "target": target,
                "gripper_speed_raw": gripper_speed_raw,
                "gripper_acceleration_raw": gripper_acceleration_raw,
            }.items() if v is not None},
        ))

    @server.tool()
    async def sleep_up(
        action_id: str, caller_id: str, session_id: str,
        speed_deg_s: float | None = None,
        acceleration_deg_s2: float | None = None,
        gripper_speed_raw: int | None = None,
        gripper_acceleration_raw: int | None = None,
    ) -> dict[str, object]:
        """Request the historical Sleep-up pose with broker safety checks."""
        parameters = {
            key: value for key, value in {
                "speed_deg_s": speed_deg_s,
                "acceleration_deg_s2": acceleration_deg_s2,
                "gripper_speed_raw": gripper_speed_raw,
                "gripper_acceleration_raw": gripper_acceleration_raw,
            }.items() if value is not None
        }
        return await gateway.submit("sleep_up", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="sleep_up", parameters=parameters,
        ))

    # The SDK used to own these MCP semantic resources and the task prompt.
    # They are hints only: SDK broker profile and measured state stay authoritative.
    for guidance_name, guidance_text in GUIDANCE_RESOURCES.items():
        def add_resource(name: str, content: str) -> None:
            @server.resource(
                f"soarm101://guidance/{name}",
                name=f"soarm101-guidance-{name}",
                title=f"SO-ARM101 {name} guidance",
                description="Static guidance only; no device permission or live state.",
                mime_type="text/markdown",
            )
            def guidance() -> str:
                return content
        add_resource(guidance_name, guidance_text)

    @server.prompt(
        name="operate_robot_task", title="Operate bounded SO-ARM101 task",
        description="Broker-restricted, evidence-first robot task guidance.",
    )
    def operate_robot_task(task: str, strategy: str = "auto") -> str:
        return task_prompt(task, strategy)
