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
from ..stackchan_speech import stackchan_native_mouth_cue

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
    speech_dynamics: Mapping[str, str] | None = None,
    stackchan_performer_id: str | None = "stackchan",
    startup_settle_s: float = 0.0,
    native_gain_performers: tuple[str, ...] = (),
    native_motion_performers: tuple[str, ...] = (),
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

    speech_window = SpeechEnvelopeWindow(performer_dynamics=speech_dynamics)
    if len(set(native_gain_performers)) != len(native_gain_performers) or not set(
        native_gain_performers
    ) <= set(ensemble.packs):
        raise ValueError("native gain performers must be unique configured performers")
    if native_gain_performers:
        from ..reachy_native_speech import NativeSpeechGainWindow
        native_windows = {name: NativeSpeechGainWindow(allow_gaps=True)
                          for name in native_gain_performers}
    else:
        native_windows = {}
    if (len(set(native_motion_performers)) != len(native_motion_performers)
            or not set(native_motion_performers) <= set(native_windows)):
        raise ValueError("native motion requires unique configured gain performers")
    for performer in native_motion_performers:
        provider = runtime._provider(performer)
        bind = getattr(provider, "bind_native_speech_source", None)
        if not callable(bind):
            raise TypeError("native speech provider cannot bind a trusted gain source")
        bind(native_windows[performer].latest)
    native_armed = False
    if native_windows:
        def invalidate_native_on_presence(event: EnsemblePresence) -> None:
            nonlocal native_armed
            previous = ensemble.snapshot()
            native_armed = False
            if (event.state != "speaking" or event.interrupted
                    or previous["session_id"] != event.session_id or previous["stale"]):
                for window in native_windows.values():
                    window.invalidate()

        ensemble.add_presence_observer(invalidate_native_on_presence)
    if set(speech_dynamics or {}) - set(ensemble.packs):
        raise ValueError("speech dynamics refer to an unknown performer")
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
                "active": latest.active, "stream_id": latest.stream_id,
                "sequence": latest.sequence, "mouth": latest.mouth,
            }

    for performer in sorted(speech_driven):
        if performer == stackchan_performer_id and (
            (speech_dynamics or {}).get(performer) == "smoothed_v1"
        ):
            # Latest-hop smoothed mouth WEIGHT, including one release on
            # silence. No duplicate firmware speaking modifier or motor loop.
            ensemble.bind_speech_channel(
                performer, _WindowChannel(performer), amplitude=0.08,
                warp=stackchan_native_mouth_cue,
                release_on_silence=True, interval_s=0.2,
            )
        elif performer == stackchan_performer_id:
            # Keep StackChan's own speaking character: the envelope gates the
            # timing, the firmware port chooses each subtle nod or sway. The
            # interval must stay well under a typical utterance, otherwise the
            # next presence transition cancels the target before it completes.
            ensemble.bind_speech_channel(
                performer, _WindowChannel(performer), amplitude=0.08,
                warp=stackchan_firmware_warp(
                    seed=speech_seeds.get(performer, 7),
                ),
                interval_s=0.6,
            )
        else:
            # Measured on hardware: a 20 deg command produces ~18.7 deg of real
            # yaw, so the tapper's own loudness scaling already attenuates the
            # signal. The channel therefore uses the operator's full expressive
            # amplitude rather than a fraction of it, and dispatches faster than
            # the 0.3 s target so speech reads as continuous motion.
            ensemble.bind_speech_channel(
                performer, _WindowChannel(performer), amplitude=0.12,
                neutral_head=(speech_neutral_heads or {}).get(performer),
                interval_s=0.2,
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
                for performer in native_motion_performers:
                    if not await runtime._provider(performer).permitted("original_speech"):
                        raise RuntimeError("native speech SDK capability unavailable")
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
                "native_speech_gain": {
                    name: window.snapshot() for name, window in native_windows.items()
                },
                "presentation_presets_configured": {
                    name: sorted(options) for name, options in presets.items()
                }}

    @server.tool()
    async def ensemble_presence(
        session_id: str, event_id: str, sequence: int,
        state: str, source: str = "agent_state", interrupted: bool = False,
    ) -> dict[str, object]:
        """Ordered shared idle/listening/speaking/disconnected event for all robots."""
        nonlocal native_armed
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
            if result.get("outcome") == "accepted":
                native_armed = event.state == "speaking" and not event.interrupted
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

    if native_windows:
        @server.tool()
        async def ensemble_native_speech_gain(
            session_id: str, performer_id: str, frames: list[dict[str, object]],
        ) -> dict[str, object]:
            """Observe bounded generated-PCM gain; no robot action is submitted."""
            if not motion_enabled:
                raise ToolError("ensemble_motion_not_operator_authorized")
            status = ensemble.snapshot()
            if (session_id != status["session_id"] or status["state"] != "speaking"
                    or status["stale"] or not native_armed):
                return {"outcome": "denied", "reason": "inactive_speaking_session"}
            window = native_windows.get(performer_id)
            if window is None:
                return {"outcome": "denied", "reason": "unknown_performer"}
            try:
                return window.accept(frames)
            except (TypeError, ValueError):
                raise ToolError("invalid_or_stale_native_speech_gain") from None

    return server
