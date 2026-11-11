"""Schema-v4 ensemble configuration: the ensemble file, one file per robot, SO-ARM broker config.

Robot descriptions are separate files so an ensemble can select any non-empty subset
without constructing an omitted device's SDK provider.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from ..mcp.launcher import ServerConfig
from .options import (
    check_reachy_options,
    parse_options,
)


class RobotReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["reachy", "stackchan", "soarm101"]
    id: str = Field(min_length=1)
    file: str = Field(min_length=1)


class LivePresenceConfig(BaseModel):
    """Breathing, listening and talking as a bounded live stream (SDK trusted-host route).

    ``envelope`` and ``envelope_revision`` pin the SDK ``JointEnvelope`` (centre pose and
    per-joint range) the operator reviewed. The motion below must fit inside it.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    envelope: str = Field(default="presence", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    envelope_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    breath_delta_deg: tuple[float, float, float, float, float]
    max_offset_deg: tuple[float, float, float, float, float]
    breath_period_s: float = Field(default=4.0, ge=1, le=8)
    talk_amplitude_deg: float = Field(default=5.0, gt=0, le=10)
    #: Follow the synthesized speech level (needs Conversationalist to send features).
    speech_driven: StrictBool = False

    @model_validator(mode="after")
    def fits_envelope(self):
        self.settings()
        return self

    def settings(self):
        import math

        from ..soarm_live import LivePresenceSettings
        if any(not math.isfinite(v) for v in (*self.breath_delta_deg, *self.max_offset_deg)) or any(
                not 0 <= v <= 15 for v in self.max_offset_deg):
            raise ValueError("live presence offsets must be finite and within 0..15 degrees")
        return LivePresenceSettings(
            self.envelope, self.envelope_revision,
            tuple(math.radians(v) for v in self.breath_delta_deg),
            tuple(math.radians(v) for v in self.max_offset_deg),
            breath_period_s=self.breath_period_s,
            talk_amplitude_rad=math.radians(self.talk_amplitude_deg))


class SOARMBrokerConfig(BaseModel):
    """SDK-owned simulation daemon or explicitly selected existing broker."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    instance_id: str = Field(min_length=1)
    broker_url: str = Field(min_length=1)
    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    trusted_host_token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    choreography_revisions: dict[str, str] = Field(default_factory=dict)
    pose_targets: dict[str, str] = Field(default_factory=dict)
    choreography_durations_s: dict[str, float] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(min_length=1)
    enable_joint_jog: bool = False
    enable_cartesian_jog: bool = False
    mode: Literal["existing_broker", "sdk_simulation_daemon", "sdk_physical_daemon"]
    simulation_state_dir: str | None = None
    simulation_port: int = Field(default=8765, ge=1, le=65535)
    trusted_choreography_catalog: str | None = None
    sdk_simulation_executable: str = "soarm101-sim-daemon"
    physical_daemon_config: str | None = None
    sdk_physical_executable: str = "soarm101-physical-daemon"
    physical_startup_timeout_s: float = Field(default=10, ge=1, le=60)
    startup_target_revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    # Name of a ``pose_targets`` entry used as the sleep move (e.g. "sleep2").
    sleep_target: str | None = None
    # Compare the pose with the standard sleep pose when the room starts.
    # Turn off only for a simulated broker, which has no physical sleep position.
    sleep_check: bool = True
    live_presence: LivePresenceConfig | None = None
    # How long a pinned gesture may keep running when the user talks over the robot, instead
    # of an immediate STOP (a STOP ends the SDK session and with it all later movement).
    # 0 restores the immediate STOP.
    finish_on_interrupt_s: float = Field(default=5.0, ge=0, le=5)

    @field_validator("choreography_durations_s", mode="before")
    @classmethod
    def finite_choreography_durations(cls, value):
        if not isinstance(value, dict) or any(
            type(duration) not in (int, float) or not math.isfinite(duration)
            or not 0.2 <= duration <= 120 for duration in value.values()
        ):
            raise ValueError("choreography durations must be finite values from 0.2 to 120 seconds")
        return value

    @model_validator(mode="after")
    def validate_binding(self):
        known = {"robot_health", "robot_capabilities", "robot_state", "list_assets",
                 "replay_recording", "replay_sequence", "capture_camera", "go_pose",
                 "jog_joint", "jog_cartesian", "move_gripper", "sleep", "sleep_up", "stop"}
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or set(self.allowed_tools) - known:
            raise ValueError("invalid SO-ARM broker tool profile")
        if "robot_capabilities" not in self.allowed_tools or "robot_state" not in self.allowed_tools or "stop" not in self.allowed_tools:
            raise ValueError("SO-ARM broker requires live state, capabilities and STOP grants")
        if self.enable_joint_jog != ("jog_joint" in self.allowed_tools):
            raise ValueError("joint jog opt-in must match the broker profile")
        if self.enable_cartesian_jog != ("jog_cartesian" in self.allowed_tools):
            raise ValueError("Cartesian jog opt-in must match the broker profile")
        parsed_url = urlsplit(self.broker_url)
        if (parsed_url.scheme != "http"
                or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed_url.port is None or parsed_url.username is not None
                or parsed_url.password is not None or parsed_url.path not in {"", "/"}
                or parsed_url.query or parsed_url.fragment):
            raise ValueError("SO-ARM broker URL must use explicit loopback HTTP")
        if self.mode == "sdk_simulation_daemon":
            if not self.simulation_state_dir or not Path(self.simulation_state_dir).is_absolute():
                raise ValueError("SDK simulation launch requires an absolute private state directory")
            if self.broker_url != f"http://127.0.0.1:{self.simulation_port}":
                raise ValueError("SDK simulation broker URL must match its configured loopback port")
        elif self.simulation_state_dir is not None:
            raise ValueError("simulation state directory is only valid for SDK-owned simulation")
        if self.mode == "sdk_physical_daemon":
            if not self.physical_daemon_config or not Path(self.physical_daemon_config).is_absolute():
                raise ValueError("SDK physical launch requires an absolute private daemon config path")
            if self.broker_url != f"http://127.0.0.1:{parsed_url.port}":
                raise ValueError("SDK physical broker URL must use canonical loopback address")
        elif self.physical_daemon_config is not None:
            raise ValueError("physical daemon config is only valid for SDK-owned physical mode")
        presence_names = {"idle", "listening", "speaking"}
        expression_names = {"yes", "no", "acknowledge", "neutral", "confused", "surprised", "curious"}
        if self.pose_targets and (not self.trusted_host_token_env or len(self.pose_targets) > 25
                or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                       or name in presence_names | {"wake_to_neutral"} | set(self.choreography_revisions)
                       or not re.fullmatch(r"sha256:[0-9a-f]{64}", revision)
                       for name, revision in self.pose_targets.items())):
            raise ValueError("pose targets require unique finite names, exact pins and trusted-host binding")
        if (self.choreography_revisions and
                (not self.trusted_host_token_env
                 or (not {"idle", "speaking"} <= set(self.choreography_revisions)
                     and self.live_presence is None)
                 or set(self.choreography_revisions) - presence_names - expression_names
                 or set(self.choreography_durations_s) != set(self.choreography_revisions)
                 or any(not 0.2 <= v <= 30 for v in self.choreography_durations_s.values())
                 or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                        for v in self.choreography_revisions.values()))):
            raise ValueError("trusted choreography requires exact revision pins, finite durations, and a separate token env")
        if self.sleep_target is not None and self.sleep_target not in self.pose_targets:
            raise ValueError("sleep_target must name one of the configured pose_targets")
        if self.live_presence is not None and (
                not self.trusted_host_token_env
                or self.mode not in {"sdk_simulation_daemon", "sdk_physical_daemon"}
                or not self.trusted_choreography_catalog):
            raise ValueError("live presence needs an SDK-owned daemon, its trusted catalog and a trusted-host token")
        if self.live_presence is not None and not self.choreography_revisions and not self.pose_targets:
            raise ValueError("live presence also needs the pinned wake and gesture programs")
        if self.startup_target_revision and not self.choreography_revisions:
            raise ValueError("startup target requires trusted choreography")
        if self.mode == "sdk_physical_daemon" and self.choreography_revisions and not self.startup_target_revision:
            # Sessions start at sleep (see startup_gate); the arm must wake to neutral
            # before the idle program, which begins at neutral, can run.
            raise ValueError("physical choreography requires a pinned startup_target_revision (wake to neutral)")
        if self.mode in {"sdk_simulation_daemon", "sdk_physical_daemon"} and (self.choreography_revisions or self.pose_targets) and (
                not self.trusted_choreography_catalog or not Path(self.trusted_choreography_catalog).is_absolute()):
            raise ValueError("SDK simulation choreography requires an absolute trusted catalog path")
        if self.trusted_choreography_catalog and self.mode not in {"sdk_simulation_daemon", "sdk_physical_daemon"}:
            raise ValueError("trusted catalog file is only valid for SDK-owned mode")
        if not self.choreography_revisions and self.choreography_durations_s:
            raise ValueError("choreography durations require exact revision pins")
        return self


class EnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[4]
    # True lets this room drive real robots (Reachy, StackChan, SO-ARM physical daemon).
    # False is for SDK-simulated SO-ARM rooms.
    physical: StrictBool = False
    robots: list[RobotReference] = Field(min_length=1, max_length=3)
    action_journal: str
    ownership_db: str
    reachy_neutral_head: str | None = None
    presence_ttl_s: float = Field(default=30, ge=1, le=3600)
    stop_timeout_s: float = Field(default=5, ge=.1, le=60)

    @model_validator(mode="after")
    def unique_and_valid(self):
        if len({r.kind for r in self.robots}) != len(self.robots):
            raise ValueError("v4 room robot kinds must be unique")
        if len({r.id for r in self.robots}) != len(self.robots):
            raise ValueError("v4 room performer IDs must be unique")
        for value in (self.action_journal, self.ownership_db):
            if not Path(value).is_absolute():
                raise ValueError("v4 journal and ownership paths must be absolute")
        if Path(self.action_journal).resolve() == Path(self.ownership_db).resolve():
            raise ValueError("v4 journal and ownership paths must be distinct")
        if not self.physical and any(r.kind in {"reachy", "stackchan"} for r in self.robots):
            raise ValueError("Reachy and StackChan are real robots: set physical: true on the room")
        if any(r.kind == "reachy" for r in self.robots):
            if not self.reachy_neutral_head or not Path(self.reachy_neutral_head).is_absolute():
                raise ValueError("v4 Reachy selection requires an absolute neutral pose file")
        elif self.reachy_neutral_head is not None:
            raise ValueError("Reachy neutral pose is only valid when Reachy is selected")
        return self


def load_config(path: str | Path) -> tuple[EnsembleConfig, dict[str, object], dict[str, object]]:
    """Returns ``(ensemble config, provider config by kind, behavior options by kind)``."""
    room_path = Path(path).resolve()
    raw = json.loads(room_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("schema_version") in (1, 2, 3):
        raise ValueError(
            "only schema version 4 is supported; convert older configs with "
            "scripts/migrate-room-config.py")
    room = EnsembleConfig.model_validate(raw)
    robots: dict[str, object] = {}
    options: dict[str, object] = {}
    for ref in room.robots:
        child = Path(ref.file)
        child = child if child.is_absolute() else room_path.parent / child
        data = json.loads(child.resolve().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("kind") != ref.kind or data.get("id") != ref.id:
            raise ValueError(f"v4 robot file identity mismatch for {ref.kind}:{ref.id}")
        if ref.kind in {"reachy", "stackchan"}:
            config_data = data.get("provider")
            if not isinstance(config_data, dict):
                raise ValueError("v4 SDK robot file requires provider object")
            if "physical_authority" in config_data:
                raise ValueError("physical_authority is no longer used; set physical: true on the room")
            authority = ("I_AUTHORIZE_REACHY_CONTROL" if ref.kind == "reachy"
                         else "I_AUTHORIZE_STACKCHAN_CONTROL")
            # The room's physical flag is the one grant; providers still take the
            # legacy marker string internally until they move into devices/.
            config = ServerConfig.model_validate({**config_data, "physical_authority": authority})
            expected = f"{ref.kind}_sdk"
            if config.device != ref.kind or config.backend != expected or config.instance_id != ref.id:
                raise ValueError(f"v4 {ref.kind} provider identity mismatch")
            required = ({"head_pose", "play_move", "device_stop"} if ref.kind == "reachy"
                        else {"head_move", "face_update", "device_stop"})
            if not required <= set(config.allowed_tools):
                raise ValueError(f"v4 {ref.kind} requires its core tool grants")
            options[ref.kind] = parse_options(ref.kind, data.get("options"))
            if ref.kind == "reachy":
                check_reachy_options(options["reachy"], config.allowed_tools,
                                     config.restricted_actions, room.reachy_neutral_head)
            robots[ref.kind] = config
        else:
            provider_data = dict(data.get("provider") or {})
            if provider_data.get("trusted_choreography_catalog"):
                catalog_path = Path(provider_data["trusted_choreography_catalog"])
                if not catalog_path.is_absolute():
                    provider_data["trusted_choreography_catalog"] = str((child.resolve().parent / catalog_path).resolve())
            if provider_data.get("physical_daemon_config"):
                daemon_config = Path(provider_data["physical_daemon_config"])
                if not daemon_config.is_absolute():
                    provider_data["physical_daemon_config"] = str((child.resolve().parent / daemon_config).resolve())
            config = SOARMBrokerConfig.model_validate(provider_data)
            if config.instance_id != ref.id:
                raise ValueError("v4 SO-ARM101 provider identity mismatch")
            if config.mode == "sdk_physical_daemon" and not room.physical:
                raise ValueError("an SDK physical daemon needs physical: true on the room")
            parse_options(ref.kind, data.get("options"))
            robots[ref.kind] = config
    return room, robots, options


