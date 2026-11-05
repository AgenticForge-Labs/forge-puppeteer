"""Portable speech-to-expression shaper, deliberately not a robot controller.

Pollen's pinned Reachy speech tapper inspired the oscillator frequencies.
This module receives content-free 50 ms generated-PCM envelope features,
produces dimensionless expression channels, and never opens a device or
submits a motion. Device adapters and ExecutionRuntime retain authority.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from .robot_contracts import ContractModel

SCHEMA = "forge.speech_envelope.v1"
# An envelope older than this must not drive motion.
_STALE_NS = 500_000_000
HOP_MS = 50

# Frequencies are an expressive texture, not joint-space or actuator rates.
FREQUENCIES_HZ = {
    "pitch": 2.2,
    "yaw": 0.6,
    "roll": 1.3,
    "x": 0.35,
    "y": 0.45,
    "z": 0.25,
    "antenna": 0.75,
}
# These are unitless presentation weights, NOT physical motion envelopes.
CHANNEL_WEIGHTS = {
    "pitch": 0.6,
    "yaw": 1.0,
    "roll": 0.3,
    "x": 0.6,
    "y": 0.5,
    "z": 0.3,
    "antenna": 0.5,
}


class SpeechEnvelopeHop(ContractModel):
    """Strict, content-free schema shared with Conversationalist's producer."""

    schema: Literal["forge.speech_envelope.v1"] = SCHEMA
    stream_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    offset_ms: int = Field(ge=0)
    duration_ms: Literal[50]
    active: bool
    intensity: float = Field(ge=0, le=1, allow_inf_nan=False)
    rms_dbfs: float = Field(ge=-120, le=0, allow_inf_nan=False)
    timing_source: Literal["generated_pcm"]
    acoustic_playout_measured: Literal[False]

    @model_validator(mode="after")
    def aligned(self):
        if self.offset_ms != self.sequence * HOP_MS:
            raise ValueError("envelope time does not match monotonically indexed hops")
        return self


@dataclass(frozen=True, slots=True)
class ExpressionChannels:
    """Unitless semantic channels; consumers must NOT interpret as actuator units."""

    stream_id: str
    sequence: int
    offset_ms: int
    active: bool
    mouth: float
    pitch: float
    yaw: float
    roll: float
    x: float
    y: float
    z: float
    antenna: float


class SpeechExpressionShaper:
    """Deterministic Pollen-style 6D gesture texture from one speech stream.

    No physical axes, robot calibration, grants, persistent motor scheduler,
    actor persona, or STOP authority live here. Per-device authored maps must
    clamp and validate the whole motion before calling an SDK. Reset with a
    new instance for a new utterance or after an interruption.
    """

    def __init__(
        self, *, performer_id: str, stream_id: str,
        dynamics: Literal["legacy", "smoothed_v1"] = "legacy",
    ) -> None:
        if not performer_id or len(performer_id) > 128:
            raise ValueError("performer_id must be a bounded nonempty string")
        if not stream_id or len(stream_id) > 128:
            raise ValueError("stream_id must be a bounded nonempty string")
        if dynamics not in {"legacy", "smoothed_v1"}:
            raise ValueError("unsupported speech dynamics")
        self.performer_id = performer_id
        self.stream_id = stream_id
        self.dynamics = dynamics
        self._smoothed_level = 0.0
        self._next_sequence = 0
        # Stable per-performer phases prevent three robots from moving in
        # mechanically identical lockstep while preserving shared speech timing.
        phases: dict[str, float] = {}
        for name in FREQUENCIES_HZ:
            digest = hashlib.sha256(
                f"{performer_id}:{stream_id}:{name}".encode()
            ).digest()
            phases[name] = math.tau * int.from_bytes(digest[:8], "big") / (2**64)
        self._phases = phases

    def render(self, item: SpeechEnvelopeHop) -> ExpressionChannels:
        """Reject duplicates, gaps and cross-stream events; never silently rearm."""
        if item.stream_id != self.stream_id:
            raise ValueError("speech stream identity changed")
        if item.sequence != self._next_sequence:
            raise ValueError("out-of-order or missing audio envelope hop")
        self._next_sequence += 1
        t = item.offset_ms / 1000.0
        # A silent/inactive hop can retain a nonzero smoothed intensity from
        # the producer. It must not produce motion solely from that residual.
        # Keep the existing active-hop oscillator and phase behavior unchanged.
        level = item.intensity if item.active else 0.0
        if self.dynamics == "smoothed_v1":
            # One 50 ms hop per sample. First-order envelope following avoids
            # hard jumps in speaking intensity; no delayed motion is emitted
            # after VAD goes inactive, including on interrupted streams.
            if not item.active:
                self._smoothed_level = 0.0
            else:
                tau_s = 0.05 if level > self._smoothed_level else 0.25
                alpha = 1.0 - math.exp(-0.05 / tau_s)
                self._smoothed_level += alpha * (level - self._smoothed_level)
            level = self._smoothed_level
        channels = {
            name: max(-1.0, min(1.0, level * CHANNEL_WEIGHTS[name] * math.sin(
                math.tau * frequency * t + self._phases[name]
            )))
            for name, frequency in FREQUENCIES_HZ.items()
        }
        return ExpressionChannels(
            stream_id=item.stream_id,
            sequence=item.sequence,
            offset_ms=item.offset_ms,
            active=item.active,
            mouth=level if item.active else 0.0,
            pitch=channels["pitch"],
            yaw=channels["yaw"],
            roll=channels["roll"],
            x=channels["x"],
            y=channels["y"],
            z=channels["z"],
            antenna=channels["antenna"],
        )


