"""Operator-configured MCP process launcher.

The packaged launcher currently supports an explicit fake backend for deployment
and wire-protocol validation. It never infers or opens a hardware connection.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..execution import ActionRequest, Completion, ExecutionRuntime, StopResult
from ..robot_contracts import AssetDescriptor, ControlDescriptor, DeviceDescriptor
from ..runtime import PerformanceStage
from .server import create_server
from .soarm_guidance import SERVER_INSTRUCTIONS


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = "fake"
    device: str
    instance_id: str = Field(min_length=1)
    name: str = "Forge Puppeteer mock device"
    allowed_tools: list[str] = Field(min_length=1)
    assets: list[str] = Field(default_factory=lambda: ["example"])
    asset_root: str | None = None
    manifest: str | None = None
    # Optional durable action history for lookup and restart reconciliation.
    action_journal: str | None = None
    ownership_db: str | None = None
    # SO-ARM broker deployments are opt-in and require this exact operator
    # declaration. The downstream broker remains the actual authority gate.
    physical_authority: str | None = None
    simulation_only: bool = False
    # New canonical client: direct to the SDK-owned authenticated broker.
    # No bearer tokens are accepted in JSON; load them from trusted env.
    broker_url: str | None = None
    broker_token_env: str = "SOARM101_BROKER_TOKEN"
    # Legacy SDK-owned MCP subprocess retained ONLY until installed-wheel parity.
    broker_command: str | None = None
    broker_args: list[str] = Field(default_factory=list)
    broker_env: dict[str, str] = Field(default_factory=dict)
    # StackChan SDK wheels are installed by the operator until a compatible
    # release is published. Pin the installed distribution exactly in config.
    port: str | None = None
    sdk_version: str | None = None
    # Reachy SDK constructor options are explicit to avoid mDNS/network fallback
    # or daemon spawning. The operator selects localhost_only or a fixed network host.
    host: str | None = None
    connection_mode: str | None = None
    daemon_port: int | None = None


class FakeProvider:
    """Deterministic in-memory provider for explicit fake deployments."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.grants = frozenset(config.allowed_tools)
        self._assets = tuple(
            AssetDescriptor(
                id=f"{config.device}:{asset}", kind="behavior", source="operator-config",
                revision="sha256:forge-puppeteer-fake-v1",
            ) for asset in config.assets
        )
        self.actions: list[dict[str, Any]] = []

    async def permitted(self, tool: str) -> bool:
        return tool in self.grants

    async def describe(self) -> DeviceDescriptor:
        metadata_tools = {
            "device_info", "device_state", "list_assets", "action_lookup",
            "control_acquire", "control_release",
        }
        controls = tuple(
            ControlDescriptor(
                id=tool, description=f"Fake backend {tool}",
                effect=("stop" if tool in {"device_stop", "stop"} else
                        "light" if "light" in tool else
                        "visual" if "face" in tool else
                        "audio" if "speak" in tool else "motion"),
                parameters_schema={}, resources=(f"{self.config.device}.fake",),
            )
            for tool in sorted(self.grants - metadata_tools)
        )
        return DeviceDescriptor(
            id=self.config.instance_id, provider=f"fake-{self.config.device}",
            connected=True, ready=True,
            supported_operations=tuple(sorted(self.grants)),
            controls=controls,
            authority={
                "mode": "fake", "physical_authorization": "none",
                "mock_execution": "active",
            },
        )

    async def state(self) -> dict[str, object]:
        return {"backend": "fake", "actions_completed": len(self.actions)}

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        return self._assets

    async def validate(self, request: ActionRequest) -> None:
        if request.performer_id != self.config.instance_id:
            raise ValueError("performer mismatch")
        if request.operation not in {"head", "face", "lights", "play_asset", "play_move", "head_pose", "antenna_targets", "go_pose", "jog_cartesian", "sleep"}:
            raise ValueError("unsupported fake operation")
        if request.operation in {"play_asset", "play_move"}:
            asset = next((a for a in self._assets if a.id == request.asset_id), None)
            if asset is None or asset.revision != request.asset_revision:
                raise ValueError("unknown or stale fake asset")

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        return (f"{self.config.device}.fake",)

    async def execute(self, request: ActionRequest) -> Completion:
        self.actions.append(request.model_dump(mode="json"))
        return Completion("deterministic fake action completed")

    async def stop(self) -> StopResult:
        return StopResult(True, "fake provider idle")


