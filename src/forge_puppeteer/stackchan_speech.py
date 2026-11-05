"""StackChan-specific render preview for generic outgoing speech expression.

This is *authoring*, not a USB command loop or motion authority. A voice
worker sends only content-free normalized envelopes. Puppeteer's existing
ExecutionRuntime and SDK remain the only approved dispatch path. Nothing
here calls an SDK, opens hardware or schedules a task.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .behavior_contract import BehaviorCue
from .speech_expression import ExpressionChannels


@dataclass(frozen=True, slots=True)
class StackChanSpeechFaceProfile:
    """Bounded presentation-only limits in native StackChan feature units."""

    max_mouth_weight: int = 75
    min_interval_ms: int = 200

    def __post_init__(self) -> None:
        if type(self.max_mouth_weight) is not int or not 0 <= self.max_mouth_weight <= 100:
            raise ValueError("mouth weight must be an integer in 0..100")
        if type(self.min_interval_ms) is not int or not 100 <= self.min_interval_ms <= 1000:
            raise ValueError("face output cadence must be between 100 and 1000 ms")


class StackChanSpeechFacePreview:
    """Reduce 50-ms speech hops to bounded, native-format mouth *candidates*.

    Every update is standalone and validated by the existing native SDK
    operation. A change from speaking to inactivity emits one closing-mouth
    cue even if its scheduled interval has not elapsed. Future dispatch
    must be operator granted through ExecutionRuntime, never from here.
    """

    def __init__(self, *, stream_id: str, profile: StackChanSpeechFaceProfile | None = None):
        if not isinstance(stream_id, str) or not 1 <= len(stream_id) <= 128:
            raise ValueError("stream_id must be a bounded identifier")
        self.stream_id = stream_id
        self.profile = profile or StackChanSpeechFaceProfile()
        self._next_sequence = 0
        self._last_emitted_ms: int | None = None
        self._was_active = False

    def render(self, channels: ExpressionChannels) -> BehaviorCue | None:
        if (
            channels.stream_id != self.stream_id
            or channels.sequence != self._next_sequence
            or channels.offset_ms != self._next_sequence * 50
        ):
            raise ValueError("speech candidate stream is out of order or inconsistent")
        if not math.isfinite(channels.mouth) or not 0 <= channels.mouth <= 1:
            raise ValueError("mouth channel must be finite and normalized")
        if type(channels.active) is not bool:
            raise ValueError("speech activity must be boolean")
        self._next_sequence += 1

        # Prioritize a clearly inactive endpoint so the next speaker can
        # start without inheriting an open mouth. Physical session teardown
        # still relies on the runtime's STOP and authored neutral behaviors.
        release = self._was_active and not channels.active
        self._was_active = channels.active
        if (
            not release and self._last_emitted_ms is not None
            and channels.offset_ms - self._last_emitted_ms < self.profile.min_interval_ms
        ):
            return None

        weight = (
            round(self.profile.max_mouth_weight * channels.mouth)
            if channels.active else 0
        )
        self._last_emitted_ms = channels.offset_ms
        return BehaviorCue(
            at_s=0.0,
            operation="face",
            parameters={"mouth": {"weight": weight}},
        )



def stackchan_native_mouth_cue(
    values: Mapping[str, object], _amplitude: float,
) -> BehaviorCue:
    """One SDK-valid latest-hop mouth target; no second modifier scheduler.

    Used only for operator-enabled smooth_v2 speaking. The native SDK owns
    firmware capability checks and completion, not this pure formatter.
    """
    level = values.get("mouth")
    if isinstance(level, bool) or not isinstance(level, (float, int)):
        raise TypeError("StackChan mouth level must be numeric")
    if not math.isfinite(level) or not 0 <= level <= 1:
        raise ValueError("StackChan mouth level must be normalized")
    active = values.get("active")
    if type(active) is not bool:
        raise ValueError("StackChan speech activity required")
    weight = round(75 * level) if active else 0
    return BehaviorCue(
        at_s=0.0, operation="face", parameters={"mouth": {"weight": weight}},
    )
