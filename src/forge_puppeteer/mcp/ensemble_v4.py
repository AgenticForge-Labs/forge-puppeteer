"""Opt-in, file-composed native room configuration (schema v4).

Robot descriptions are deliberately separate files so a room can select any
non-empty subset without constructing an omitted device's SDK provider.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import anyio
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from ..behavior_contract import PerformerBehaviors
from ..conversation_native import stackchan_conversation_pack
from ..execution import ExecutionRuntime
from ..runtime import PerformanceStage
from .launcher import ServerConfig


class RobotReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["reachy", "stackchan", "soarm101"]
    id: str = Field(min_length=1)
    file: str = Field(min_length=1)


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
    operator_established_neutral: StrictBool = False
    startup_target_revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    startup_authority: str | None = None

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
                 or not {"idle", "speaking"} <= set(self.choreography_revisions)
                 or set(self.choreography_revisions) - presence_names - expression_names
                 or set(self.choreography_durations_s) != set(self.choreography_revisions)
                 or any(not 0.2 <= v <= 30 for v in self.choreography_durations_s.values())
                 or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                        for v in self.choreography_revisions.values()))):
            raise ValueError("trusted choreography requires exact revision pins, finite durations, and a separate token env")
        if self.startup_target_revision:
            if not self.choreography_revisions:
                raise ValueError("startup target requires trusted choreography")
            if self.mode != "sdk_simulation_daemon" and self.startup_authority != "I_AUTHORIZE_SOARM101_WAKE_TO_NEUTRAL_STARTUP":
                raise ValueError("physical startup target requires explicit startup authorization")
            if self.mode == "sdk_simulation_daemon" and self.startup_authority is not None:
                raise ValueError("startup authority is only valid for physical startup")
        elif self.startup_authority is not None:
            raise ValueError("startup authority requires a target")
        if self.mode == "sdk_physical_daemon" and self.choreography_revisions and not (self.operator_established_neutral or self.startup_target_revision):
            raise ValueError("v4 physical choreography requires operator_established_neutral or pinned startup target")
        if self.mode in {"sdk_simulation_daemon", "sdk_physical_daemon"} and (self.choreography_revisions or self.pose_targets) and (
                not self.trusted_choreography_catalog or not Path(self.trusted_choreography_catalog).is_absolute()):
            raise ValueError("SDK simulation choreography requires an absolute trusted catalog path")
        if self.trusted_choreography_catalog and self.mode not in {"sdk_simulation_daemon", "sdk_physical_daemon"}:
            raise ValueError("trusted catalog file is only valid for SDK-owned mode")
        if not self.choreography_revisions and self.choreography_durations_s:
            raise ValueError("choreography durations require exact revision pins")
        return self


class RoomConfigV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[4]
    motion_authority: Literal["I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"]
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
        if any(r.kind == "reachy" for r in self.robots):
            if not self.reachy_neutral_head or not Path(self.reachy_neutral_head).is_absolute():
                raise ValueError("v4 Reachy selection requires an absolute neutral pose file")
        elif self.reachy_neutral_head is not None:
            raise ValueError("Reachy neutral pose is only valid when Reachy is selected")
        return self


def load_v4(path: str | Path) -> tuple[RoomConfigV4, dict[str, object]]:
    room_path = Path(path).resolve()
    room = RoomConfigV4.model_validate(json.loads(room_path.read_text(encoding="utf-8")))
    robots: dict[str, object] = {}
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
            config = ServerConfig.model_validate(config_data)
            expected = f"{ref.kind}_sdk"
            if config.device != ref.kind or config.backend != expected or config.instance_id != ref.id:
                raise ValueError(f"v4 {ref.kind} provider identity mismatch")
            required = ({"head_pose", "play_move", "device_stop"} if ref.kind == "reachy"
                        else {"head_move", "face_update", "device_stop"})
            authority = ("I_AUTHORIZE_REACHY_CONTROL" if ref.kind == "reachy"
                         else "I_AUTHORIZE_STACKCHAN_CONTROL")
            if not required <= set(config.allowed_tools) or config.physical_authority != authority:
                raise ValueError(f"v4 {ref.kind} requires operator authority and core grants")
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
            robots[ref.kind] = config
    return room, robots


def build_v4(room: RoomConfigV4, robots: dict[str, object], private_tokens: dict[str, tuple[str, str | None]] | None = None):
    """Construct only selected providers and packs; all actions use one runtime."""
    providers: dict[str, object] = {}
    packs: list[PerformerBehaviors] = []
    runtime = ExecutionRuntime(PerformanceStage(), journal_path=room.action_journal,
                               ownership_path=room.ownership_db,
                               stop_timeout=room.stop_timeout_s)
    connected_contexts = []
    private_tokens = private_tokens or {}
    reachy = None
    for ref in room.robots:
        config = robots[ref.kind]
        if ref.kind == "reachy":
            from .launcher import ReachySDKProvider
            reachy = ReachySDKProvider(config)
            providers[ref.id] = reachy
            connected_contexts.append(reachy.connected)
        elif ref.kind == "stackchan":
            from .launcher import StackChanSDKProvider
            provider = StackChanSDKProvider(config)
            providers[ref.id] = provider
            packs.append(stackchan_conversation_pack(ref.id))
            connected_contexts.append(provider.connected)
        else:
            from ..devices.soarm101 import SOARM101Provider
            from ..devices.soarm101_broker_http import SOARM101BrokerHTTP
            token = private_tokens.get(config.instance_id, (os.environ.get(config.token_env, ""), None))[0]
            session = SOARM101BrokerHTTP(config.broker_url, token)
            trusted = None
            if config.choreography_revisions or config.pose_targets:
                from ..devices.soarm101_trusted_http import (
                    SOARM101TrustedChoreographyHTTP,
                )
                trusted_token = private_tokens.get(config.instance_id, ("", os.environ.get(config.trusted_host_token_env or "", "")))[1] or ""
                trusted = SOARM101TrustedChoreographyHTTP(
                    config.broker_url, trusted_token, config.choreography_revisions,
                    durations_s=config.choreography_durations_s,
                    startup_target_revision=config.startup_target_revision,
                    pose_targets=config.pose_targets)
            provider = SOARM101Provider(config.instance_id, session,
                                        allowed_tools=frozenset(config.allowed_tools),
                                        trusted_choreography=trusted)
            pack = _soarm_broker_pack(config)
            arm_connected = _soarm_broker_connected(provider)
            providers[ref.id] = provider
            if pack is not None:
                packs.append(pack)
            connected_contexts.append(lambda f=arm_connected: f())
        runtime.register(ref.id, providers[ref.id])

    async def resolve_reachy_pack():
        if reachy is None:
            return

        import json as _json

        from ..conversation_native import reachy_pack_from_catalog
        packs.insert(0, await reachy_pack_from_catalog(
            reachy.config.instance_id, reachy.catalog,
            neutral_head=_json.loads(Path(room.reachy_neutral_head).read_text(encoding="utf-8"))))

    if reachy is not None:
        anyio.run(resolve_reachy_pack)

    @asynccontextmanager
    async def connected():
        async with AsyncExitStack() as connections:
            # The caller (serve_v4) owns SDK daemon startup and teardown. This
            # context only connects the authenticated Puppeteer client.
            for factory in connected_contexts:
                await connections.enter_async_context(factory())
            for ref in room.robots:
                config = robots[ref.kind]
                provider = providers[ref.id]
                trusted = getattr(provider, "trusted_choreography", None)
                if trusted is not None:
                    await trusted.validate_catalog(config.choreography_durations_s)
            yield

    return runtime, tuple(packs), connected


def start_owned_simulation(config: SOARMBrokerConfig) -> tuple[tuple[str, str | None], Callable[[], None]]:
    """Start the SDK's supervised simulation daemon and return its secret token.

    The SDK owns the broker, SDK session, watchdog and heartbeat. We only run
    its bounded lifecycle CLI and stop the daemon if this call created it.
    """
    if config.mode != "sdk_simulation_daemon":
        raise ValueError("selected robot file did not request SDK simulation ownership")
    state_dir = Path(config.simulation_state_dir or "").resolve()
    record = state_dir / "daemon.json"
    token_path = state_dir / "broker.token"
    host_token_path = state_dir / "trusted-host.token"
    if record.exists() or token_path.exists() or host_token_path.exists():
        raise RuntimeError("SDK simulation state already exists; refusing to claim or stop an unowned broker")
    command = [config.sdk_simulation_executable, "start",
               "--state-dir", str(state_dir), "--port", str(config.simulation_port)]
    if config.choreography_revisions or config.pose_targets:
        command.extend(["--trusted-choreography", str(Path(config.trusted_choreography_catalog).resolve())])
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("SDK simulation daemon startup failed or timed out") from None
    if result.returncode != 0 or not record.is_file() or not token_path.is_file():
        raise RuntimeError("SDK simulation daemon did not become ready")
    token = token_path.read_text(encoding="utf-8").strip()
    trusted_token = host_token_path.read_text(encoding="utf-8").strip() if (config.choreography_revisions or config.pose_targets) else None
    if not token or ((config.choreography_revisions or config.pose_targets) and not trusted_token):
        raise RuntimeError("SDK simulation daemon token is unavailable")

    def cleanup() -> None:
        try:
            subprocess.run([config.sdk_simulation_executable, "stop",
                            "--state-dir", str(state_dir)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=8, check=True)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("owned SDK simulation daemon cleanup failed") from None
    return (token, trusted_token), cleanup


def start_owned_physical(config: SOARMBrokerConfig) -> tuple[tuple[str, str | None], Callable[[], None]]:
    """Start exactly one SDK physical daemon child and authenticate its ready broker."""
    if config.mode != "sdk_physical_daemon":
        raise ValueError("selected robot file did not request SDK physical ownership")
    daemon_path = Path(config.physical_daemon_config or "")
    raw = daemon_path.read_bytes()
    daemon_digest = hashlib.sha256(raw).digest()
    daemon = json.loads(raw)
    required = {"physical", "robot_id", "port", "calibration_id", "lease_path",
                "operator_choreography_only",
                "trusted_catalog_path", "loopback_port", "state_dir"}
    if (not isinstance(daemon, dict) or not required <= set(daemon)
            or set(daemon) - required - {"establish_hold_on_startup", "joint_speed_deg_s", "joint_acceleration_deg_s2"}
            or daemon["physical"] is not True):
        raise ValueError("physical daemon config must use the SDK's exact physical schema")
    if type(daemon["operator_choreography_only"]) is not bool:
        raise ValueError("physical daemon operator_choreography_only must be boolean")
    if (type(daemon.get("establish_hold_on_startup", False)) is not bool
            or (daemon.get("establish_hold_on_startup") and not daemon["operator_choreography_only"])):
        raise ValueError("startup HOLD requires explicit operator-only configuration")
    for key in (("trusted_catalog_path", "state_dir") if daemon["operator_choreography_only"]
                else ("lease_path", "trusted_catalog_path", "state_dir")):
        if not isinstance(daemon[key], str) or not Path(daemon[key]).is_absolute():
            raise ValueError(f"physical daemon {key} must be absolute")
    if daemon["loopback_port"] != urlsplit(config.broker_url).port:
        raise ValueError("physical daemon loopback port does not match robot broker URL")
    if config.trusted_choreography_catalog and Path(config.trusted_choreography_catalog).resolve() != Path(daemon["trusted_catalog_path"]).resolve():
        raise ValueError("physical daemon catalog path does not match robot catalog")
    if not config.trusted_choreography_catalog:
        config = config.model_copy(update={"trusted_choreography_catalog": daemon["trusted_catalog_path"]})
    catalog_bytes = Path(daemon["trusted_catalog_path"]).read_bytes()
    catalog_doc = json.loads(catalog_bytes)
    policy = catalog_doc.get("policy") if isinstance(catalog_doc, dict) else None
    if (not isinstance(policy, dict) or policy.get("robot_id") != daemon["robot_id"]
            or policy.get("calibration_id") != daemon["calibration_id"]
            or policy.get("allow_physical_trial") is not True):
        raise ValueError("physical trusted catalog identity/calibration does not match daemon config")
    state_dir = Path(daemon["state_dir"])
    record = state_dir / "daemon.json"
    token_path, host_path = state_dir / "broker.token", state_dir / "trusted-host.token"
    if record.exists() or token_path.exists() or host_path.exists():
        raise RuntimeError("physical daemon state already exists; refusing to claim an unowned daemon")
    if hashlib.sha256(daemon_path.read_bytes()).digest() != daemon_digest:
        raise RuntimeError("physical daemon config changed before launch")
    # Preserve the SDK's actual admission/readiness error in a private local
    # log. Discarding stderr hides the difference between configuration,
    # calibration, torque-HOLD and device failures from the operator.
    startup_log = daemon_path.with_suffix(".startup.log")
    log_fd = os.open(startup_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(log_fd, 0o600)
        proc = subprocess.Popen([config.sdk_physical_executable, "--config", str(daemon_path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=log_fd, close_fds=True)
    finally:
        os.close(log_fd)
    owned = True
    try:
        deadline = time.monotonic() + config.physical_startup_timeout_s
        token = trusted = ""
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"SDK physical daemon exited before readiness; see {startup_log}")
            try:
                if not record.is_file() or not token_path.is_file() or not host_path.is_file():
                    time.sleep(.05); continue
                detail = json.loads(record.read_text(encoding="utf-8"))
                token = token_path.read_text(encoding="utf-8").strip()
                trusted = host_path.read_text(encoding="utf-8").strip()
                for secret_path in (token_path, host_path):
                    st = secret_path.stat()
                    if st.st_uid != os.getuid() or st.st_mode & 0o077:
                        raise RuntimeError("SDK physical daemon token file is not private to this user")
                if (detail.get("pid") != proc.pid or detail.get("physical") is not True
                        or detail.get("port") != daemon["loopback_port"]
                        or detail.get("robot_id") != daemon["robot_id"] or not token or not trusted):
                    raise RuntimeError("SDK physical daemon identity/readiness record mismatch")
                def get(path, bearer):
                    from ..devices.soarm101_broker_http import _NoRedirects
                    req = urllib.request.Request(config.broker_url + path,
                        headers={"Authorization": "Bearer " + bearer})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects()).open(req, timeout=1) as res:
                        return json.loads(res.read())
                health = get("/v1/health", token)
                caps = get("/v1/capabilities", token)
                cat = get("/v1/trusted/catalog", trusted)
                if not all(isinstance(x, dict) and x.get("ok") is True and isinstance(x.get("result"), dict)
                           for x in (health, caps, cat)):
                    time.sleep(.05); continue
                programs = cat["result"].get("programs")
                if not isinstance(programs, list):
                    raise TypeError("SDK physical daemon trusted catalog response is invalid")
                found = {p.get("name"): p for p in programs if isinstance(p, dict)}
                for name, revision in config.choreography_revisions.items():
                    if name not in found or found[name].get("revision") != revision:
                        raise RuntimeError("SDK physical daemon trusted catalog does not match pinned robot config")
                if hashlib.sha256(Path(daemon["trusted_catalog_path"]).read_bytes()).digest() != hashlib.sha256(catalog_bytes).digest():
                    raise RuntimeError("physical trusted catalog changed during daemon startup")
                if hashlib.sha256(daemon_path.read_bytes()).digest() != daemon_digest:
                    raise RuntimeError("physical daemon config changed during startup")
                break
            except (OSError, ValueError, KeyError):
                time.sleep(.05)
        else:
            raise RuntimeError("SDK physical daemon readiness timed out")
    except BaseException:
        if owned and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: raise RuntimeError("SDK physical daemon cleanup timed out; STOP outcome uncertain") from None
        raise
    def cleanup():
        if proc.poll() is not None:
            if proc.returncode != 0:
                raise RuntimeError("SDK physical daemon exited abnormally; STOP outcome uncertain")
            return
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            raise RuntimeError("SDK physical daemon did not stop within bound; STOP outcome uncertain") from None
        if code != 0:
            raise RuntimeError("SDK physical daemon reported shutdown failure; STOP outcome uncertain")
    return (token, trusted), cleanup


def _soarm_broker_pack(config: SOARMBrokerConfig) -> PerformerBehaviors:
    """Bind optional presence semantics to exact private-host catalog pins."""
    from ..behavior_contract import BehaviorCue, BehaviorPattern
    if config.choreography_revisions:
        patterns = {}
        for state in ("idle", "listening", "speaking"):
            name = state if state in config.choreography_revisions else "idle"
            cue = BehaviorCue(at_s=0, operation="play_choreography", parameters={},
                asset_id=f"soarm101/choreography/{name}",
                asset_revision=config.choreography_revisions[name])
            patterns[state] = BehaviorPattern(source="recorded", mode="loop", cues=(cue,),
                period_s=config.choreography_durations_s[name], max_cycles=120,
                max_duration_s=min(3600, config.choreography_durations_s[name] * 120))
        for expression in ("yes", "no", "acknowledge", "neutral", "confused", "surprised", "curious"):
            if expression not in config.choreography_revisions:
                continue
            cue = BehaviorCue(at_s=0, operation="play_choreography", parameters={},
                asset_id=f"soarm101/choreography/{expression}",
                asset_revision=config.choreography_revisions[expression])
            patterns[expression] = BehaviorPattern(
                source="recorded", mode="once", cues=(cue,),
                max_duration_s=config.choreography_durations_s[expression])
    else:
        hold = BehaviorPattern(source="procedural", mode="hold", cues=())
        patterns = {state: hold for state in ("idle", "listening", "speaking")}
    for name, revision in config.pose_targets.items():
        patterns[name] = BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(
            at_s=0, operation="play_joint_target", parameters={},
            asset_id=f"soarm101/target/{name}", asset_revision=revision),), max_duration_s=30)
    startup = None
    if config.startup_target_revision:
        startup = BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(
            at_s=0, operation="wake_to_neutral", parameters={},
            asset_id="soarm101/target/wake_to_neutral",
            asset_revision=config.startup_target_revision),), max_duration_s=30)
    return PerformerBehaviors.author(config.instance_id, patterns, startup=startup)


def _soarm_broker_connected(provider):
    @asynccontextmanager
    async def connected():
        # Discovery is read-only. Puppeteer never starts or reconnects the
        # physical broker; live broker state/profile remains authoritative.
        await provider.describe()
        await provider.state()
        yield
    return connected