class ReachyCatalogProvider:
    """Offline catalog discovery. Deliberately has no SDK client or daemon link."""

    def __init__(self, config: ServerConfig):
        if not config.asset_root or not config.manifest:
            raise ValueError("Reachy catalog backend requires asset_root and manifest")
        from ..devices.reachy import ReachyCatalog

        manifest = json.loads(Path(config.manifest).read_text(encoding="utf-8"))
        self.catalog = ReachyCatalog(Path(config.asset_root), manifest)
        self.config = config
        self.grants = frozenset(config.allowed_tools)

    async def permitted(self, tool: str) -> bool:
        return tool in self.grants and tool in {"device_info", "device_state", "list_assets", "action_lookup"}

    async def describe(self) -> DeviceDescriptor:
        return await self.catalog.describe()

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        return await self.catalog.list_assets()

    async def state(self) -> dict[str, object]:
        return {"backend": "reachy_catalog", "connected": False, "ready": False}

    async def validate(self, request: ActionRequest) -> None:
        raise PermissionError("Reachy catalog backend is discovery-only")

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        return ()

    async def execute(self, request: ActionRequest) -> Completion:
        raise PermissionError("Reachy catalog backend is discovery-only")

    async def stop(self) -> StopResult:
        return StopResult(False, "no daemon connection was created")


class ReachySDKProvider:
    """Operator-authorized Reachy SDK session; connection exists only in lifespan."""

    _TOOLS = frozenset({
        "device_info", "device_state", "list_assets", "action_lookup",
        "play_move", "head_pose", "antenna_targets", "action_cancel", "device_stop",
    })

    def __init__(self, config: ServerConfig, *, robot_factory=None, catalog_factory=None):
        if config.physical_authority != "I_AUTHORIZE_REACHY_CONTROL":
            raise ValueError("Reachy deployment requires explicit physical_authority declaration")
        if not config.asset_root or not config.manifest:
            raise ValueError("Reachy SDK backend requires asset_root and manifest")
        if not config.host or not config.daemon_port:
            raise ValueError("Reachy SDK backend requires explicit host and daemon_port")
        if config.connection_mode not in {"localhost_only", "network"}:
            raise ValueError("Reachy connection_mode must be localhost_only or network")
        if config.connection_mode == "localhost_only" and config.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("localhost_only requires a loopback host")
        if not config.sdk_version or config.sdk_version != "1.10.0rc5":
            raise ValueError("Reachy SDK backend requires sdk_version 1.10.0rc5")
        unknown = set(config.allowed_tools) - self._TOOLS
        if unknown:
            raise ValueError(f"unsupported Reachy allowed_tools: {sorted(unknown)}")
        if set(config.allowed_tools) & {"play_move", "head_pose", "antenna_targets", "action_cancel", "device_stop"} and config.physical_authority != "I_AUTHORIZE_REACHY_CONTROL":
            raise ValueError("Reachy motion tools require explicit physical authority")
        try:
            from importlib.metadata import version
            installed = version("reachy-mini")
        except Exception as exc:
            raise RuntimeError("Install forge-puppeteer[reachy] for the pinned SDK backend") from exc
        if installed != config.sdk_version:
            raise ValueError(f"installed reachy-mini {installed} does not match configured {config.sdk_version}")
        from ..devices.reachy import ReachyCatalog, ReachyPlaybackProvider

        manifest = json.loads(Path(config.manifest).read_text(encoding="utf-8"))
        self.catalog = (catalog_factory or ReachyCatalog)(Path(config.asset_root), manifest)
        self.config = config
        self.grants = frozenset(config.allowed_tools)
        self._provider_type = ReachyPlaybackProvider
        self._robot_factory = robot_factory
        self._adapter = None
        self._robot = None

    @asynccontextmanager
    async def connected(self):
        import asyncio

        if self._robot_factory is None:
            from reachy_mini import ReachyMini

            factory = ReachyMini
        else:
            factory = self._robot_factory
        # The pinned constructor connects immediately; never spawn a daemon and
        # never use auto/mDNS fallback. Create and destroy it on worker threads.
        robot = await asyncio.to_thread(factory, host=self.config.host,
            port=self.config.daemon_port, connection_mode=self.config.connection_mode,
            spawn_daemon=False, use_sim=False, media_backend="no_media")
        self._robot = robot
        try:
            # Adapter initialization is expected to be local-only, but keep the
            # just-opened SDK connection covered if it ever raises.
            self._adapter = self._provider_type(self.catalog, robot, motion_authorized=True)
            yield
        finally:
            try:
                await asyncio.to_thread(robot.__exit__, None, None, None)
            finally:
                self._adapter = None
                self._robot = None

    async def permitted(self, tool: str) -> bool:
        return tool in self.grants

    def __getattr__(self, name):
        adapter = self._adapter
        if adapter is None:
            raise RuntimeError("Reachy SDK session is not active")
        return getattr(adapter, name)


