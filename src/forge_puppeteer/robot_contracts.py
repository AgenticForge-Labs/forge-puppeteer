"""Device-neutral metadata for explicit robot adapters.

These models describe capabilities; they do not grant authority or connect to a
device. Device-specific adapters remain responsible for validating and executing
their operations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

CONTRACT_VERSION = "1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ActionState(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ControlDescriptor(ContractModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    effect: Literal["read", "visual", "light", "audio", "motion", "stop"]
    parameters_schema: dict[str, JsonValue]
    resources: tuple[str, ...] = ()
    unit: str | None = None
    limits: dict[str, JsonValue] = Field(default_factory=dict)
    concurrency_constraints: tuple[str, ...] = ()


class AssetDescriptor(ContractModel):
    id: str = Field(min_length=1)
    kind: Literal["pose", "recording", "sequence", "behavior"]
    source: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    description: str = ""
    duration_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    resources: tuple[str, ...] = ()
    parameters_schema: dict[str, JsonValue] = Field(default_factory=dict)
    calibration_reference: str | None = None
    device_compatibility: dict[str, JsonValue] = Field(default_factory=dict)
    audio_association: str | None = None


class DeviceDescriptor(ContractModel):
    contract_version: Literal["1"] = CONTRACT_VERSION
    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    sdk_version: str | None = None
    firmware_version: str | None = None
    connected: bool
    ready: bool
    supported_operations: tuple[str, ...] = ()
    controls: tuple[ControlDescriptor, ...] = ()
    feedback: Literal["commanded", "measured", "mixed", "unknown"] = "unknown"
    resource_relationships: dict[str, JsonValue] = Field(default_factory=dict)
    effective_limits: dict[str, JsonValue] = Field(default_factory=dict)
    authority: dict[str, JsonValue] = Field(default_factory=dict)


class ActionRecord(ContractModel):
    contract_version: Literal["1"] = CONTRACT_VERSION
    id: str = Field(min_length=1)
    state: ActionState
    operation: str = Field(min_length=1)
    caller_id: str | None = None
    session_id: str | None = None
    cue_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    asset_revision: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    resources: tuple[str, ...] = ()
    timestamps: dict[str, str] = Field(default_factory=dict)
    result: dict[str, JsonValue] | None = None
    error: str | None = None
    completion_evidence: str | None = None
    cancellation_outcome: str | None = None


class DeviceProvider(Protocol):
    """Explicit provider surface; availability does not imply motion authority."""

    async def describe(self) -> DeviceDescriptor: ...

    async def list_assets(self) -> tuple[AssetDescriptor, ...]: ...

    async def stop(self) -> None: ...