class SpeechEnvelopeWindow:
    """Content-free, bounded, process-local preview for a room-owned MCP child.

    This accepts *observations*, not action requests. It has no robot link,
    command dispatcher, timer thread or STOP/rearm authority. A caller may
    read its unitless preview, but it cannot actuate a motor.
    """

    def __init__(
        self, *,
        performer_dynamics: Mapping[str, Literal["legacy", "smoothed_v1"]] | None = None,
    ) -> None:
        configured = dict(performer_dynamics or {})
        if any(
            not name or len(name) > 128 or mode not in {"legacy", "smoothed_v1"}
            for name, mode in configured.items()
        ):
            raise ValueError("invalid per-performer speech dynamics")
        self._performer_dynamics = configured
        self.clear()

    def clear(self) -> None:
        self._stream_id: str | None = None
        self._next_sequence = 0
        # Each bound performer keeps its own shaper and sequence: two robots
        # share one window but receive independently attributed streams, so a
        # batch for one must not reset or invalidate the other's state.
        self._shapers: dict[str, SpeechExpressionShaper] = {}
        self._performer_sequences: dict[str, tuple[str, int]] = {}
        self._latest: dict[str, ExpressionChannels] = {}
        self._latest_hop: SpeechEnvelopeHop | None = None
        self._received_at_ns: int | None = None

    def accept(
        self, frames: list[dict[str, object]], *, performer_ids: tuple[str, ...],
    ) -> dict[str, object]:
        """Validate a whole batch before updating the observed stream state."""
        if not isinstance(frames, list) or not 1 <= len(frames) <= 8:
            raise ValueError("speech batch must contain one to eight hops")
        if not performer_ids or len(set(performer_ids)) != len(performer_ids):
            raise ValueError("unique bound performer IDs required")
        parsed = [SpeechEnvelopeHop.model_validate(frame) for frame in frames]
        stream_id = parsed[0].stream_id
        # Validate the batch's own continuity first: a gap or mixed stream is
        # invalid regardless of which performer it is attributed to.
        for index, hop in enumerate(parsed):
            if hop.stream_id != stream_id or hop.sequence != parsed[0].sequence + index:
                raise ValueError("speech batch contains a gap or mixed stream")
        # Continuity is tracked per performer. The window is room-wide while
        # each performer receives its own attributed offers, so one performer's
        # sequence must never gate another's.
        previous_stream, previous_next = self._performer_sequences.get(
            performer_ids[0], (stream_id, parsed[0].sequence)
        )
        new_stream = stream_id != previous_stream
        expected = parsed[0].sequence if new_stream else previous_next
        for hop in parsed:
            if hop.sequence != expected:
                raise ValueError("speech batch contains a gap or mixed stream")
            expected += 1
        # The source may switch at an explicit stream boundary, but cannot
        # implicitly reset the sequence of an existing stream.
        shapers = {}
        for name in performer_ids:
            bound_stream, _ = self._performer_sequences.get(name, (stream_id, 0))
            existing = self._shapers.get(name)
            if existing is None or bound_stream != stream_id:
                shapers[name] = SpeechExpressionShaper(
                    performer_id=name, stream_id=stream_id,
                    dynamics=self._performer_dynamics.get(name, "legacy"),
                )
            else:
                shapers[name] = existing
        latest = {}
        for hop in parsed:
            latest = {name: shaper.render(hop) for name, shaper in shapers.items()}
        self._shapers.update(shapers)
        # Merge, never replace: an attributed batch updates only its own
        # performer, otherwise one robot's envelope would erase the other's.
        self._latest.update(latest)
        self._stream_id = stream_id
        self._next_sequence = expected
        self._performer_sequences[performer_ids[0]] = (stream_id, expected)
        self._latest_hop = parsed[-1]
        self._received_at_ns = time.monotonic_ns()
        return {
            "frames_accepted": len(parsed),
            "stream_id": stream_id,
            "last_sequence": expected - 1,
            "timing_source": "generated_pcm",
            "physical_motion_dispatched": False,
        }

    def latest(self, performer_id: str) -> ExpressionChannels | None:
        """Unitless channels for one bound performer, or None when stale.

        Staleness is wall-clock only: a room that stopped sending hops must
        not keep a robot moving on an old envelope.
        """
        if self._received_at_ns is None:
            return None
        if (time.monotonic_ns() - self._received_at_ns) > _STALE_NS:
            return None
        return self._latest.get(performer_id)

    def snapshot(self) -> dict[str, object]:
        if self._latest_hop is None or self._received_at_ns is None:
            return {"available": False, "physical_motion_dispatched": False}
        return {
            "available": True,
            "stream_id": self._stream_id,
            "last_sequence": self._latest_hop.sequence,
            "intensity": self._latest_hop.intensity,
            "active": self._latest_hop.active,
            "timing_source": "generated_pcm",
            "stale": time.monotonic_ns() - self._received_at_ns > 750_000_000,
            "performers": sorted(self._latest),
            "physical_motion_dispatched": False,
        }
