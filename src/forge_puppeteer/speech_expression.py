"""Portable speech-to-expression shaper, deliberately not a robot controller.

Pollen's pinned Reachy speech tapper inspired the oscillator frequencies.
This module receives content-free 50 ms generated-PCM envelope features,
produces dimensionless expression channels, and never opens a device or
submits a motion. Device adapters and ExecutionRuntime retain authority.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from .robot_contracts import ContractModel

SCHEMA = "forge.speech_envelope.v1"
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

    def __init__(self, *, performer_id: str, stream_id: str) -> None:
        if not performer_id or len(performer_id) > 128:
            raise ValueError("performer_id must be a bounded nonempty string")
        if not stream_id or len(stream_id) > 128:
            raise ValueError("stream_id must be a bounded nonempty string")
        self.performer_id = performer_id
        self.stream_id = stream_id
        self._next_sequence = 0
        # Stable per-performer phases prevent three robots from moving in
        # mechanically identical lockstep while preserving shared speech timing.
        phases: dict[str, float] = {}
        for name in FREQUENCIES_HZ:
            digest = hashlib.sha256(
                f"{performer_id}:{stream_id}:{name}".encode("utf-8")
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
        level = item.intensity
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
