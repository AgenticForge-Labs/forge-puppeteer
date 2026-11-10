"""Per-robot behavior options for schema-v4 ensembles (Reachy and StackChan).

A robot file may carry an ``options`` object next to its ``provider``. Only the
options in current use are supported; the older experimental profiles
(``native_v1``, idle ``smooth_v2``, original speech, display presets) were dropped
in favor of the behavior redesign (#53).

Options choose *how* the standard behaviors are authored and which opt-in motion
variants run. They never grant device access: that is the ensemble's ``physical``
flag plus the provider's tool grants.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from ..behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors


class ReachyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expression_style: Literal["classic", "expressive"] = "classic"
    startup_behavior: Literal["none", "wake_up_if_sleeping"] = "none"
    listening_mode: Literal["tilt", "hold"] = "tilt"
    listening_tilt_rad: float = Field(default=0.02, ge=0, le=0.07)
    speaking_axis: Literal["yaw", "tilt"] = "yaw"
    speaking_amplitude_rad: float = Field(default=0.025, ge=0, le=0.07)
    expressive_amplitude_rad: float = Field(default=0.09, ge=0, le=0.12)
    antenna_sway_rad: float = Field(default=0.18, ge=0, le=0.26)
    breath_amplitude_m: float = Field(default=0.0, ge=0, le=0.005)
    neutral_antennas_rad: tuple[float, float] | None = None
    # "original_v1": the Conversation App's own breathing idle as one bounded SDK action.
    idle: Literal["default", "original_v1"] = "default"
    # "smooth_v2": speech-driven expressive head motion that follows the audio envelope.
    speaking: Literal["default", "smooth_v2"] = "default"
    speech_driven: StrictBool = False
    # Rhythmic head-only speaking pattern used when generated features go stale.
    legacy_speech_fallback: StrictBool = False

    @model_validator(mode="after")
    def coherent(self):
        if self.speaking == "smooth_v2" and not self.speech_driven:
            raise ValueError("speaking: smooth_v2 requires speech_driven: true")
        if self.legacy_speech_fallback and (
            self.speaking != "smooth_v2" or not self.speech_driven
            or self.expression_style != "classic"
        ):
            raise ValueError(
                "legacy_speech_fallback requires speaking: smooth_v2, speech_driven: true "
                "and expression_style: classic")
        if self.neutral_antennas_rad is not None and any(
                not math.isfinite(v) for v in self.neutral_antennas_rad):
            raise ValueError("neutral_antennas_rad must be finite")
        return self


class StackChanOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    speaking_mode: Literal["legacy_size", "native_weight"] = "legacy_size"
    speech_driven: StrictBool = False


def parse_options(kind: str, data: object):
    """Validate a robot file's ``options`` object; SO-ARM101 has none."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"v4 {kind} options must be an object")  # noqa: TRY004 - config errors are ValueErrors
    if kind == "reachy":
        return ReachyOptions.model_validate(data)
    if kind == "stackchan":
        return StackChanOptions.model_validate(data)
    if data:
        raise ValueError(f"v4 {kind} takes no options")
    return None


def check_reachy_options(options: ReachyOptions, tools, restricted: bool, neutral_head_file: str) -> None:
    """Cross-check options against the provider's tool grants and the neutral pose."""
    tools = set(tools)
    if restricted:
        raise ValueError("Reachy head expression requires the normal native SDK mode")
    if options.startup_behavior == "wake_up_if_sleeping" and "wake_up" not in tools:
        raise ValueError("startup_behavior: wake_up_if_sleeping needs the wake_up tool grant")
    if options.expression_style == "expressive" or options.speaking == "smooth_v2":
        if "pose_and_antennas" not in tools:
            raise ValueError("expressive Reachy motion needs the pose_and_antennas tool grant")
        if options.neutral_antennas_rad is None:
            raise ValueError("expressive Reachy motion needs operator-reviewed neutral_antennas_rad")
        from ..devices.reachy import _ANTENNA_ANGLE_LIMIT_RAD
        if any(abs(v) + options.antenna_sway_rad > _ANTENNA_ANGLE_LIMIT_RAD
               for v in options.neutral_antennas_rad):
            raise ValueError("expressive Reachy antenna motion is not within adapter bounds")
    if options.idle == "original_v1":
        if "original_idle" not in tools:
            raise ValueError("idle: original_v1 needs the original_idle tool grant")
        matrix = json.loads(Path(neutral_head_file).read_text(encoding="utf-8"))
        if not isinstance(matrix, list) or len(matrix) != 4 or any(
            not isinstance(row, list) or len(row) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   or abs(v - (1.0 if i == j else 0.0)) > 1e-9 for j, v in enumerate(row))
            for i, row in enumerate(matrix)
        ):
            raise ValueError("idle: original_v1 requires the identity neutral head reference")


@dataclass(frozen=True)
class ReachyBehavior:
    pack: PerformerBehaviors
    #: ``{"idle": pack, "speaking": pack}`` replacements for ConversationEnsemble.
    motion_patterns: dict[str, PerformerBehaviors] = field(default_factory=dict)
    speech_dynamics: str | None = None
    speech_fallback: BehaviorPattern | None = None


def _original_idle_pack(pack: PerformerBehaviors) -> PerformerBehaviors:
    """One bounded SDK action for the whole ensemble-owned idle interval."""
    idle = BehaviorPattern(
        source="procedural", mode="loop", period_s=120, max_duration_s=3600,
        cues=(BehaviorCue(at_s=0, operation="original_idle", parameters={"duration_s": 3600}),),
    )
    return PerformerBehaviors.author(pack.performer_id, {**pack.behaviors, "idle": idle, "speaking": idle},
                                     startup=pack.startup)


async def build_reachy_behavior(
    performer_id: str, catalog, options: ReachyOptions, neutral_head,
) -> ReachyBehavior:
    from ..conversation_native import reachy_pack_from_catalog

    async def resolve(style: str):
        return await reachy_pack_from_catalog(
            performer_id, catalog, neutral_head=neutral_head,
            wobble_amplitude_rad=(options.expressive_amplitude_rad if style == "expressive"
                                  else options.speaking_amplitude_rad),
            speaking_axis=options.speaking_axis,
            listening_tilt_rad=options.listening_tilt_rad,
            listening_mode=options.listening_mode,
            expression_style=style,
            breath_amplitude_m=options.breath_amplitude_m,
            neutral_antennas_rad=options.neutral_antennas_rad,
            antenna_sway_rad=options.antenna_sway_rad,
            wake_up_if_sleeping=options.startup_behavior == "wake_up_if_sleeping",
        )

    pack = await resolve(options.expression_style)
    patterns: dict[str, PerformerBehaviors] = {}
    if options.idle == "original_v1":
        patterns["idle"] = _original_idle_pack(pack)
    dynamics = fallback = None
    if options.speaking == "smooth_v2":
        patterns["speaking"] = pack if options.expression_style == "expressive" else await resolve("expressive")
        dynamics = "smoothed_v1"
        if options.legacy_speech_fallback:
            fallback = pack.behaviors["speaking"]
    return ReachyBehavior(pack, patterns, dynamics, fallback)


def build_stackchan_pack(performer_id: str, options: StackChanOptions) -> PerformerBehaviors:
    from ..conversation_native import stackchan_conversation_pack
    return stackchan_conversation_pack(performer_id, speaking_mode=options.speaking_mode)
