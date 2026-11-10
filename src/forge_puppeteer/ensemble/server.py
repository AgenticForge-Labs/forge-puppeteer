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

from ..behavior_contract import BehaviorCue, BehaviorPattern, EnsemblePresence
from ..conversation_ensemble import ConversationEnsemble
from ..conversation_native import stackchan_firmware_warp
from ..execution import ExecutionRuntime
from ..speech_expression import SpeechEnvelopeWindow
from ..stackchan_speech import stackchan_native_mouth_cue
from ..startup_gate import StartupGate

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
    speech_fallbacks: Mapping[str, BehaviorPattern] | None = None,
    live_presences: Mapping[str, object] | None = None,
    stackchan_performer_id: str | None = "stackchan",
    startup_settle_s: float = 0.0,
    startup_gate: StartupGate | None = None,
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
    if set(speech_dynamics or {}) - set(ensemble.packs):
        raise ValueError("speech dynamics refer to an unknown performer")
    # Opt-in audio-driven wording texture. Disabled unless every bound
    # performer was explicitly authorized by the operator config.
    speech_driven = frozenset(speech_driven_performers or ())
    speech_seeds = dict(speech_seeds or {})
    if not speech_driven <= set(ensemble.packs):
        raise ValueError("speech-driven performers must be configured performers")
    if set(speech_fallbacks or {}) - speech_driven:
        raise ValueError("speech fallbacks require speech-driven performers")

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

    live_presences = dict(live_presences or {})
    if not set(live_presences) <= set(ensemble.packs):
        raise ValueError("live presences must be configured performers")
    for performer, presence in sorted(live_presences.items()):
        ensemble.bind_live_presence(performer, presence)
        if performer in speech_driven:
            # The live generator reads the same unitless speech window directly.
            presence.speech = _WindowChannel(performer)
    for performer in sorted(speech_driven - set(live_presences)):
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
                fallback=(speech_fallbacks or {}).get(performer),
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
                if startup_gate is not None:
                    # Read-only: compares each robot's pose with its sleep pose
                    # and holds any that are not there still until the user decides.
                    await startup_gate.check()
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
    async def ensemble_capabilities() -> dict[str, object]:
        """Read the configured performers' actual semantic capabilities.

        Discovery is read-only: it never acquires room ownership or starts
        startup/idle behavior. A fresh caller session must explicitly send an
        authorized idle presence event to enter the configured startup path.
        """
        snapshot = ensemble.snapshot()
        # The config flag says the operator enabled this software path. It is
        # not evidence that the connected SDK/session is currently authorized,
        # ready, calibrated, leased, or safe to move.
        performers = {
            performer_id: {**details, "live_readiness": "unknown"}
            for performer_id, details in snapshot["performers"].items()
        }
        return {
            "outcome": "observed", **snapshot,
            "performers": performers,
            "motion_configured_enabled": bool(motion_enabled),
            "live_motion_authority": "unknown",
            "session_ready_event": "ensemble_presence(state='idle')",
        }

    @server.tool()
    async def ensemble_startup_check() -> dict[str, object]:
        """Read-only: is each robot at its sleep position? Reports per-joint deltas.

        A robot that is not at sleep stays still until the user decides; relay each
        performer's ``message`` to the user and act on their answer with
        ``ensemble_prepare``. Never moves a robot.
        """
        if startup_gate is None:
            return {"outcome": "unsupported", "reason": "startup_gate_not_configured"}
        try:
            return await startup_gate.check()
        except Exception:  # noqa: BLE001
            raise ToolError("startup_check_unavailable") from None

    @server.tool()
    async def ensemble_prepare(
        performer_id: str, action: str, action_id: str = "",
    ) -> dict[str, object]:
        """The user's answer to a startup check.

        ``move_to_sleep``: one sleep move then one re-check (needs ``action_id``).
        ``confirm_at_sleep``: the user attests a commanded-only robot is parked.
        """
        if startup_gate is None:
            return {"outcome": "unsupported", "reason": "startup_gate_not_configured"}
        try:
            if action == "confirm_at_sleep":
                return await startup_gate.confirm_at_sleep(performer_id)
            if action == "move_to_sleep":
                if not motion_enabled:
                    raise ToolError("ensemble_motion_not_operator_authorized")
                if not action_id:
                    return {"outcome": "denied", "reason": "action_id_required"}
                return await startup_gate.move_to_sleep(performer_id, action_id)
        except ToolError:
            raise
        except Exception:  # noqa: BLE001
            raise ToolError("startup_prepare_unavailable") from None
        return {"outcome": "denied", "reason": "unknown_action"}

    @server.tool()
    async def ensemble_recover(performer_id: str) -> dict[str, object]:
        """Lift a performer's quarantine after fresh evidence; never retries a motion.

        Evidence is a measured pose that is settled near sleep/neutral, or a
        confirmed STOP. Refused while a conversation is active. Afterwards the
        sleep check runs again.
        """
        if startup_gate is None:
            return {"outcome": "unsupported", "reason": "startup_gate_not_configured"}
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        try:
            return await startup_gate.recover(performer_id)
        except Exception:  # noqa: BLE001
            raise ToolError("recover_unavailable") from None

    @server.tool()
    async def ensemble_park(action_id: str, settle_session: bool = False) -> dict[str, object]:
        """End of session: move every robot to sleep and report the re-check.

        Refused while a conversation session is active, unless ``settle_session`` is
        true: the caller that owns that conversation is ending it, so running
        behaviors finish (no STOP), the robots are released and then moved to sleep;
        the caller should send the disconnect afterwards. Stops at the first fault
        and reports it; never retries.
        """
        if startup_gate is None:
            return {"outcome": "unsupported", "reason": "startup_gate_not_configured"}
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        if not action_id:
            return {"outcome": "denied", "reason": "action_id_required"}
        try:
            return await startup_gate.park(action_id, settle_session=settle_session)
        except Exception:  # noqa: BLE001
            raise ToolError("park_unavailable") from None

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
    async def ensemble_intent(
        intent: str, action_id: str,
        performer_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Perform one authored semantic intent supported by each selected robot."""
        if not motion_enabled:
            raise ToolError("ensemble_motion_not_operator_authorized")
        try:
            return await ensemble.express(
                intent, action_id=action_id,
                performer_ids=tuple(performer_ids) if performer_ids is not None else None,
            )
        except Exception:  # noqa: BLE001 - provider failures map to safe MCP error
            raise ToolError("invalid_or_unavailable_ensemble_intent") from None


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
