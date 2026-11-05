"""One operator-owned stdio MCP room for multiple native robot performers.

This is a presentation boundary, not a second motion/STOP controller. It
exposes only a fixed semantic vocabulary, never raw paths, servo commands,
operator policy documents or arbitrary tool dispatch. No personality or
business tools are stored in Puppeteer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from ..behavior_contract import BehaviorCue, EnsemblePresence
from ..conversation_ensemble import ConversationEnsemble
from ..conversation_native import stackchan_firmware_warp
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
    presentation_presets: Mapping[str, Mapping[str, BehaviorCue]] | None = None,
    speech_driven_performers: tuple[str, ...] = (),
    speech_neutral_heads: Mapping[str, object] | None = None,
    speech_seeds: Mapping[str, int] | None = None,
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
    # Opt-in audio-driven wording texture. Disabled unless every bound
    # performer was explicitly authorized by the operator config.
    speech_driven = frozenset(speech_driven_performers or ())
    speech_seeds = dict(speech_seeds or {})
    if not speech_driven <= set(ensemble.packs):
        raise ValueError("speech-driven performers must be configured performers")

    class _WindowChannel:
        """Adapter exposing only the latest unitless channels to the ensemble."""

        def __init__(self, performer_id: str) -> None:
            self._performer_id = performer_id

        def latest(self) -> dict[str, float] | None:
            latest = speech_window.latest(self._performer_id)
            if latest is None:
                return None
            return {
                "pitch": latest.pitch, "yaw": latest.yaw, "roll": latest.roll,
                "z": latest.z, "antenna": latest.antenna,
            }

    for performer in sorted(speech_driven):
        if performer == "stackchan":
            # Keep StackChan's own speaking character: the envelope gates the
            # timing, the firmware port chooses each subtle nod or sway.
            ensemble.bind_speech_channel(
                performer, _WindowChannel(performer), amplitude=0.08,
                warp=stackchan_firmware_warp(
                    seed=speech_seeds.get(performer, 7),
                ),
                interval_s=2.0,
            )
        else:
            ensemble.bind_speech_channel(
                performer, _WindowChannel(performer), amplitude=0.09,
                neutral_head=(speech_neutral_heads or {}).get(performer),
                interval_s=0.25,
            )
    # This is operator-selected trusted code/data, never LLM-provided cues.
    presets = {name: dict(options) for name, options in (presentation_presets or {}).items()}
    if any(name not in ensemble.packs or not options for name, options in presets.items()):
        raise ValueError("presentation presets must bind configured performers")

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
                "speech_envelope": speech_window.snapshot(),
                "presentation_presets_configured": {
                    name: sorted(options) for name, options in presets.items()
                }}

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
    async def ensemble_presentation(
        performer_id: str, preset: str, action_id: str,
    ) -> dict[str, object]:
        """Explicit source-controlled, finite visual preset; not a free-form SDK call."""
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        cue = presets.get(performer_id, {}).get(preset)
        if cue is None:
            return {"outcome": "unsupported", "reason": "unmapped_presentation_preset"}
        try:
            return await ensemble.present(
                performer_id, preset, action_id=action_id, cue=cue,
            )
        except (TypeError, ValueError, RuntimeError):
            raise ToolError("invalid_or_unavailable_ensemble_presentation") from None

    @server.tool()
    async def ensemble_speech_envelope(
        session_id: str, frames: list[dict[str, object]],
        performer_id: str | None = None,
    ) -> dict[str, object]:
        """Optional generated-PCM feature ingress; preview only, NO SDK motion.

        This is a room-owned, content-free observation tool, not an LLM tool
        or an extra actuator scheduler. Existing action/STOP authority is
        unchanged. The feature source is not a measured speaker clock.

        ``performer_id`` attributes the batch so two robots sharing one
        runtime are gated independently. An attributed batch only feeds that
        performer's channel; omitting it keeps the original behaviour of
        feeding every configured channel.
        """
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        if not session_id or session_id != ensemble.snapshot()["session_id"]:
            return {"outcome": "denied", "reason": "inactive_or_foreign_room"}
        if performer_id is not None and performer_id not in ensemble.packs:
            return {"outcome": "denied", "reason": "unknown_performer"}
        try:
            result = speech_window.accept(
                frames,
                performer_ids=(
                    (performer_id,) if performer_id is not None
                    else tuple(ensemble.packs)
                ),
            )
            return {"outcome": "observed", **result}
        except (TypeError, ValueError):
            raise ToolError("invalid_or_stale_speech_envelope") from None

    return server
