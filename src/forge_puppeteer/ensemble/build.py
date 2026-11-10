"""Build an ensemble from its config: providers, behavior packs, the startup gate and owned SDK daemons."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import anyio

from ..behavior_contract import PerformerBehaviors
from ..execution import ExecutionRuntime
from ..runtime import PerformanceStage
from .config import EnsembleConfig, SOARMBrokerConfig
from .options import (
    ReachyOptions,
    StackChanOptions,
    build_reachy_behavior,
    build_stackchan_pack,
)


@dataclass(frozen=True)
class EnsembleExtras:
    """What the ensemble and MCP server need beyond the packs."""
    #: ``{performer_id: {"idle": pack, "speaking": pack}}`` for ConversationEnsemble.
    motion_patterns: dict[str, dict[str, object]] = field(default_factory=dict)
    #: Keyword arguments for ``create_ensemble_server`` (speech-driven motion).
    server_kwargs: dict[str, object] = field(default_factory=dict)


def build_ensemble(room: EnsembleConfig, robots: dict[str, object],
             private_tokens: dict[str, tuple[str, str | None]] | None = None,
             options: dict[str, object] | None = None):
    """Construct only selected providers and packs; all actions use one runtime.

    Returns ``(runtime, packs, connected, extras)``.
    """
    options = options or {}
    providers: dict[str, object] = {}
    packs: list[PerformerBehaviors] = []
    runtime = ExecutionRuntime(PerformanceStage(), journal_path=room.action_journal,
                               ownership_path=room.ownership_db,
                               stop_timeout=room.stop_timeout_s)
    connected_contexts = []
    private_tokens = private_tokens or {}
    reachy = None
    live_presences: dict[str, object] = {}
    speech_driven: list[str] = []
    for ref in room.robots:
        config = robots[ref.kind]
        if ref.kind == "reachy":
            from ..mcp.launcher import ReachySDKProvider
            reachy = ReachySDKProvider(config)
            providers[ref.id] = reachy
            connected_contexts.append(reachy.connected)
        elif ref.kind == "stackchan":
            from ..mcp.launcher import StackChanSDKProvider
            provider = StackChanSDKProvider(config)
            providers[ref.id] = provider
            packs.append(build_stackchan_pack(ref.id, options.get("stackchan") or StackChanOptions()))
            connected_contexts.append(provider.connected)
        else:
            from ..devices.soarm101 import SOARM101Provider
            from ..devices.soarm101_broker_http import SOARM101BrokerHTTP
            token = private_tokens.get(config.instance_id, (os.environ.get(config.token_env, ""), None))[0]
            session = SOARM101BrokerHTTP(config.broker_url, token)
            trusted = None
            live_presence = None
            if config.live_presence is not None:
                from ..soarm_live import LivePresence
                live_presence = LivePresence(config.live_presence.settings())
                live_presences[ref.id] = live_presence
                if config.live_presence.speech_driven:
                    speech_driven.append(ref.id)
            if config.choreography_revisions or config.pose_targets or live_presence:
                from ..devices.soarm101_trusted_http import (
                    SOARM101TrustedChoreographyHTTP,
                )
                trusted_token = private_tokens.get(config.instance_id, ("", os.environ.get(config.trusted_host_token_env or "", "")))[1] or ""
                trusted = SOARM101TrustedChoreographyHTTP(
                    config.broker_url, trusted_token, config.choreography_revisions,
                    durations_s=config.choreography_durations_s,
                    startup_target_revision=config.startup_target_revision,
                    pose_targets=config.pose_targets, live_presence=live_presence)
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

    extras_motion: dict[str, dict[str, object]] = {}
    server_kwargs: dict[str, object] = {}
    reachy_options = options.get("reachy") or ReachyOptions()
    stackchan_options = options.get("stackchan") or StackChanOptions()

    async def resolve_reachy_pack():
        if reachy is None:
            return None
        neutral = json.loads(Path(room.reachy_neutral_head).read_text(encoding="utf-8"))
        behavior = await build_reachy_behavior(
            reachy.config.instance_id, reachy.catalog, reachy_options, neutral)
        packs.insert(0, behavior.pack)
        return behavior, neutral

    resolved = anyio.run(resolve_reachy_pack) if reachy is not None else None
    if resolved is not None:
        behavior, neutral = resolved
        reachy_id = reachy.config.instance_id
        if behavior.motion_patterns:
            extras_motion[reachy_id] = dict(behavior.motion_patterns)
        if reachy_options.speech_driven:
            speech_driven.append(reachy_id)
            server_kwargs["speech_neutral_heads"] = {reachy_id: neutral}
        if behavior.speech_dynamics:
            server_kwargs["speech_dynamics"] = {reachy_id: behavior.speech_dynamics}
        if behavior.speech_fallback is not None:
            server_kwargs["speech_fallbacks"] = {reachy_id: behavior.speech_fallback}
    for ref in room.robots:
        if ref.kind == "stackchan" and stackchan_options.speech_driven:
            speech_driven.append(ref.id)
    if speech_driven:
        server_kwargs["speech_driven_performers"] = tuple(speech_driven)
    if live_presences:
        server_kwargs["live_presences"] = live_presences

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

    return runtime, tuple(packs), connected, EnsembleExtras(extras_motion, server_kwargs)


def _daemon_process_alive(pid: int) -> bool:
    """True when ``pid`` exists and (on Linux) still looks like an SO-ARM daemon."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        return b"soarm101" in cmdline.read_bytes()
    except FileNotFoundError:
        return not Path("/proc").is_dir()  # non-Linux: trust os.kill
    except OSError:
        return True


