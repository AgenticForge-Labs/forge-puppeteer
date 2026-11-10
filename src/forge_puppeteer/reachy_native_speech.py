"""Pinned Reachy speech oscillator, without SDK access or motor authority.

The input is the native tapper's scalar gain from outgoing synthesized audio.
Scheduling and motion remain the responsibility of the existing runtime.
"""

from __future__ import annotations

import math
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .robot_contracts import ContractModel


@dataclass(frozen=True, slots=True)
class ReachySpeechOffsets:
    x_m: float
    y_m: float
    z_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float


class ReachyNativeSpeechShaper:
    """Replicate the pinned SDK's seeded six-axis oscillator from native gain."""

    def __init__(self, *, seed: int = 7) -> None:
        import numpy as np

        rng = np.random.default_rng(seed)
        self._phases = tuple(float(rng.random() * math.tau) for _ in range(6))
        self._next_sequence = 0
        self._time_s = 0.0

    def reset(self) -> None:
        """Reset time while retaining the original seeded oscillator phases."""
        self._next_sequence = 0
        self._time_s = 0.0

    def skip_to(self, sequence: int, *, max_gap: int = 1200) -> None:
        """Advance phase over discarded hops without generating old targets."""
        if (type(sequence) is not int or sequence < self._next_sequence
                or sequence - self._next_sequence > max_gap):
            raise ValueError("native speech gap is invalid or too large")
        for _ in range(sequence - self._next_sequence):
            self._time_s += 0.05
        self._next_sequence = sequence

    def render(self, sequence: int, gain: float) -> ReachySpeechOffsets:
        if type(sequence) is not int or sequence != self._next_sequence:
            raise ValueError("native speech sequence must be contiguous")
        if (isinstance(gain, bool) or not isinstance(gain, (int, float))
                or not math.isfinite(gain) or not 0 <= gain <= 1.5):
            raise ValueError("native speech gain must be finite and within 0..1.5")
        self._next_sequence += 1
        # The pinned SDK accumulates hop time rather than multiplying the
        # sequence, which also preserves its floating-point phase over long runs.
        self._time_s += 0.05
        t = self._time_s
        pitch, yaw, roll, x, y, z = self._phases
        return ReachySpeechOffsets(
            x_m=0.0045 * gain * math.sin(math.tau * 0.35 * t + x),
            y_m=0.00375 * gain * math.sin(math.tau * 0.45 * t + y),
            z_m=0.00225 * gain * math.sin(math.tau * 0.25 * t + z),
            roll_rad=math.radians(2.25) * gain * math.sin(math.tau * 1.3 * t + roll),
            pitch_rad=math.radians(4.5) * gain * math.sin(math.tau * 2.2 * t + pitch),
            yaw_rad=math.radians(7.5) * gain * math.sin(math.tau * 0.6 * t + yaw),
        )


def compose_original_speech_offset(base_pose: object, offsets: ReachySpeechOffsets) -> object:
    """Apply the daemon's world-frame speech offset without sending a command.

    Only the six bounded offsets produced by the native oscillator are
    accepted. The SDK's pose validation and the runtime's resource ownership
    must still be applied before any result reaches a device.
    """
    import numpy as np
    from reachy_mini.utils import create_head_pose
    from reachy_mini.utils.interpolation import compose_world_offset

    if not isinstance(offsets, ReachySpeechOffsets):
        raise TypeError("validated native speech offsets required")
    values = (offsets.x_m, offsets.y_m, offsets.z_m,
              offsets.roll_rad, offsets.pitch_rad, offsets.yaw_rad)
    limits = (0.0045 * 1.5, 0.00375 * 1.5, 0.00225 * 1.5,
              math.radians(2.25) * 1.5, math.radians(4.5) * 1.5,
              math.radians(7.5) * 1.5)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or abs(value) > limit + 1e-12
           for value, limit in zip(values, limits, strict=True)):
        raise ValueError("native speech offset exceeds the original oscillator bounds")
    base = np.asarray(base_pose, dtype=np.float64)
    if base.shape != (4, 4) or not np.isfinite(base).all():
        raise ValueError("finite 4x4 base pose required")
    offset_pose = create_head_pose(
        x=offsets.x_m, y=offsets.y_m, z=offsets.z_m,
        roll=offsets.roll_rad, pitch=offsets.pitch_rad, yaw=offsets.yaw_rad,
        degrees=False,
    )
    return compose_world_offset(base, offset_pose)


