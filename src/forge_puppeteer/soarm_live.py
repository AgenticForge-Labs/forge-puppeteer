"""Live presence motion for the SO-ARM101: breathing, listening hold, audio-driven talking.

The SDK's trusted-host live stream accepts small joint *offsets* from an
operator-pinned centre pose and does the smoothing and clamping itself. This
module only decides the offsets, so there is no motion seam between breathing,
listening and talking and the talking follows the voice.

It has no I/O, clock dependency beyond the injected one, and no robot access.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

#: Canonical SDK joint order: pan, lift, elbow, wrist flex, wrist roll.
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
PRESENCE = ("idle", "listening", "speaking")
BLEND_S = 0.6
#: Speech-level response: attack/release time constants and gain.
LEVEL_ATTACK_S = 0.06
LEVEL_RELEASE_S = 0.25
LEVEL_GAIN = 2.0
NOD_HZ = 1.1
TILT_HZ = 0.6
TILT_SCALE = 0.6
#: Without fresh speech features the talking keeps a gentle rhythm.
FALLBACK_LEVEL = 0.5


@dataclass(frozen=True)
class LivePresenceSettings:
    envelope: str
    envelope_revision: str
    breath_delta_rad: tuple[float, ...]
    max_offset_rad: tuple[float, ...]
    breath_period_s: float = 4.0
    talk_amplitude_rad: float = math.radians(5.0)

    def __post_init__(self) -> None:
        if len(self.breath_delta_rad) != 5 or len(self.max_offset_rad) != 5:
            raise ValueError("live presence needs five joints")
        if not 1.0 <= self.breath_period_s <= 8.0:
            raise ValueError("breathing period must be within 1..8 seconds")
        if not 0.0 < self.talk_amplitude_rad <= math.radians(10.0):
            raise ValueError("talking amplitude must be within 0..10 degrees")
        for i, limit in enumerate(self.max_offset_rad):
            used = abs(self.breath_delta_rad[i])
            if i == 3:
                used = max(used, self.talk_amplitude_rad)
            if i == 4:
                used = max(used, self.talk_amplitude_rad * TILT_SCALE)
            if used > limit + 1e-9:
                raise ValueError(f"{JOINTS[i]} motion exceeds the pinned envelope range")


def _ease(weight: float) -> float:
    return weight * weight * (3.0 - 2.0 * weight)


class LivePresence:
    """Current presence state plus the offset generator the SO-ARM stream follows.

    ``set_behavior`` may be called from the ensemble at any time; ``offsets`` is
    called by the stream loop (about 20 times a second).
    """

    def __init__(self, settings: LivePresenceSettings, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.asset_id = f"soarm101/envelope/{settings.envelope}"
        self.asset_revision = settings.envelope_revision
        #: Optional object with ``latest() -> dict | None`` (the speech envelope window).
        self.speech = None
        self._clock = clock
        self._lock = threading.Lock()
        self._behavior = "idle"
        self._closing = threading.Event()
        self.reset()

    # -- state (ensemble side) -------------------------------------------------
    @property
    def behavior(self) -> str:
        with self._lock:
            return self._behavior

    def set_behavior(self, behavior: str) -> None:
        if behavior not in PRESENCE:
            raise ValueError("unknown presence behavior")
        with self._lock:
            self._behavior = behavior

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    def close(self) -> None:
        """Ask the stream loop to return to the centre pose and end."""
        self._closing.set()

    def reset(self) -> None:
        """Fresh stream: breathing starts at the bottom of its swell."""
        self._closing.clear()
        self._t0 = self._clock()
        self._last = self._t0
        self._weights = {name: 1.0 if name == "idle" else 0.0 for name in PRESENCE}
        self._level = 0.0
        self._source = "idle"

    @property
    def source(self) -> str:
        """What drove the last talking sample: ``speech``, ``fallback`` or the state name."""
        return self._source

    # -- generator (stream side) -------------------------------------------------
    def offsets(self) -> list[float]:
        """Joint offsets (rad) from the pinned centre for the current moment."""
        now = self._clock()
        dt = max(0.0, min(0.5, now - self._last))
        self._last = now
        behavior = self.behavior
        step = dt / BLEND_S
        for name in PRESENCE:
            goal = 1.0 if name == behavior else 0.0
            current = self._weights[name]
            self._weights[name] = min(goal, current + step) if goal > current else max(goal, current - step)
        settings = self.settings
        t = now - self._t0
        out = [0.0] * 5

        idle_w = _ease(self._weights["idle"])
        if idle_w > 0.0:
            swell = (1.0 - math.cos(math.tau * t / settings.breath_period_s)) / 2.0
            for i, delta in enumerate(settings.breath_delta_rad):
                out[i] += idle_w * delta * swell

        speak_w = _ease(self._weights["speaking"])
        target = self._speech_level() if (behavior == "speaking" or speak_w > 0.0) else 0.0
        rate = LEVEL_ATTACK_S if target > self._level else LEVEL_RELEASE_S
        self._level += (target - self._level) * (1.0 - math.exp(-dt / rate)) if dt else 0.0
        if speak_w > 0.0:
            amplitude = settings.talk_amplitude_rad * speak_w * self._level
            out[3] += amplitude * math.sin(math.tau * NOD_HZ * t)
            out[4] += amplitude * TILT_SCALE * math.sin(math.tau * TILT_HZ * t + 1.0)
        self._source = behavior if behavior != "speaking" else self._source

        return [max(-limit, min(limit, value)) for value, limit in
                zip(out, settings.max_offset_rad, strict=True)]

    def _speech_level(self) -> float:
        """0..1 talking strength: follows the speech level when features are fresh."""
        channel = self.speech
        values = channel.latest() if channel is not None else None
        if values is None:
            self._source = "fallback"
            return FALLBACK_LEVEL
        self._source = "speech"
        if values.get("active") is False:
            return 0.0
        mouth = values.get("mouth", 0.0)
        if isinstance(mouth, bool) or not isinstance(mouth, (int, float)) or not math.isfinite(mouth):
            return 0.0
        return max(0.0, min(1.0, float(mouth) * LEVEL_GAIN))