def clear_stale_daemon_state(state_dir: Path, label: str) -> None:
    """Remove a daemon record/tokens left by a dead daemon; refuse if one is still running."""
    record = state_dir / "daemon.json"
    files = (record, state_dir / "broker.token", state_dir / "trusted-host.token")
    if not any(f.exists() for f in files):
        return
    pid = None
    try:
        pid = int(json.loads(record.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if pid is not None and _daemon_process_alive(pid):
        raise RuntimeError(f"{label} daemon is already running (pid {pid}); stop it before starting another controller")
    for f in files:
        f.unlink(missing_ok=True)


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
    clear_stale_daemon_state(state_dir, "SDK simulation")
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
    clear_stale_daemon_state(state_dir, "SDK physical")
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
    live = config.live_presence is not None
    if config.choreography_revisions:
        patterns = {}
        for state in ("idle", "listening", "speaking"):
            if live:
                # The live stream supplies the presence motion; the pack only lists the states.
                patterns[state] = BehaviorPattern(source="procedural", mode="hold", cues=())
                continue
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


def build_startup_gate(room: EnsembleConfig, robots: dict[str, object], runtime, ensemble):
    """Sleep-pose gate over the selected real robots (read-only until the user says move).

    SDK-simulated arms are skipped: a simulation has no physical sleep position.
    Returns None when nothing needs gating.
    """
    from ..behavior_contract import BehaviorCue
    from ..standard_poses import load_standard_poses
    from ..startup_gate import StartupGate

    readers, poses, cues = {}, {}, {}
    for ref in room.robots:
        config = robots[ref.kind]
        if getattr(config, "mode", None) == "sdk_simulation_daemon" or not getattr(config, "sleep_check", True):
            continue
        readers[ref.id] = runtime._provider(ref.id)
        poses[ref.id] = load_standard_poses(ref.kind)
        target = getattr(config, "sleep_target", None)
        if ref.kind == "soarm101" and target:
            cues[ref.id] = BehaviorCue(
                at_s=0, operation="play_joint_target", parameters={},
                asset_id=f"soarm101/target/{target}", asset_revision=config.pose_targets[target])
    return StartupGate(runtime, ensemble, readers, poses, cues) if readers else None


def _soarm_broker_connected(provider):
    @asynccontextmanager
    async def connected():
        # Discovery is read-only. Puppeteer never starts or reconnects the
        # physical broker; live broker state/profile remains authoritative.
        await provider.describe()
        await provider.state()
        yield
    return connected

