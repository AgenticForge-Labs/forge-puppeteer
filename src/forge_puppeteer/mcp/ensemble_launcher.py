"""Operator-configured standalone native-robot ensemble stdio launcher.

This is not an LLM, audio player, or Platform client. It reuses the existing
Reachy/StackChan launcher SDK connections, one persistent SDK-host SO-ARM101
executor and watchdog, and Puppeteer's single ExecutionRuntime/Ensemble.

Physical SO-ARM is disabled unless an operator explicitly selects the attended
trial mode, an existing human lease and torque HOLD, exact calibration-bound
programs, and the full existing native SDK/watchdog admission.
"""

from __future__ import annotations

import argparse
import json
import math
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors
from ..conversation_ensemble import ConversationEnsemble
from ..conversation_native import (
    reachy_pack_from_catalog,
    stackchan_conversation_pack,
    stackchan_recorded_assets,
)
from ..conversation_soarm import EXPECTED, soarm_conversation_pack
from ..execution import ExecutionRuntime
from ..runtime import PerformanceStage
from .ensemble import create_ensemble_server
from .launcher import ReachySDKProvider, ServerConfig, StackChanSDKProvider


def _check_reachy_expression_authority(
    style: str,
    neutral_antennas_rad: tuple[float, float] | None,
    granted_tools: list[str],
    antenna_sway_rad: float,
) -> None:
    """Expressive motion requires an explicit compound SDK grant and reviewed neutral."""
    if style != "expressive":
        return
    if "pose_and_antennas" not in granted_tools:
        raise ValueError("expressive Reachy requires pose_and_antennas tool grant")
    if neutral_antennas_rad is None:
        raise ValueError("expressive Reachy requires operator-reviewed neutral antenna angles")
    # Validate whole authored envelope before the SDK opens a device.
    from ..devices.reachy import _ANTENNA_ANGLE_LIMIT_RAD
    if any(not isinstance(v, (int, float)) or not math.isfinite(v)
           or abs(v) + antenna_sway_rad > _ANTENNA_ANGLE_LIMIT_RAD
           for v in neutral_antennas_rad):
        raise ValueError("expressive Reachy antenna motion is not within adapter bounds")


class ReviewedJointFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at_s: float = Field(ge=0)
    joints_rad: tuple[float, float, float, float, float]


class ReviewedMotion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frames: tuple[ReviewedJointFrame, ...] = Field(min_length=2, max_length=256)


class SOARMEnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1)
    robot_id: str = Field(min_length=1)
    calibration_id: str = Field(min_length=1)
    mode: Literal["simulation", "attended_physical_trial"] = "simulation"
    port: str | None = None
    human_lease_path: str | None = None
    physical_authority: str | None = None
    max_joint_speed_deg_s: float = Field(default=8, gt=0, le=100)
    max_joint_acceleration_deg_s2: float = Field(default=25, gt=0, le=1000)
    max_duration_s: float = Field(default=30, gt=0, le=30)
    max_start_error_rad: float = Field(default=0.02, gt=0, le=0.05)
    startup_behavior: Literal["none", "wake_to_neutral"] = "none"
    idle_behavior: Literal["legacy", "breathe"] = "legacy"
    startup_target_joints_rad: tuple[float, float, float, float, float] | None = None
    startup_target_revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    startup_authority: str | None = None
    programs: list[ReviewedMotion] = Field(min_length=7, max_length=25)
    approved_revisions: list[str] = Field(min_length=7, max_length=26)
    watchdog_timeout_s: float = Field(default=4, ge=0.5, le=10)

    @model_validator(mode="after")
    def validate_operator_config(self):
        if self.mode == "simulation":
            if (self.port is not None or self.human_lease_path is not None
                    or self.physical_authority is not None
                    or self.calibration_id != "simulation"):
                raise ValueError("simulation cannot declare physical robot authority")
        else:
            if (not self.port or not Path(self.port).is_absolute()
                    or not self.human_lease_path
                    or not Path(self.human_lease_path).is_absolute()
                    or self.physical_authority != "I_AUTHORIZE_ATTENDED_PHYSICAL_CHOREOGRAPHY"
                    or self.calibration_id == "simulation"):
                raise ValueError("physical SO-ARM requires explicit port, lease, calibration and operator opt-in")
        if len({p.name for p in self.programs}) != len(self.programs):
            raise ValueError("duplicate SO-ARM program names")
        if not EXPECTED <= {p.name for p in self.programs}:
            raise ValueError("missing required SO-ARM semantic programs")
        if self.idle_behavior == "breathe" and "breathe" not in {p.name for p in self.programs}:
            raise ValueError("breathe idle requires an exact pinned breathe program")
        if len(set(self.approved_revisions)) != len(self.approved_revisions):
            raise ValueError("duplicate approved revision pins")
        program_revisions = {p.revision for p in self.programs}
        has_target = self.startup_target_joints_rad is not None or self.startup_target_revision is not None
        if self.startup_behavior == "wake_to_neutral":
            if (self.startup_target_joints_rad is None or self.startup_target_revision is None
                    or self.startup_target_revision not in self.approved_revisions):
                raise ValueError("wake-to-neutral startup requires an exact pinned five-joint target")
            if self.mode == "attended_physical_trial" and self.startup_authority != "I_AUTHORIZE_SOARM101_WAKE_TO_NEUTRAL_STARTUP":
                raise ValueError("physical wake-to-neutral requires its separate explicit startup authorization")
            if self.mode == "simulation" and self.startup_authority is not None:
                raise ValueError("simulation startup must not declare physical startup authority")
            from soarm101_motion.choreography import JointTarget
            authored_target = JointTarget.author(
                "wake_to_neutral", self.robot_id, self.calibration_id,
                tuple(self.startup_target_joints_rad),
            )
            if authored_target.revision != self.startup_target_revision:
                raise ValueError("wake-to-neutral target content differs from its exact revision pin")
        elif has_target or self.startup_authority is not None:
            raise ValueError("startup target and authorization require startup_behavior=wake_to_neutral")
        expected_revisions = program_revisions | (
            {self.startup_target_revision} if self.startup_behavior == "wake_to_neutral" else set()
        )
        if expected_revisions != set(self.approved_revisions):
            raise ValueError("all configured program and startup target revisions must be pinned exactly once")
        return self


