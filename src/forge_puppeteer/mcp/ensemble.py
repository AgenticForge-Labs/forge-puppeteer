"""One operator-owned stdio MCP room for multiple native robot performers.

This is a presentation boundary, not a second motion/STOP controller. It
exposes only a fixed semantic vocabulary, never raw paths, servo commands,
operator policy documents or arbitrary tool dispatch. No personality or
business tools are stored in Puppeteer.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from ..behavior_contract import EnsemblePresence
from ..conversation_ensemble import ConversationEnsemble
from ..execution import ExecutionRuntime
from ..speech_expression import SpeechEnvelopeWindow

if TYPE_CHECKING:
    from mcp.server import MCPServer


def create_ensemble_server(
    runtime: ExecutionRuntime,
    ensemble: ConversationEnsemble,
    *,
    connected: Callable | None = None,
    name: str = "Forge Puppeteer Ensemble",
    motion_enabled: bool = False,
) -> MCPServer:
    """An opt-in single-room stdio server with operator startup gate.

    The operator owns the host process and devices; stdio is not authentication.
    Config must not be sourced from a model/tool argument. Read-only status
    works without the explicit motion gate.
    """
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError as exc:
        raise RuntimeError("Install forge-puppeteer[mcp] for ensemble tools") from exc

    speech_window = SpeechEnvelopeWindow()

    @asynccontextmanager
    async def lifespan(_server):
        try:
            async with AsyncExitStack() as stack:
                if connected is not None:
                    await stack.enter_async_context(connected())
                await stack.enter_async_context(runtime.run())
                await stack.enter_async_context(ensemble.run())
                yield {}
        finally:
            runtime.close()

    server = MCPServer(name, lifespan=lifespan,
        instructions="Semantic physical performance only. A conversation persona and its tools "
        "are owned by the caller; this server does not grant general robot control.")

    @server.tool()
    async def ensemble_state() -> dict[str, object]:
        """Current room state and independent per-performer action evidence."""
        return {"outcome": "observed", **ensemble.snapshot(),
                "speech_envelope": speech_window.snapshot()}

    @server.tool()
    async def ensemble_presence(
        session_id: str, event_id: str, sequence: int,
        state: str, source: str = "agent_state", interrupted: bool = False,
    ) -> dict[str, object]:
        """Ordered shared idle/listening/speaking/disconnected event for all robots."""
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        try:
            event = EnsemblePresence.model_validate({
                "session_id": session_id, "event_id": event_id,
                "sequence": sequence, "state": state,
                "source": source, "interrupted": interrupted,
            })
            result = await ensemble.observe(event)
            if event.interrupted or event.state == "disconnected":
                speech_window.clear()
            return result
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            raise ToolError("invalid_or_unavailable_ensemble_presence") from None

    @server.tool()
    async def ensemble_expression(
        expression: str, action_id: str,
        performer_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Finite named gesture; each performer must have an approved mapping."""
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        try:
            return await ensemble.express(
                expression, action_id=action_id,
                performer_ids=tuple(performer_ids) if performer_ids is not None else None,
            )
        except Exception:  # noqa: BLE001 - all provider failures map to a safe MCP error
            raise ToolError("invalid_or_unavailable_ensemble_expression") from None


    @server.tool()
    async def ensemble_speech_envelope(
        session_id: str, frames: list[dict[str, object]],
    ) -> dict[str, object]:
        """Optional generated-PCM feature ingress; preview only, NO SDK motion.

        This is a room-owned, content-free observation tool, not an LLM tool
        or an extra actuator scheduler. Existing action/STOP authority is
        unchanged. The feature source is not a measured speaker clock.
        """
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        if not session_id or session_id != ensemble.snapshot()["session_id"]:
            return {"outcome": "denied", "reason": "inactive_or_foreign_room"}
        try:
            result = speech_window.accept(
                frames, performer_ids=tuple(ensemble.packs),
            )
            return {"outcome": "observed", **result}
        except Exception:
            raise ToolError("invalid_or_stale_speech_envelope") from None

    return server
