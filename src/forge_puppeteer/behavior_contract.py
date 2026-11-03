"""Portable, versioned semantic behaviors; never a robot SDK or LLM persona.

Puppeteer owns the patterns and scheduling. ExecutionRuntime already owns action
identity, SDK-backed provider validation, resource admission, provenance and STOP.
A behavior binding is *not* a hardware grant or a personality/tool policy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .robot_contracts import ContractModel

BEHAVIOR_SCHEMA = "forge.behavior.v1"
Presence = Literal["idle", "listening", "speaking", "disconnected"]
TimingSource = Literal["agent_state", "playback", "manual"]
Mode = Literal["once", "loop", "hold"]
Source = Literal["procedural", "recorded", "sequence"]


class BehaviorCue(ContractModel):
    """One typed provider operation at a monotonic offset from the pattern start.

    Native providers validate the operation, parameters, complete trajectory,
    revisions and actual motion authority. This is not a motor-command language.
    """

    at_s: float = Field(ge=0, le=3600)
    operation: str = Field(min_length=1, max_length=128)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    asset_id: str | None = Field(default=None, min_length=1, max_length=256)
    asset_revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def pinned_asset(self):
        if (self.asset_id is None) != (self.asset_revision is None):
            raise ValueError("asset ID and SHA-256 revision must appear together")
        return self


class BehaviorPattern(ContractModel):
    """Finite choreography unit; 'loop' repeats it only while presence is fresh.

    A pattern never creates its own SDK connection. Loop update rates and
    durations are bounded; slow SDK calls do not create catch-up bursts.
    """

    source: Source
    mode: Mode
    cues: tuple[BehaviorCue, ...] = Field(max_length=64)
    period_s: float | None = Field(default=None, ge=0.2, le=120)
    max_cycles: int = Field(default=360, ge=1, le=3600)
    max_duration_s: float = Field(default=3600, gt=0, le=3600)

    @model_validator(mode="after")
    def validate_pattern(self):
        if self.mode == "hold":
            if self.source != "procedural" or self.cues or self.period_s is not None:
                raise ValueError("hold must be procedural with no cues or period")
            return self
        if not self.cues:
            raise ValueError("movement patterns require at least one cue")
        offsets = [cue.at_s for cue in self.cues]
        if offsets != sorted(offsets) or len(offsets) != len(set(offsets)):
            raise ValueError("behavior cue offsets must be strictly increasing")
        if self.mode == "loop":
            if self.period_s is None or offsets[-1] >= self.period_s:
                raise ValueError("loop requires a period longer than all cue offsets")
        elif self.period_s is not None:
            raise ValueError("finite behavior must not specify a loop period")
        if offsets[-1] > self.max_duration_s:
            raise ValueError("last cue exceeds the behavior's duration bound")
        if self.source == "recorded" and any(c.asset_id is None for c in self.cues):
            raise ValueError("recorded behavior requires pinned assets for every cue")
        return self


class PerformerBehaviors(ContractModel):
    """One robot's semantic bindings, independent of conversational personality.

    The same profile may be driven by 4G, another LLM persona, a text-based
    agent or a scripted director. Device-specific amplitudes are authored here,
    but native SDK path and limit validation remain authoritative.
    """

    schema: Literal["forge.behavior.v1"] = BEHAVIOR_SCHEMA
    performer_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    behaviors: dict[str, BehaviorPattern] = Field(min_length=1)

    @classmethod
    def author(cls, performer_id: str, behaviors: dict[str, BehaviorPattern]):
        """Build a content-addressed pack without asking authors to invent hashes."""
        body = {
            "schema": BEHAVIOR_SCHEMA,
            "performer_id": performer_id,
            "behaviors": {name: pattern.model_dump(mode="json") for name, pattern in behaviors.items()},
        }
        revision = "sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return cls(performer_id=performer_id, revision=revision, behaviors=behaviors)

    def content_revision(self) -> str:
        payload = self.model_dump(mode="json", exclude={"revision"})
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def validate_bindings(self):
        if any(not name or len(name) > 64 for name in self.behaviors):
            raise ValueError("invalid semantic behavior name")
        for name in ("idle", "listening", "speaking"):
            pattern = self.behaviors.get(name)
            allowed = {"loop", "hold"} if name == "listening" else {"loop"}
            if pattern is not None and pattern.mode not in allowed:
                raise ValueError(f"{name} must be an interruptible loop or supported hold")
        if any(name != "listening" and pattern.mode == "hold"
               for name, pattern in self.behaviors.items()):
            raise ValueError("hold mode is supported only for listening")
        for name in ("yes", "no", "acknowledge"):
            pattern = self.behaviors.get(name)
            if pattern is not None and pattern.mode != "once":
                raise ValueError(f"{name} must be a finite gesture")
        if self.revision != self.content_revision():
            raise ValueError("behavior pack SHA-256 revision does not match authored content")
        return self


class EnsemblePresence(ContractModel):
    """One content-free event from a conversation or scripted caller."""

    session_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    state: Presence
    source: TimingSource
    interrupted: bool = False

    @model_validator(mode="after")
    def interruption_state(self):
        if self.interrupted and self.state not in {"idle", "listening", "disconnected"}:
            raise ValueError("interrupted requires idle, listening, or disconnected")
        return self
