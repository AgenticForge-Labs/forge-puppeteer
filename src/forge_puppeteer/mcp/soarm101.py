"""Typed SO-ARM broker operations exposed through the shared MCP lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..execution import ActionRequest
from .server import Gateway

if TYPE_CHECKING:
    from mcp.server import MCPServer


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PoseParameters(_Parameters):
    name: str = Field(min_length=1)
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)


class CartesianParameters(_Parameters):
    frame: Literal["world", "tool"]
    x_mm: float = 0
    y_mm: float = 0
    z_mm: float = 0
    speed_mm_s: float | None = Field(default=None, gt=0)
    acceleration_mm_s2: float | None = Field(default=None, gt=0)


class JointParameters(_Parameters):
    joint: Literal["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
    delta_deg: float = Field(gt=0, le=2)
    speed_deg_s: float | None = Field(default=None, gt=0)
    acceleration_deg_s2: float | None = Field(default=None, gt=0)


class ReplayParameters(_Parameters):
    asset_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    speed_scale: float = Field(gt=0, le=1)


def register_soarm101_tools(server: MCPServer, gateway: Gateway) -> None:
    """Expose only fixed broker operations; operation names are never caller-selected."""

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
        action_id: str, caller_id: str, session_id: str
    ) -> dict[str, object]:
        """Request the broker's saved Sleep pose through shared action ownership."""
        return await gateway.submit(
            "sleep",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="sleep",
            ),
        )

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