class PerformerMotionSelection(BaseModel):
    """Explicit independent idle/speaking choices; no implicit SDK permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    idle: Literal["legacy", "native_v1", "smooth_v2", "original_v1"] = "legacy"
    speaking: Literal["legacy", "native_v1", "smooth_v2", "original_v1"] = "legacy"


def _validate_motion_selection(
    choices: dict[str, PerformerMotionSelection],
    performer_ids: tuple[str, ...],
    *,
    reachy_id: str,
    stackchan_id: str | None,
    speech_driven: tuple[str, ...],
    tools: list[str],
    neutral_antennas: tuple[float, float] | None,
    antenna_sway_rad: float,
    original_idle_authority: str | None,
    original_speech_authority: str | None,
) -> None:
    selected = set(performer_ids)
    if set(choices) - selected:
        raise ValueError("motion profile references an unknown performer")
    if len(set(speech_driven)) != len(speech_driven):
        raise ValueError("speech-driven performer IDs must be unique")
    if set(speech_driven) - selected or set(speech_driven) - {
        reachy_id, stackchan_id,
    }:
        raise ValueError("speech-driven performer must be native Reachy or StackChan")
    for performer_id, profile in choices.items():
        nonlegacy = profile.idle != "legacy" or profile.speaking != "legacy"
        if nonlegacy and performer_id not in {reachy_id, stackchan_id}:
            raise ValueError("SO-ARM only supports legacy approved choreography")
        if profile.idle == "original_v1":
            if performer_id != reachy_id:
                raise ValueError("original_v1 idle is available only for Reachy")
            if "original_idle" not in tools or original_idle_authority != "I_AUTHORIZE_REACHY_ORIGINAL_IDLE":
                raise ValueError("original_v1 idle requires original_idle grant and explicit authority")
        if profile.speaking == "original_v1":
            if performer_id != reachy_id or profile.idle != "original_v1":
                raise ValueError("original_v1 speaking requires original_v1 Reachy idle")
            if ("original_speech" not in tools
                    or original_speech_authority != "I_AUTHORIZE_REACHY_ORIGINAL_SPEECH"):
                raise ValueError("original_v1 speaking requires original_speech grant and explicit authority")
        if performer_id == reachy_id and (
            profile.idle in {"native_v1", "smooth_v2"} or profile.speaking in {"native_v1", "smooth_v2"}
        ):
            _check_reachy_expression_authority(
                "expressive", neutral_antennas, tools, antenna_sway_rad,
            )
        if profile.speaking == "smooth_v2" and performer_id not in speech_driven:
            raise ValueError("smooth_v2 speaking requires explicit speech-driven opt-in")


def _profile_overrides(
    choices: dict[str, PerformerMotionSelection],
    native: dict[str, PerformerBehaviors],
    smooth_idle: dict[str, PerformerBehaviors] | None = None,
    original_idle: dict[str, PerformerBehaviors] | None = None,
) -> dict[str, dict[str, PerformerBehaviors]]:
    """Select native or amplitude-reduced idle without new motor ownership."""
    result: dict[str, dict[str, PerformerBehaviors]] = {}
    for performer_id, selection in choices.items():
        alternatives: dict[str, PerformerBehaviors] = {}
        for state in ("idle", "speaking"):
            name = getattr(selection, state)
            if name == "legacy":
                continue
            if name == "original_v1":
                alternatives[state] = (original_idle or {})[performer_id]
            elif state == "idle" and name == "smooth_v2":
                alternatives[state] = (smooth_idle or {}).get(
                    performer_id, native[performer_id],
                )
            else:
                alternatives[state] = native[performer_id]
        if alternatives:
            result[performer_id] = alternatives
    return result


def _original_idle_pack(pack: PerformerBehaviors) -> PerformerBehaviors:
    """Bind one bounded SDK action for the entire room-owned idle interval."""
    idle = BehaviorPattern(
        source="procedural", mode="loop", period_s=120,
        max_duration_s=3600,
        cues=(BehaviorCue(at_s=0, operation="original_idle", parameters={"duration_s": 3600}),),
    )
    return PerformerBehaviors.author(pack.performer_id, {**pack.behaviors, "idle": idle, "speaking": idle},
                                     startup=pack.startup)


def _stackchan_startup_pack(
    pack: PerformerBehaviors, behavior: str,
) -> PerformerBehaviors:
    """Bind only the configured authored neutral as a finite startup cue."""
    if behavior == "none":
        return pack
    if behavior != "neutral" or "neutral" not in pack.behaviors:
        raise ValueError("unsupported StackChan startup behavior")
    return PerformerBehaviors.author(
        pack.performer_id, pack.behaviors, startup=pack.behaviors["neutral"],
    )


def _check_original_idle_reference(config: EnsembleConfig | ReachyOnlyEnsembleConfig) -> None:
    """Require the reviewed reference to match the original app's fixed neutral."""
    selection = config.motion_profiles.get(config.reachy.instance_id)
    if selection is None or selection.idle != "original_v1":
        return
    matrix = json.loads(Path(config.reachy_neutral_head).read_text(encoding="utf-8"))
    if not isinstance(matrix, list) or len(matrix) != 4 or any(
        not isinstance(row, list) or len(row) != 4
        or any(type(value) not in (int, float) or not math.isfinite(value)
               or abs(value - (1.0 if i == j else 0.0)) > 1e-9
               for j, value in enumerate(row))
        for i, row in enumerate(matrix)
    ):
        raise ValueError("original_v1 requires an operator-reviewed identity neutral reference")


class EnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1, 3]
    motion_authority: Literal["I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"]
    reachy: ServerConfig
    stackchan: ServerConfig
    stackchan_display_presets_enabled: bool = False
    stackchan_startup_behavior: Literal["none", "neutral"] = "none"
    reachy_neutral_head: str
    soarm101: SOARMEnsembleConfig | None = None
    action_journal: str
    ownership_db: str
    presence_ttl_s: float = Field(default=30, ge=1, le=3600)
    stop_timeout_s: float = Field(default=5, ge=0.1, le=60)
    stackchan_speaking_mode: Literal["legacy_size", "native_weight"] = "legacy_size"
    # Operator opt-in: performers whose speaking slot follows the live
    # generated-PCM envelope instead of the authored pattern.
    speech_driven_performers: tuple[str, ...] = ()
    reachy_legacy_speech_fallback: bool = Field(default=False, strict=True)
    speech_seed: int = 7
    motion_profiles: dict[str, PerformerMotionSelection] = Field(default_factory=dict)
    # Hold the first spoken turn until startup motion (if any) has settled.
    # The pinning of Reachy's own SDK wake-up is the alternative; raising the
    # head and antennas to the reviewed neutral does not need the choreography.
    startup_settle_s: float = Field(default=3.0, ge=0, le=30)
    reachy_expression_style: Literal["classic", "expressive"] = "classic"
    reachy_startup_behavior: Literal["none", "wake_up_if_sleeping"] = "none"
    reachy_neutral_antennas_rad: tuple[float, float] | None = None
    reachy_expressive_amplitude_rad: float = Field(default=0.09, ge=0, le=0.12)
    reachy_antenna_sway_rad: float = Field(default=0.18, ge=0, le=0.26)
    reachy_breath_amplitude_m: float = Field(default=0.0, ge=0, le=0.005)
    reachy_original_idle_authority: Literal["I_AUTHORIZE_REACHY_ORIGINAL_IDLE"] | None = None
    reachy_original_speech_authority: Literal["I_AUTHORIZE_REACHY_ORIGINAL_SPEECH"] | None = None

    @model_validator(mode="before")
    @classmethod
    def require_explicit_performer_selection(cls, value):
        # v1 is the existing three-robot contract. v3 explicitly selects
        # Reachy + StackChan and refuses even a nullable SO-ARM record.
        if (isinstance(value, dict) and value.get("schema_version") == 3
                and "soarm101" in value):
            raise ValueError("two-robot ensemble cannot declare SO-ARM101")
        return value

    @model_validator(mode="after")
    def validate_ensemble(self):
        if self.reachy.backend != "reachy_sdk" or self.reachy.device != "reachy":
            raise ValueError("Reachy native SDK provider required")
        if self.stackchan.backend != "stackchan_sdk" or self.stackchan.device != "stackchan":
            raise ValueError("StackChan native SDK provider required")
        if self.schema_version == 1 and self.soarm101 is None:
            raise ValueError("three-robot ensemble requires an SO-ARM101 profile")
        if self.schema_version == 3 and self.soarm101 is not None:
            raise ValueError("two-robot ensemble excludes SO-ARM101")
        ids = [self.reachy.instance_id, self.stackchan.instance_id]
        if self.soarm101 is not None:
            ids.append(self.soarm101.instance_id)
        if len(set(ids)) != len(ids):
            raise ValueError("unique performer IDs required")
        if self.reachy_legacy_speech_fallback and (
            self.reachy.instance_id not in self.speech_driven_performers
            or self.reachy_expression_style != "classic"
            or self.motion_profiles.get(self.reachy.instance_id, PerformerMotionSelection()).speaking != "smooth_v2"
        ):
            raise ValueError("legacy speech fallback requires smooth_v2, speech-driven Reachy and classic style")
        _validate_motion_selection(
            self.motion_profiles, tuple(ids),
            reachy_id=self.reachy.instance_id,
            stackchan_id=self.stackchan.instance_id,
            speech_driven=self.speech_driven_performers,
            tools=self.reachy.allowed_tools,
            neutral_antennas=self.reachy_neutral_antennas_rad,
            antenna_sway_rad=self.reachy_antenna_sway_rad,
            original_idle_authority=self.reachy_original_idle_authority,
            original_speech_authority=self.reachy_original_speech_authority,
        )
        for value in (self.action_journal, self.ownership_db, self.reachy_neutral_head):
            if not value or not Path(value).is_absolute():
                raise ValueError("action history, ownership and Reachy neutral pose require absolute paths")
        if Path(self.action_journal).resolve() == Path(self.ownership_db).resolve():
            raise ValueError("action journal and ownership DB must be distinct")
        if self.reachy.restricted_actions:
            raise ValueError("head expression requires Reachy's normal native SDK mode")
        _check_reachy_expression_authority(
            self.reachy_expression_style, self.reachy_neutral_antennas_rad,
            self.reachy.allowed_tools, self.reachy_antenna_sway_rad,
        )
        reachy_tools = {"head_pose", "play_move", "device_stop"}
        stack_tools = {"head_move", "face_update", "device_stop"}
        if not reachy_tools <= set(self.reachy.allowed_tools):
            raise ValueError("native Reachy expression requires head, playback and stop grants")
        if (self.reachy_startup_behavior == "wake_up_if_sleeping"
                and "wake_up" not in self.reachy.allowed_tools):
            raise ValueError("Reachy wake-up startup requires the explicit wake_up grant")
        if not stack_tools <= set(self.stackchan.allowed_tools):
            raise ValueError("native StackChan expression requires head, face and stop grants")
        if self.stackchan_display_presets_enabled and (
            self.stackchan.sdk_version != "0.4.0"
            or "face_scale_update" not in self.stackchan.allowed_tools
        ):
            raise ValueError(
                "StackChan display presets require native SDK 0.4.0 and face_scale_update grant"
            )
        if self.stackchan.physical_authority != "I_AUTHORIZE_STACKCHAN_CONTROL":
            raise ValueError("StackChan expression requires operator physical authorization")
        if (self.soarm101 is not None and self.soarm101.mode == "attended_physical_trial"
                and self.stackchan.port == self.soarm101.port):
            raise ValueError("StackChan and physical SO-ARM cannot share one serial port")
        return self