class BrokerProvider:
    """SO-ARM adapter delegates only to the SDK trusted broker, never motors."""

    def __init__(self, config: ServerConfig):
        if config.simulation_only:
            if config.physical_authority is not None:
                raise ValueError("simulation_only cannot declare physical_authority")
        elif config.physical_authority != "I_AUTHORIZE_BROKER_CONTROL":
            raise ValueError("SO-ARM deployment requires explicit physical_authority declaration")
        if config.simulation_only and "robot_state" not in config.allowed_tools:
            raise ValueError("simulation_only requires robot_state for broker mode verification")
        if bool(config.broker_command) == bool(config.broker_url):
            raise ValueError("configure exactly one SO-ARM broker transport")
        if config.broker_url and config.broker_env:
            raise ValueError("direct SO-ARM broker URL must not carry inline credentials")
        if not config.broker_token_env.isidentifier():
            raise ValueError("invalid broker token environment variable name")
        from ..devices.soarm101 import SOARM101Provider
        self.config = config
        self.grants = frozenset(config.allowed_tools)
        self._adapter_type = SOARM101Provider
        self._adapter = None

    @asynccontextmanager
    async def connected(self):
        if self.config.broker_url is not None:
            from ..devices.soarm101_broker_http import SOARM101BrokerHTTP

            # Token remains in the trusted Puppeteer process, not an agent
            # tool payload, operator JSON file or nested MCP subprocess.
            token = os.environ.get(self.config.broker_token_env, "")
            client = SOARM101BrokerHTTP(self.config.broker_url, token)
            self._adapter = self._adapter_type(
                self.config.instance_id, client,
                allowed_tools=self.grants,
                simulation_only=self.config.simulation_only,
            )
            try:
                yield
            finally:
                self._adapter = None
            return
        # Migration-only legacy source MCP; remove once the SDK OpenShell
        # and installed-wheel canary use the direct HTTP broker client.
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("Install forge-puppeteer[mcp] for broker delegation") from exc
        params = StdioServerParameters(command=self.config.broker_command,
            args=self.config.broker_args, env=self.config.broker_env or None)
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            self._adapter = self._adapter_type(self.config.instance_id, session,
                allowed_tools=self.grants, simulation_only=self.config.simulation_only)
            try:
                yield
            finally:
                self._adapter = None

    def __getattr__(self, name):
        adapter = self._adapter
        if adapter is None:
            raise RuntimeError("SO-ARM broker session is not active")
        return getattr(adapter, name)


class StackChanSDKProvider:
    """Operator-configured StackChan USB session over an installed SDK wheel."""

    _TOOLS = frozenset({
        "device_info", "device_state", "list_assets", "action_lookup",
        "head_move", "face_update", "lights_update", "play_asset",
        "action_cancel", "device_stop",
    })

    def __init__(self, config: ServerConfig, *, connector=None):
        if config.physical_authority != "I_AUTHORIZE_STACKCHAN_CONTROL":
            raise ValueError("StackChan deployment requires explicit physical_authority declaration")
        if not config.port:
            raise ValueError("StackChan SDK backend requires an explicit USB port")
        if not config.sdk_version:
            raise ValueError("StackChan SDK backend requires an exact installed sdk_version")
        unknown = set(config.allowed_tools) - self._TOOLS
        if unknown:
            raise ValueError(f"unsupported StackChan allowed_tools: {sorted(unknown)}")
        try:
            from importlib.metadata import version
            installed = version("stackchan-control")
        except Exception as exc:
            raise RuntimeError(
                "Install the operator-selected stackchan-control wheel in this environment"
            ) from exc
        if installed != config.sdk_version:
            raise ValueError(
                f"installed stackchan-control {installed} does not match configured {config.sdk_version}"
            )
        from ..devices.stackchan import StackChanProvider
        self.config = config
        self.grants = frozenset(config.allowed_tools)
        self._provider_type = StackChanProvider
        self._connector = connector
        self._adapter = None

    @asynccontextmanager
    async def connected(self):
        adapter = self._provider_type(
            self.config.instance_id, asset_directory=self.config.asset_root,
            connector=self._connector, allowed_tools=self.grants,
        )
        self._adapter = adapter
        try:
            await adapter.connect(self.config.port)
            yield
        finally:
            try:
                await adapter.close()
            finally:
                self._adapter = None

    def __getattr__(self, name):
        adapter = self._adapter
        if adapter is None:
            raise RuntimeError("StackChan SDK session is not active")
        return getattr(adapter, name)