class NativeSpeechGain(ContractModel):
    """Strict content-free generated-PCM observation, never a motion request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    schema: Literal["forge.native_speech_gain.v1"]
    stream_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    offset_ms: int = Field(ge=0)
    duration_ms: Literal[50]
    gain: float = Field(ge=0, le=1.5)
    timing_source: Literal["generated_pcm"]
    acoustic_playout_measured: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def exact_literals(cls, value):
        # Pydantic's Literal checks use equality even under strict mode, so
        # 0 and 50.0 otherwise silently become False and 50 respectively.
        if isinstance(value, dict) and (
            value.get("acoustic_playout_measured") is not False
            or type(value.get("duration_ms")) is not int
        ):
            raise ValueError("speech timing literals must have exact types")
        return value

    @model_validator(mode="after")
    def aligned(self):
        if self.offset_ms != self.sequence * 50:
            raise ValueError("speech gain offset must match sequence")
        return self


class NativeSpeechGainWindow:
    """Atomic, ordered observations; no hardware connection or dispatch.

    Old streams remain fenced after replacement or invalidation. After the
    bounded stream budget is exhausted the window fails closed rather than
    forgetting identities and allowing a previously cancelled stream back.
    Receipt time is not speaker playout time and this window is not a player.
    """

    def __init__(self, *, max_streams: int = 4096, stale_after_ns: int = 500_000_000,
                 allow_gaps: bool = False):
        if type(max_streams) is not int or max_streams < 1:
            raise ValueError("positive stream budget required")
        if type(stale_after_ns) is not int or stale_after_ns < 1:
            raise ValueError("positive freshness bound required")
        if type(allow_gaps) is not bool:
            raise ValueError("gap policy must be boolean")
        self._allow_gaps = allow_gaps
        self._max_streams = max_streams
        self._stale_after_ns = stale_after_ns
        self._seen: set[str] = set()
        self._active: str | None = None
        self._sequence = 0
        self._shaper: ReachyNativeSpeechShaper | None = None
        self._latest: ReachySpeechOffsets | None = None
        self._received_at_ns: int | None = None

    def invalidate(self) -> None:
        """Fence the current stream after a presence transition or interruption."""
        self._active = None
        self._shaper = None
        self._latest = None
        self._received_at_ns = None

    def accept(self, frames: list[dict[str, object]]) -> dict[str, object]:
        if not isinstance(frames, list) or not 1 <= len(frames) <= 8:
            raise ValueError("speech gain batch must contain one to eight frames")
        parsed = [NativeSpeechGain.model_validate(frame) for frame in frames]
        stream = parsed[0].stream_id
        if any(hop.stream_id != stream or hop.sequence != parsed[0].sequence + i
               for i, hop in enumerate(parsed)):
            raise ValueError("speech gain batch must be contiguous and single-stream")
        if stream != self._active:
            if stream in self._seen or (parsed[0].sequence != 0 and not self._allow_gaps):
                raise ValueError("old or noninitial speech stream rejected")
            if len(self._seen) >= self._max_streams:
                raise ValueError("speech stream budget exhausted")
            shaper = ReachyNativeSpeechShaper()
            if self._allow_gaps:
                shaper.skip_to(parsed[0].sequence)
        else:
            if (self._shaper is None or parsed[0].sequence < self._sequence
                    or (parsed[0].sequence != self._sequence and not self._allow_gaps)):
                raise ValueError("out-of-order speech gain rejected")
            shaper = deepcopy(self._shaper)
            if self._allow_gaps:
                shaper.skip_to(parsed[0].sequence)
        latest = None
        for hop in parsed:
            latest = shaper.render(hop.sequence, hop.gain)
        self._seen.add(stream)
        self._active = stream
        self._sequence = parsed[-1].sequence + 1
        self._shaper = shaper
        self._latest = latest
        self._received_at_ns = time.monotonic_ns()
        return {"outcome": "observed", "frames_accepted": len(parsed),
                "stream_id": stream, "last_sequence": parsed[-1].sequence,
                "timing_source": "generated_pcm", "physical_motion_dispatched": False}

    def latest(self) -> ReachySpeechOffsets | None:
        if self._received_at_ns is None or time.monotonic_ns() - self._received_at_ns > self._stale_after_ns:
            return None
        return self._latest

    def snapshot(self) -> dict[str, object]:
        """Expose only content-free observation status, never motor targets."""
        if self._active is None or self._received_at_ns is None:
            return {"available": False, "physical_motion_dispatched": False}
        return {"available": True, "stream_id": self._active,
                "last_sequence": self._sequence - 1,
                "stale": time.monotonic_ns() - self._received_at_ns > self._stale_after_ns,
                "timing_source": "generated_pcm", "physical_motion_dispatched": False}
