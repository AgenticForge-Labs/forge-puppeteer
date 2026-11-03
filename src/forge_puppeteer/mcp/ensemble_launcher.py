"""Operator-configured standalone three-robot ensemble stdio launcher.

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

from ..conversation_ensemble import ConversationEnsemble
from ..conversation_native import reachy_pack_from_catalog, stackchan_conversation_pack
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
    programs: list[ReviewedMotion] = Field(min_length=7, max_length=25)
    approved_revisions: list[str] = Field(min_length=7, max_length=25)
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
        if len(set(self.approved_revisions)) != len(self.approved_revisions):
            raise ValueError("duplicate approved revision pins")
        if {p.revision for p in self.programs} != set(self.approved_revisions):
            raise ValueError("every reviewed SO-ARM program revision must be pinned, with no extras")
        return self


class EnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    motion_authority: Literal["I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"]
    reachy: ServerConfig
    stackchan: ServerConfig
    reachy_neutral_head: str
    soarm101: SOARMEnsembleConfig
    action_journal: str
    ownership_db: str
    presence_ttl_s: float = Field(default=30, ge=1, le=3600)
    stop_timeout_s: float = Field(default=5, ge=0.1, le=60)
    reachy_expression_style: Literal["classic", "expressive"] = "classic"
    reachy_startup_behavior: Literal["none", "wake_up_if_sleeping", "wake_up"] = "none"
    reachy_return_to_neutral_after_expression: bool = False
    reachy_neutral_antennas_rad: tuple[float, float] | None = None
    reachy_expressive_amplitude_rad: float = Field(default=0.09, ge=0, le=0.12)
    reachy_antenna_sway_rad: float = Field(default=0.18, ge=0, le=0.26)
    reachy_breath_amplitude_m: float = Field(default=0.0, ge=0, le=0.005)

    @model_validator(mode="after")
    def validate_ensemble(self):
        if self.reachy.backend != "reachy_sdk" or self.reachy.device != "reachy":
            raise ValueError("Reachy native SDK provider required")
        if self.stackchan.backend != "stackchan_sdk" or self.stackchan.device != "stackchan":
            raise ValueError("StackChan native SDK provider required")
        ids = (self.reachy.instance_id, self.stackchan.instance_id, self.soarm101.instance_id)
        if len(set(ids)) != 3:
            raise ValueError("three unique performer IDs required")
        for value in (self.action_journal, self.ownership_db, self.reachy_neutral_head):
            if not value or not Path(value).is_absolute():
                raise ValueError("action history, ownership and Reachy neutral pose require absolute paths")
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
        if ((self.reachy_startup_behavior in {"wake_up_if_sleeping", "wake_up"}
             or self.reachy_return_to_neutral_after_expression)
                and "wake_up" not in self.reachy.allowed_tools):
            raise ValueError("Reachy wake-up startup requires the explicit wake_up grant")
        if (self.reachy_return_to_neutral_after_expression
                and self.reachy_startup_behavior != "wake_up"):
            raise ValueError("Reachy automatic neutral return requires forced SDK startup capture")
        if (self.reachy_return_to_neutral_after_expression
                and "pose_and_antennas" not in self.reachy.allowed_tools):
            raise ValueError("Reachy automatic neutral return requires the explicit pose_and_antennas grant")
        if not stack_tools <= set(self.stackchan.allowed_tools):
            raise ValueError("native StackChan expression requires head, face and stop grants")
        if self.stackchan.physical_authority != "I_AUTHORIZE_STACKCHAN_CONTROL":
            raise ValueError("StackChan expression requires operator physical authorization")
        if (self.soarm101.mode == "attended_physical_trial"
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
    reachy_expression_style: Literal["classic", "expressive"] = "classic"
    reachy_startup_behavior: Literal["none", "wake_up_if_sleeping", "wake_up"] = "none"
    reachy_return_to_neutral_after_expression: bool = False
    reachy_neutral_antennas_rad: tuple[float, float] | None = None
    reachy_expressive_amplitude_rad: float = Field(default=0.09, ge=0, le=0.12)
    reachy_antenna_sway_rad: float = Field(default=0.18, ge=0, le=0.26)
    reachy_breath_amplitude_m: float = Field(default=0.0, ge=0, le=0.005)

    @model_validator(mode="after")
    def validate_reachy(self):
        if self.reachy.backend != "reachy_sdk" or self.reachy.device != "reachy":
            raise ValueError("Reachy native SDK provider required")
        if self.reachy.restricted_actions:
            raise ValueError("head expression requires Reachy's normal native SDK mode")
        _check_reachy_expression_authority(
            self.reachy_expression_style, self.reachy_neutral_antennas_rad,
            self.reachy.allowed_tools, self.reachy_antenna_sway_rad,
        )
        if not {"head_pose", "play_move", "device_stop"} <= set(self.reachy.allowed_tools):
            raise ValueError("native Reachy expression requires head, playback and stop grants")
        if ((self.reachy_startup_behavior in {"wake_up_if_sleeping", "wake_up"}
             or self.reachy_return_to_neutral_after_expression)
                and "wake_up" not in self.reachy.allowed_tools):
            raise ValueError("Reachy wake-up startup requires the explicit wake_up grant")
        if (self.reachy_return_to_neutral_after_expression
                and self.reachy_startup_behavior != "wake_up"):
            raise ValueError("Reachy automatic neutral return requires forced SDK startup capture")
        if (self.reachy_return_to_neutral_after_expression
                and "pose_and_antennas" not in self.reachy.allowed_tools):
            raise ValueError("Reachy automatic neutral return requires the explicit pose_and_antennas grant")
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
    )
    return programs, policy, pack


def build_ensemble(config: EnsembleConfig):
    """All three native providers share one action journal/ownership boundary."""
    from soarm101_motion.agent_control import AgentAuthorityStore
    from soarm101_motion.broker import AgentMotionRates
    from soarm101_motion.broker_sdk import SDKAgentExecutor
    from soarm101_motion.broker_watchdog import BrokerWatchdog
    from soarm101_motion.config import SOARM101Config

    reachy = ReachySDKProvider(config.reachy)
    stackchan = StackChanSDKProvider(config.stackchan)
    programs, policy, arm_pack = soarm_native_binding(config.soarm101)
    arm_options = config.soarm101
    physical = arm_options.mode == "attended_physical_trial"
    sdk_config = SOARM101Config(
        robot_id=arm_options.robot_id,
        port=arm_options.port if physical else None,
        auto_enable_torque=False,
        configure_motors_on_connect=False,
        disable_torque_on_disconnect=False,
    )
    rates = AgentMotionRates(
        joint_speed_deg_s=arm_options.max_joint_speed_deg_s,
        joint_acceleration_deg_s2=arm_options.max_joint_acceleration_deg_s2,
    ).validated(sdk_config)
    executor = SDKAgentExecutor(
        config=sdk_config, rates=rates,
        simulation=not physical, simulation_actions=not physical,
        physical_actions=physical,
        authority_store=AgentAuthorityStore(
            Path(arm_options.human_lease_path) if physical
            else Path(config.action_journal).with_suffix(".simulation-lease.json")
        ),
    )
    watchdog = BrokerWatchdog(
        timeout_s=arm_options.watchdog_timeout_s,
        validate=executor.validate_supervision,
        trip=lambda _reason: executor.execute("stop", {}),
    )
    executor.motion_guard = watchdog.check
    from ..devices.soarm101_choreography import SOARMChoreographyProvider
    arm = SOARMChoreographyProvider(executor, watchdog, policy, programs)

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
            try:
                # The host-owned SDK connection is created once, and must not
                # auto-enable physical torque or bypass human lease.
                await anyio.to_thread.run_sync(executor.execute, "state", {})
                watchdog.start()
                watchdog.heartbeat()
                await stack.enter_async_context(reachy.connected())
                await stack.enter_async_context(stackchan.connected())

                async def heartbeat_loop():
                    while True:
                        await anyio.sleep(arm_options.watchdog_timeout_s / 4)
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

    # reachy native pack is resolved from the pinned manifest/catalog; these
    # read-only bytes are checked before any hardware connection in serve().
    return reachy, stackchan, arm, arm_pack, runtime, connected


def load_config(path: str | Path) -> EnsembleConfig | ReachyOnlyEnsembleConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 2:
        return ReachyOnlyEnsembleConfig.model_validate(data)
    return EnsembleConfig.model_validate(data)


def serve(config_path: str | Path) -> None:
    config = load_config(config_path)
    if isinstance(config, ReachyOnlyEnsembleConfig):
        reachy = ReachySDKProvider(config.reachy)
        async def resolve_reachy():
            return await reachy_pack_from_catalog(
                config.reachy.instance_id, reachy.catalog,
                neutral_head=json.loads(Path(config.reachy_neutral_head).read_text()),
                wobble_amplitude_rad=(
                    config.reachy_expressive_amplitude_rad
                    if config.reachy_expression_style == "expressive"
                    else config.speaking_amplitude_rad
                ),
                speaking_axis=config.speaking_axis,
                listening_tilt_rad=config.listening_tilt_rad,
                listening_mode=config.listening_mode,
                expression_style=config.reachy_expression_style,
                breath_amplitude_m=config.reachy_breath_amplitude_m,
                neutral_antennas_rad=config.reachy_neutral_antennas_rad,
                antenna_sway_rad=config.reachy_antenna_sway_rad,
                wake_up_if_sleeping=config.reachy_startup_behavior == "wake_up_if_sleeping",
                wake_up_always=config.reachy_startup_behavior == "wake_up",
                return_to_neutral_after_expression=config.reachy_return_to_neutral_after_expression,
            )
        pack = anyio.run(resolve_reachy)
        runtime = ExecutionRuntime(
            PerformanceStage(), journal_path=config.action_journal,
            ownership_path=config.ownership_db, stop_timeout=config.stop_timeout_s,
        )
        runtime.register(config.reachy.instance_id, reachy)
        ensemble = ConversationEnsemble(
            runtime, (pack,), presence_ttl_s=config.presence_ttl_s,
            stop_timeout_s=config.stop_timeout_s,
        )
        create_ensemble_server(
            runtime, ensemble, connected=reachy.connected, motion_enabled=True,
        ).run(transport="stdio")
        return
    # This is intentionally a deliberate, potentially physical operator action:
    # no local discovery, no automatic launch of the Conversation App.
    reachy, _stackchan, _arm, arm_pack, runtime, connected = build_ensemble(config)
    async def resolve():
        return await reachy_pack_from_catalog(
            config.reachy.instance_id, reachy.catalog,
            neutral_head=json.loads(Path(config.reachy_neutral_head).read_text()),
            wobble_amplitude_rad=(
                config.reachy_expressive_amplitude_rad
                if config.reachy_expression_style == "expressive" else 0.025
            ),
            expression_style=config.reachy_expression_style,
            breath_amplitude_m=config.reachy_breath_amplitude_m,
            neutral_antennas_rad=config.reachy_neutral_antennas_rad,
            antenna_sway_rad=config.reachy_antenna_sway_rad,
            wake_up_if_sleeping=config.reachy_startup_behavior == "wake_up_if_sleeping",
            wake_up_always=config.reachy_startup_behavior == "wake_up",
            return_to_neutral_after_expression=config.reachy_return_to_neutral_after_expression,
        )
    # NOTE: Reachy neutral is operator supplied, no fabricated physical zero.
    reachy_pack = anyio.run(resolve)
    stack_pack = stackchan_conversation_pack(config.stackchan.instance_id)
    ensemble = ConversationEnsemble(
        runtime, (reachy_pack, stack_pack, arm_pack),
        presence_ttl_s=config.presence_ttl_s,
        stop_timeout_s=config.stop_timeout_s,
    )
    create_ensemble_server(
        runtime, ensemble, connected=connected,
        motion_enabled=True,
    ).run(transport="stdio")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-puppeteer-ensemble")
    parser.add_argument("--config", required=True, help="trusted operator three-robot JSON config")
    args = parser.parse_args(argv)
    try:
        serve(args.config)
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
