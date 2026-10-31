"""Typed Reachy recorded playback and native controls over shared lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ..execution import ActionRequest
from .server import Gateway

if TYPE_CHECKING:
    from mcp.server import MCPServer


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlayParameters(_Parameters):
    sound: bool = False


class HeadParameters(_Parameters):
    pose: list[list[float]] = Field(min_length=4, max_length=4)
    duration_s: float = Field(default=0.5, ge=0.05, le=5.0)


class AntennaParameters(_Parameters):
    angles_rad: list[float] = Field(min_length=2, max_length=2)
    duration_s: float = Field(default=0.5, ge=0.05, le=5.0)


def register_reachy_tools(server: MCPServer, gateway: Gateway) -> None:
    """Only fixed, typed operations; every motion passes through Gateway/runtime."""

    @server.tool()
    async def play_move(
        action_id: str, caller_id: str, session_id: str,
        asset_id: str, revision: str, parameters: PlayParameters | None = None,
    ) -> dict[str, object]:
        """Play one exact snapshot move and revision; sound is opt-in."""
        return await gateway.submit("play_move", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="play_move", asset_id=asset_id,
            asset_revision=revision,
            parameters=(parameters or PlayParameters()).model_dump(),
        ))

    @server.tool()
    async def head_pose(
        action_id: str, caller_id: str, session_id: str, parameters: HeadParameters,
    ) -> dict[str, object]:
        """Move head with a bounded homogeneous pose matrix."""
        return await gateway.submit("head_pose", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="head_pose",
            parameters=parameters.model_dump(),
        ))

    @server.tool()
    async def antenna_targets(
        action_id: str, caller_id: str, session_id: str, parameters: AntennaParameters,
    ) -> dict[str, object]:
        """Set bounded right/left antenna targets in radians."""
        return await gateway.submit("antenna_targets", ActionRequest(
            id=action_id, performer_id=gateway.performer_id, caller_id=caller_id,
            session_id=session_id, operation="antenna_targets",
            parameters=parameters.model_dump(),
        ))
