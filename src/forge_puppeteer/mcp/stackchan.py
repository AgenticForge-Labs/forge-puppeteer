"""Explicit StackChan tools for the common trusted-local MCP gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ..execution import ActionRequest
from .server import Gateway

if TYPE_CHECKING:
    from mcp.server import MCPServer


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ServoTarget(_Parameters):
    angle: int = Field(strict=True)
    speed: int = Field(strict=True, ge=0, le=1000)


class HeadParameters(_Parameters):
    yaw: ServoTarget | None = None
    pitch: ServoTarget | None = None


class FaceFeature(_Parameters):
    x: int | None = Field(default=None, strict=True, ge=-100, le=100)
    y: int | None = Field(default=None, strict=True, ge=-100, le=100)
    rotation: int | None = Field(default=None, strict=True, ge=0, le=3600)
    weight: int | None = Field(default=None, strict=True, ge=0, le=100)
    size: int | None = Field(default=None, strict=True, ge=-100, le=100)


class FaceParameters(_Parameters):
    left_eye: FaceFeature | None = None
    right_eye: FaceFeature | None = None
    mouth: FaceFeature | None = None


class LightParameters(_Parameters):
    left_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    right_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    left_duration: float | None = Field(default=None, ge=0, le=10)
    right_duration: float | None = Field(default=None, ge=0, le=10)


def register_stackchan_tools(server: MCPServer, gateway: Gateway) -> None:
    """Register fixed native operations; caller cannot select a Python method or path."""

    @server.tool()
    async def head_move(
        action_id: str, caller_id: str, session_id: str, parameters: HeadParameters
    ) -> dict[str, object]:
        """Set bounded native head targets in tenths of a degree."""
        return await gateway.submit(
            "head_move",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="head",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def face_update(
        action_id: str, caller_id: str, session_id: str, parameters: FaceParameters
    ) -> dict[str, object]:
        """Update native face features without moving servos."""
        return await gateway.submit(
            "face_update",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="face",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def lights_update(
        action_id: str, caller_id: str, session_id: str, parameters: LightParameters
    ) -> dict[str, object]:
        """Update built-in lighting with explicit colors and durations."""
        return await gateway.submit(
            "lights_update",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="lights",
                parameters=parameters.model_dump(exclude_none=True),
            ),
        )

    @server.tool()
    async def play_asset(
        action_id: str, caller_id: str, session_id: str, asset_id: str, revision: str
    ) -> dict[str, object]:
        """Play an exact configured asset revision through the device SDK."""
        return await gateway.submit(
            "play_asset",
            ActionRequest(
                id=action_id,
                performer_id=gateway.performer_id,
                caller_id=caller_id,
                session_id=session_id,
                operation="play_asset",
                asset_id=asset_id,
                asset_revision=revision,
            ),
        )