def _soarm_startup_grants(config: ServerConfig) -> frozenset[str]:
    """Intersect operator configuration with the trusted broker's current profile.

    A later broker revocation must still be checked at each call. This
    startup snapshot is tool discovery, not motion authorization.
    """
    if config.backend != "soarm101_broker" or config.broker_url is None:
        return frozenset(config.allowed_tools)
    import anyio

    from ..devices.soarm101_broker_http import SOARM101BrokerHTTP

    client = SOARM101BrokerHTTP(
        config.broker_url, os.environ.get(config.broker_token_env, ""),
    )
    visible = frozenset(tool.name for tool in anyio.run(client.list_tools).tools)
    grants = frozenset(config.allowed_tools) & visible
    if config.simulation_only and "robot_state" not in grants:
        raise PermissionError("simulation broker state is not visible")
    return grants


def serve(config_path: str | Path) -> None:
    config = load_config(config_path)
    if config.backend not in {"fake", "reachy_catalog", "reachy_sdk", "soarm101_broker", "stackchan_sdk"}:
        raise ValueError("unsupported backend")
    tool_registrations = {
        "stackchan": "register_stackchan_tools",
        "reachy": "register_reachy_tools",
        "soarm101": "register_soarm101_tools",
    }
    if config.device not in tool_registrations:
        raise ValueError("device must be stackchan, reachy, or soarm101")
    provider, runtime = create_execution(config)
    module = __import__(f"forge_puppeteer.mcp.{config.device}", fromlist=[tool_registrations[config.device]])
    register = getattr(module, tool_registrations[config.device])
    effective_grants = _soarm_startup_grants(config)
    enabled_tools = effective_grants
    if config.backend in {"soarm101_broker", "stackchan_sdk", "reachy_sdk"}:
        # Config grants name broker operations. The facade's discovery/lifecycle
        # tools are enabled only when their corresponding broker capability is.
        mapping = {"robot_capabilities": "device_info", "robot_state": "device_state",
            "stop": "device_stop", "robot_capabilities_lookup": "action_lookup"}
        if config.backend in {"stackchan_sdk", "reachy_sdk"}:
            enabled_tools = frozenset(effective_grants)
        else:
            enabled_tools = frozenset(
                {"control_acquire", "control_release"}
                | {mapping[name] for name in effective_grants if name in mapping}
                | (set(effective_grants) & {
                    "list_assets", "robot_health", "robot_capabilities", "robot_state",
                    "capture_camera", "go_pose", "jog_joint", "jog_cartesian",
                    "move_gripper", "sleep", "sleep_up",
                })
                | (set(effective_grants) & {"replay_recording", "replay_sequence"})
                | ({"action_lookup"} if "robot_state" in effective_grants else set())
                | ({"action_cancel"} if "stop" in effective_grants else set())
            )
    create_server(
        runtime, provider, config.instance_id, register,
        name=config.name, enabled_tools=enabled_tools,
        instructions=SERVER_INSTRUCTIONS if config.device == "soarm101" else None,
    ).run(transport="stdio")


def load_config(config_path: str | Path) -> ServerConfig:
    """Load trusted operator configuration shared by MCP, Python, and CLI."""
    return ServerConfig.model_validate_json(Path(config_path).read_text())


def create_execution(config: ServerConfig):
    """Construct the single configured provider adapter and shared runtime."""
    if config.backend not in {"fake", "reachy_catalog", "reachy_sdk", "soarm101_broker", "stackchan_sdk"}:
        raise ValueError("unsupported backend")
    provider = (FakeProvider(config) if config.backend == "fake" else
                ReachyCatalogProvider(config) if config.backend == "reachy_catalog" else
                ReachySDKProvider(config) if config.backend == "reachy_sdk" else
                BrokerProvider(config) if config.backend == "soarm101_broker" else
                StackChanSDKProvider(config))
    runtime = ExecutionRuntime(PerformanceStage(), journal_path=config.action_journal,
        ownership_path=config.ownership_db)
    runtime.register(config.instance_id, provider)
    return provider, runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-puppeteer-mcp")
    parser.add_argument("--config", required=True, help="trusted operator JSON server configuration")
    args = parser.parse_args(argv)
    try:
        serve(args.config)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