class ReachyOnlyEnsembleConfig(BaseModel):
    """Explicit single-performer deployment; no other device is opened."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2]
    motion_authority: Literal["I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"]
    reachy: ServerConfig
    reachy_neutral_head: str
    action_journal: str
    ownership_db: str
    presence_ttl_s: float = Field(default=30, ge=1, le=3600)
    stop_timeout_s: float = Field(default=5, ge=0.1, le=60)
    speaking_axis: Literal["yaw", "tilt"] = "yaw"
    speaking_amplitude_rad: float = Field(default=0.025, ge=0, le=0.07)
    listening_tilt_rad: float = Field(default=0.02, ge=0, le=0.07)
    listening_mode: Literal["tilt", "hold"] = "tilt"
    speech_driven_performers: tuple[str, ...] = ()
    reachy_legacy_speech_fallback: bool = Field(default=False, strict=True)
    motion_profiles: dict[str, PerformerMotionSelection] = Field(default_factory=dict)
    reachy_expression_style: Literal["classic", "expressive"] = "classic"
    reachy_startup_behavior: Literal["none", "wake_up_if_sleeping"] = "none"
    reachy_neutral_antennas_rad: tuple[float, float] | None = None
    reachy_expressive_amplitude_rad: float = Field(default=0.09, ge=0, le=0.12)
    reachy_antenna_sway_rad: float = Field(default=0.18, ge=0, le=0.26)
    reachy_breath_amplitude_m: float = Field(default=0.0, ge=0, le=0.005)
    reachy_original_idle_authority: Literal["I_AUTHORIZE_REACHY_ORIGINAL_IDLE"] | None = None
    reachy_original_speech_authority: Literal["I_AUTHORIZE_REACHY_ORIGINAL_SPEECH"] | None = None

    @model_validator(mode="after")
    def validate_reachy(self):
        if self.reachy.backend != "reachy_sdk" or self.reachy.device != "reachy":
            raise ValueError("Reachy native SDK provider required")
        if self.reachy.restricted_actions:
            raise ValueError("head expression requires Reachy's normal native SDK mode")
        if self.reachy_legacy_speech_fallback and (
            self.reachy.instance_id not in self.speech_driven_performers
            or self.reachy_expression_style != "classic"
            or self.motion_profiles.get(self.reachy.instance_id, PerformerMotionSelection()).speaking != "smooth_v2"
        ):
            raise ValueError("legacy speech fallback requires smooth_v2, speech-driven Reachy and classic style")
        _check_reachy_expression_authority(
            self.reachy_expression_style, self.reachy_neutral_antennas_rad,
            self.reachy.allowed_tools, self.reachy_antenna_sway_rad,
        )
        _validate_motion_selection(
            self.motion_profiles, (self.reachy.instance_id,),
            reachy_id=self.reachy.instance_id, stackchan_id=None,
            speech_driven=self.speech_driven_performers,
            tools=self.reachy.allowed_tools,
            neutral_antennas=self.reachy_neutral_antennas_rad,
            antenna_sway_rad=self.reachy_antenna_sway_rad,
            original_idle_authority=self.reachy_original_idle_authority,
            original_speech_authority=self.reachy_original_speech_authority,
        )
        if not {"head_pose", "play_move", "device_stop"} <= set(self.reachy.allowed_tools):
            raise ValueError("native Reachy expression requires head, playback and stop grants")
        if (self.reachy_startup_behavior == "wake_up_if_sleeping"
                and "wake_up" not in self.reachy.allowed_tools):
            raise ValueError("Reachy wake-up startup requires the explicit wake_up grant")
        for value in (self.action_journal, self.ownership_db, self.reachy_neutral_head):
            if not value or not Path(value).is_absolute():
                raise ValueError("action history, ownership and Reachy neutral pose require absolute paths")
        if Path(self.action_journal).resolve() == Path(self.ownership_db).resolve():
            raise ValueError("action history and ownership paths must differ")
        return self


def soarm_native_binding(config: SOARMEnsembleConfig):
    """Build ONLY exact source-controlled reviewed programs and native policy."""
    from soarm101_motion.choreography import (
        ChoreographyPolicy,
        JointChoreography,
        JointFrame,
    )

    programs = {
        item.name: JointChoreography(
            item.name, config.robot_id, config.calibration_id,
            tuple(JointFrame(frame.at_s, frame.joints_rad) for frame in item.frames),
            item.revision,
        ) for item in config.programs
    }
    startup_target = None
    if config.startup_behavior == "wake_to_neutral":
        from soarm101_motion.choreography import JointTarget
        startup_target = JointTarget(
            "wake_to_neutral", config.robot_id, config.calibration_id,
            tuple(config.startup_target_joints_rad), config.startup_target_revision,
        )
    policy = ChoreographyPolicy(
        config.robot_id, config.calibration_id,
        tuple(config.approved_revisions),
        max_duration_s=config.max_duration_s,
        max_start_error_rad=config.max_start_error_rad,
        max_speed_deg_s=config.max_joint_speed_deg_s,
        max_acceleration_deg_s2=config.max_joint_acceleration_deg_s2,
        allow_physical_trial=config.mode == "attended_physical_trial",
    )
    pack = soarm_conversation_pack(
        config.instance_id, programs, approved_revisions=policy.allowed_revisions,
        startup_target=startup_target, idle_behavior=config.idle_behavior,
    )
    return programs, policy, pack


def build_soarm_choreography_provider(config: SOARMEnsembleConfig, journal_path: str | Path):
    """Build the shared native SDK executor, watchdog, provider, pack and lifecycle."""
    from soarm101_motion.agent_control import AgentAuthorityStore
    from soarm101_motion.broker import AgentMotionRates
    from soarm101_motion.broker_sdk import SDKAgentExecutor
    from soarm101_motion.broker_watchdog import BrokerWatchdog
    from soarm101_motion.config import SOARM101Config
    programs, policy, pack = soarm_native_binding(config)
    startup_target = None
    if config.startup_behavior == "wake_to_neutral":
        from soarm101_motion.choreography import JointTarget
        startup_target = JointTarget("wake_to_neutral", config.robot_id, config.calibration_id,
                                     tuple(config.startup_target_joints_rad),
                                     config.startup_target_revision)
    physical = config.mode == "attended_physical_trial"
    sdk_config = SOARM101Config(
        robot_id=config.robot_id, port=config.port if physical else None,
        auto_enable_torque=False, configure_motors_on_connect=False,
        disable_torque_on_disconnect=False,
    )
    rates = AgentMotionRates(
        joint_speed_deg_s=config.max_joint_speed_deg_s,
        joint_acceleration_deg_s2=config.max_joint_acceleration_deg_s2,
    ).validated(sdk_config)
    executor = SDKAgentExecutor(
        config=sdk_config, rates=rates, simulation=not physical,
        simulation_actions=not physical, physical_actions=physical,
        authority_store=AgentAuthorityStore(
            Path(config.human_lease_path) if physical
            else Path(journal_path).with_suffix(".simulation-lease.json")),
    )
    watchdog = BrokerWatchdog(
        timeout_s=config.watchdog_timeout_s,
        validate=executor.validate_supervision,
        trip=lambda _reason: executor.execute("stop", {}),
    )
    executor.motion_guard = watchdog.check
    from ..devices.soarm101_choreography import SOARMChoreographyProvider
    provider = SOARMChoreographyProvider(executor, watchdog, policy, programs, startup_target)

    @asynccontextmanager
    async def connected():
        try:
            await anyio.to_thread.run_sync(executor.execute, "state", {})
            watchdog.start()
            watchdog.heartbeat()
            async def heartbeat_loop():
                while True:
                    await anyio.sleep(config.watchdog_timeout_s / 4)
                    await anyio.to_thread.run_sync(watchdog.heartbeat)
            async with anyio.create_task_group() as workers:
                workers.start_soon(heartbeat_loop)
                try:
                    yield
                finally:
                    workers.cancel_scope.cancel()
        finally:
            watchdog.close()
            await anyio.to_thread.run_sync(executor.close)
    return provider, pack, connected, policy


def build_ensemble(config: EnsembleConfig):
    """The original three-performer assembly; unavailable for duo configs."""
    if config.soarm101 is None:
        raise ValueError("SO-ARM101 profile required for three-performer builder")
    reachy = ReachySDKProvider(config.reachy)
    stackchan = StackChanSDKProvider(config.stackchan)
    arm_options = config.soarm101
    arm, arm_pack, arm_connected, policy = build_soarm_choreography_provider(
        arm_options, config.action_journal,
    )

    runtime = ExecutionRuntime(
        PerformanceStage(), journal_path=config.action_journal,
        ownership_path=config.ownership_db,
        execution_timeout=max(60, policy.max_duration_s + 10),
        stop_timeout=config.stop_timeout_s,
    )
    for instance_id, provider in (
        (config.reachy.instance_id, reachy),
        (config.stackchan.instance_id, stackchan),
        (arm_options.instance_id, arm),
    ):
        runtime.register(instance_id, provider)

    # The catalog is verified before opening Reachy's SDK hardware connection.
    # Bind all three packs after the verified asset inventory is available.
    # ReachyCatalog.list_assets() is async, hence assembly within connected().
    @asynccontextmanager
    async def connected():
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(arm_connected())
            await stack.enter_async_context(reachy.connected())
            await stack.enter_async_context(stackchan.connected())
            yield

    # reachy native pack is resolved from the pinned manifest/catalog; these
    # read-only bytes are checked before any hardware connection in serve().
    return reachy, stackchan, arm, arm_pack, runtime, connected



def build_reachy_stackchan_ensemble(config: EnsembleConfig):
    """Two native SDK connections, one journal; no SO-ARM import or simulator.

    The existing Reachy and StackChan providers are identical to the v1
    three-performer deployment. No second controller or alternative STOP path
    is introduced. This factory intentionally does not import soarm101_motion.
    """
    if config.schema_version != 3 or config.soarm101 is not None:
        raise ValueError("explicit two-performer configuration required")
    reachy = ReachySDKProvider(config.reachy)
    stackchan = StackChanSDKProvider(config.stackchan)
    runtime = ExecutionRuntime(
        PerformanceStage(), journal_path=config.action_journal,
        ownership_path=config.ownership_db,
        stop_timeout=config.stop_timeout_s,
    )
    runtime.register(config.reachy.instance_id, reachy)
    runtime.register(config.stackchan.instance_id, stackchan)

    @asynccontextmanager
    async def connected():
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(reachy.connected())
            await stack.enter_async_context(stackchan.connected())
            yield

    return reachy, stackchan, runtime, connected


def load_config(path: str | Path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 4:
        from .ensemble_v4 import load_v4
        return load_v4(path)
    if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 2:
        return ReachyOnlyEnsembleConfig.model_validate(data)
    return EnsembleConfig.model_validate(data)


def config_readiness(path: str | Path) -> dict[str, object]:
    """Validate the operator config without importing device SDKs or connecting.

    This is deliberately a file/configuration check only. In particular, it
    does not infer that a configured port, lease, SDK daemon, or robot is live.
    """
    config = load_config(path)
    if isinstance(config, tuple):
        room, _robots, _options = config
        return {
            "configuration": "valid", "schema_version": 4,
            "performers": [robot.id for robot in room.robots],
            "robot_kinds": [robot.kind for robot in room.robots],
            "robot_files_resolved": True,
            "device_connections_attempted": False,
            "motion_authority_checked": False,
        }
    _check_original_idle_reference(config)
    if isinstance(config, ReachyOnlyEnsembleConfig):
        performers = [config.reachy.instance_id]
        schema_version = 2
        soarm_mode = None
    elif config.schema_version == 3:
        performers = [config.reachy.instance_id, config.stackchan.instance_id]
        schema_version = 3
        soarm_mode = None
    else:
        assert config.soarm101 is not None
        performers = [config.reachy.instance_id, config.stackchan.instance_id,
                      config.soarm101.instance_id]
        schema_version = config.schema_version
        soarm_mode = config.soarm101.mode
    return {
        "configuration": "valid",
        "schema_version": schema_version,
        "performers": performers,
        "soarm101_mode": soarm_mode,
        "device_connections_attempted": False,
        "motion_authority_checked": False,
    }


def stackchan_display_presets(config: EnsembleConfig) -> dict[str, dict[str, BehaviorCue]]:
    """Trusted display-only presets, independent of emotion and speech packs."""
    if not config.stackchan_display_presets_enabled:
        return {}
    return {
        config.stackchan.instance_id: {
            "normal": BehaviorCue(at_s=0.0, operation="face_scale", parameters={"percent": 100}),
            "enlarged": BehaviorCue(at_s=0.0, operation="face_scale", parameters={"percent": 170}),
        },
    }


def serve(config_path: str | Path) -> None:
    config = load_config(config_path)
    if isinstance(config, tuple):
        from .ensemble_v4 import (
            build_startup_gate,
            build_v4,
            start_owned_physical,
            start_owned_simulation,
        )
        room, robots, options = config
        owned_cleanup = None
        private_tokens = {}
        try:
            for ref in room.robots:
                broker = robots.get(ref.kind)
                if ref.kind == "soarm101" and broker.mode == "sdk_simulation_daemon":
                    (token, trusted_token), owned_cleanup = start_owned_simulation(broker)
                    private_tokens[broker.instance_id] = (token, trusted_token)
                elif ref.kind == "soarm101" and broker.mode == "sdk_physical_daemon":
                    (token, trusted_token), owned_cleanup = start_owned_physical(broker)
                    private_tokens[broker.instance_id] = (token, trusted_token)
            runtime, packs, connected, extras = build_v4(room, robots, private_tokens, options)
            ensemble = ConversationEnsemble(runtime, packs, presence_ttl_s=room.presence_ttl_s,
                                            stop_timeout_s=room.stop_timeout_s,
                                            motion_patterns=extras.motion_patterns)
            create_ensemble_server(runtime, ensemble, connected=connected, motion_enabled=True,
                                   startup_gate=build_startup_gate(room, robots, runtime, ensemble),
                                   stackchan_performer_id=next((r.id for r in room.robots if r.kind == "stackchan"), None),
                                   **extras.server_kwargs).run(transport="stdio")
        finally:
            if owned_cleanup is not None:
                owned_cleanup()
        return
    _check_original_idle_reference(config)
    if isinstance(config, ReachyOnlyEnsembleConfig):
        reachy = ReachySDKProvider(config.reachy)
        async def resolve_reachy(native: bool = False, smooth_idle: bool = False):
            style = "expressive" if native else config.reachy_expression_style
            return await reachy_pack_from_catalog(
                config.reachy.instance_id, reachy.catalog,
                neutral_head=json.loads(Path(config.reachy_neutral_head).read_text()),
                wobble_amplitude_rad=(
                    config.reachy_expressive_amplitude_rad
                    if style == "expressive"
                    else config.speaking_amplitude_rad
                ),
                speaking_axis=config.speaking_axis,
                listening_tilt_rad=config.listening_tilt_rad,
                listening_mode=config.listening_mode,
                expression_style=style,
                breath_amplitude_m=config.reachy_breath_amplitude_m,
                neutral_antennas_rad=config.reachy_neutral_antennas_rad,
                antenna_sway_rad=config.reachy_antenna_sway_rad,
                wake_up_if_sleeping=config.reachy_startup_behavior == "wake_up_if_sleeping",
                idle_gain=0.6 if smooth_idle else 1.0,
            )
        pack = anyio.run(resolve_reachy)
        profile = config.motion_profiles.get(config.reachy.instance_id)
        alternate = {}
        smooth_idle = {}
        original_idle = {}
        if profile and (profile.idle in {"native_v1", "smooth_v2"}
                        or profile.speaking in {"native_v1", "smooth_v2"}):
            alternate[config.reachy.instance_id] = (
                pack if config.reachy_expression_style == "expressive"
                else anyio.run(resolve_reachy, True)
            )
            if profile.idle == "smooth_v2":
                smooth_idle[config.reachy.instance_id] = anyio.run(
                    resolve_reachy, True, True,
                )
        if profile and profile.idle == "original_v1":
            original_idle[config.reachy.instance_id] = _original_idle_pack(pack)
        runtime = ExecutionRuntime(
            PerformanceStage(), journal_path=config.action_journal,
            ownership_path=config.ownership_db, stop_timeout=config.stop_timeout_s,
        )
        runtime.register(config.reachy.instance_id, reachy)
        original_speech = bool(profile and profile.speaking == "original_v1")
        ensemble = ConversationEnsemble(
            runtime, (pack,), presence_ttl_s=config.presence_ttl_s,
            stop_timeout_s=config.stop_timeout_s,
            motion_patterns=_profile_overrides(
                config.motion_profiles, alternate, smooth_idle, original_idle,
            ),
            continuous_presence_performers=(config.reachy.instance_id,) if original_speech else (),
        )
        speech_driven = config.speech_driven_performers
        speech_dynamics = {
            name: "smoothed_v1" for name, selection in config.motion_profiles.items()
            if selection.speaking == "smooth_v2"
        }
        create_ensemble_server(
            runtime, ensemble, connected=reachy.connected, motion_enabled=True,
            speech_driven_performers=speech_driven,
            speech_neutral_heads={config.reachy.instance_id: json.loads(
                Path(config.reachy_neutral_head).read_text()
            )} if speech_driven else {},
            speech_dynamics=speech_dynamics,
            speech_fallbacks={config.reachy.instance_id: pack.behaviors["speaking"]}
            if config.reachy_legacy_speech_fallback else {},
            native_gain_performers=(config.reachy.instance_id,) if original_speech else (),
            native_motion_performers=(config.reachy.instance_id,) if original_speech else (),
            stackchan_performer_id=None,
        ).run(transport="stdio")
        return
    # This is intentionally a deliberate, potentially physical operator action:
    # no local discovery, no automatic launch of the Conversation App.
    if config.schema_version == 3:
        reachy, _stackchan, runtime, connected = build_reachy_stackchan_ensemble(config)
        arm_pack = None
    else:
        reachy, _stackchan, _arm, arm_pack, runtime, connected = build_ensemble(config)
    async def resolve(native: bool = False, smooth_idle: bool = False):
        style = "expressive" if native else config.reachy_expression_style
        return await reachy_pack_from_catalog(
            config.reachy.instance_id, reachy.catalog,
            neutral_head=json.loads(Path(config.reachy_neutral_head).read_text()),
            wobble_amplitude_rad=(
                config.reachy_expressive_amplitude_rad
                if style == "expressive" else 0.025
            ),
            expression_style=style,
            breath_amplitude_m=config.reachy_breath_amplitude_m,
            neutral_antennas_rad=config.reachy_neutral_antennas_rad,
            antenna_sway_rad=config.reachy_antenna_sway_rad,
            wake_up_if_sleeping=config.reachy_startup_behavior == "wake_up_if_sleeping",
            idle_gain=0.6 if smooth_idle else 1.0,
        )
    # NOTE: Reachy neutral is operator supplied, no fabricated physical zero.
    reachy_pack = anyio.run(resolve)
    reachy_profile = config.motion_profiles.get(config.reachy.instance_id)
    alternate = {}
    smooth_idle = {}
    original_idle = {}
    if reachy_profile and (
        reachy_profile.idle in {"native_v1", "smooth_v2"}
        or reachy_profile.speaking in {"native_v1", "smooth_v2"}
    ):
        alternate[config.reachy.instance_id] = (
            reachy_pack if config.reachy_expression_style == "expressive"
            else anyio.run(resolve, True)
        )
        if reachy_profile.idle == "smooth_v2":
            smooth_idle[config.reachy.instance_id] = anyio.run(resolve, True, True)
    if reachy_profile and reachy_profile.idle == "original_v1":
        original_idle[config.reachy.instance_id] = _original_idle_pack(reachy_pack)
    recorded = ()
    if config.stackchan.asset_root:
        # Operator-selected native asset pack; the SDK validates and pins it.
        recorded = stackchan_recorded_assets(config.stackchan.asset_root)
    # Opt-in audio-driven wording; operator config, never a tool argument.
    speech_driven = tuple(getattr(config, "speech_driven_performers", ()) or ())
    speech_seeds = {
        name: int(getattr(config, "speech_seed", 7)) for name in speech_driven
    }
    speech_neutral = {}
    if config.reachy.instance_id in speech_driven:
        speech_neutral[config.reachy.instance_id] = json.loads(
            Path(config.reachy_neutral_head).read_text()
        )
    stack_pack = stackchan_conversation_pack(
        config.stackchan.instance_id,
        speaking_mode=config.stackchan_speaking_mode,
        recorded_assets=recorded,
    )
    stack_pack = _stackchan_startup_pack(stack_pack, config.stackchan_startup_behavior)
    stack_profile = config.motion_profiles.get(config.stackchan.instance_id)
    if stack_profile and (
        stack_profile.idle != "legacy" or stack_profile.speaking != "legacy"
    ):
        alternate[config.stackchan.instance_id] = (
            stack_pack if config.stackchan_speaking_mode == "native_weight"
            else stackchan_conversation_pack(
                config.stackchan.instance_id,
                speaking_mode="native_weight", recorded_assets=recorded,
            )
        )
    if stack_profile and stack_profile.idle == "smooth_v2":
        smooth_idle[config.stackchan.instance_id] = stackchan_conversation_pack(
            config.stackchan.instance_id,
            speaking_mode="native_weight", yaw_amplitude=40,
            recorded_assets=recorded,
        )
    motion_patterns = _profile_overrides(config.motion_profiles, alternate, smooth_idle, original_idle)
    speech_dynamics = {
        name: "smoothed_v1" for name, selection in config.motion_profiles.items()
        if selection.speaking == "smooth_v2"
    }
    packs = (reachy_pack, stack_pack) if arm_pack is None else (
        reachy_pack, stack_pack, arm_pack,
    )
    original_speech = bool(reachy_profile and reachy_profile.speaking == "original_v1")
    ensemble = ConversationEnsemble(
        runtime, packs,
        presence_ttl_s=config.presence_ttl_s,
        stop_timeout_s=config.stop_timeout_s,
        startup_settle_s=config.startup_settle_s,
        motion_patterns=motion_patterns,
        continuous_presence_performers=(config.reachy.instance_id,) if original_speech else (),
    )
    create_ensemble_server(
        runtime, ensemble, connected=connected,
        motion_enabled=True,
        speech_driven_performers=speech_driven,
        speech_neutral_heads=speech_neutral,
        speech_seeds=speech_seeds,
        speech_dynamics=speech_dynamics,
        speech_fallbacks={config.reachy.instance_id: reachy_pack.behaviors["speaking"]}
        if config.reachy_legacy_speech_fallback else {},
        native_gain_performers=(config.reachy.instance_id,) if original_speech else (),
        native_motion_performers=(config.reachy.instance_id,) if original_speech else (),
        stackchan_performer_id=config.stackchan.instance_id,
        startup_settle_s=config.startup_settle_s,
        presentation_presets=stackchan_display_presets(config),
    ).run(transport="stdio")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-puppeteer-ensemble")
    parser.add_argument("--config", required=True, help="trusted operator one-, two- or three-performer JSON config")
    parser.add_argument(
        "--check-config", action="store_true",
        help="validate config and print a readiness summary without connecting devices",
    )
    args = parser.parse_args(argv)
    try:
        if args.check_config:
            print(json.dumps(config_readiness(args.config), indent=2))
            return 0
        serve(args.config)
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
