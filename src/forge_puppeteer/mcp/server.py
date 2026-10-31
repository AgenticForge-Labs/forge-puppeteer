"""Trusted-local stdio MCP facade over the shared execution runtime.

Providers explicitly register typed tools and must route actions through Gateway.
Permissions are checked on every call; provider validation remains authoritative
at submission and execution. Stdio is not an authentication boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from ..execution import (
    ActionRequest,
    AmbientBehaviorControl,
    ExecutionError,
    ExecutionRuntime,
)
from ..robot_contracts import AssetDescriptor, DeviceDescriptor

if TYPE_CHECKING:
    from mcp.server import MCPServer


class DiscoveryProvider(Protocol):
    async def describe(self) -> DeviceDescriptor: ...
    async def list_assets(self) -> tuple[AssetDescriptor, ...]: ...
    async def state(self) -> dict[str, object]: ...
    async def permitted(self, tool: str) -> bool: ...


class Gateway:
    """Explicit provider tool registrations call submit with a fixed operation."""

    def __init__(
        self, runtime: ExecutionRuntime, provider: DiscoveryProvider, performer_id: str
    ):
        self.runtime, self.provider, self.performer_id = runtime, provider, performer_id
        self.action_ids: set[str] = set()

    async def check(self, tool: str) -> None:
        from mcp.server.mcpserver.exceptions import ToolError

        try:
            allowed = await self.provider.permitted(tool)
        except Exception:  # noqa: BLE001
            raise ToolError("permission_check_failed") from None
        if allowed is not True:
            raise ToolError("permission_denied")

    async def submit(self, tool: str, request: ActionRequest) -> dict[str, object]:
        from mcp.server.mcpserver.exceptions import ToolError

        await self.check(tool)
        if request.performer_id != self.performer_id:
            raise ToolError("unknown_performer")
        try:
            record = await self.runtime.submit(request)
            self.action_ids.add(request.id)
            return record.model_dump(mode="json")
        except ExecutionError as exc:
            raise ToolError(exc.code) from None
        except Exception:  # noqa: BLE001
            raise ToolError("action_submission_failed") from None


RegisterTools = Callable[["MCPServer", Gateway], None]


def create_server(
    runtime: ExecutionRuntime,
    provider: DiscoveryProvider,
    performer_id: str,
    register_tools: RegisterTools,
    *,
    name: str = "Forge Puppeteer",
    enabled_tools: frozenset[str] | None = None,
) -> MCPServer:
    """Build a server. Register the provider with runtime before calling this.

    enabled_tools is an operator-pinned startup grant; each call also checks live
    provider permission. Provider registration must be explicit and typed, and
    must never expose caller-selected operations, paths, or method dispatch.
    """
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError as exc:
        raise RuntimeError("Install forge-puppeteer[mcp] to use MCP") from exc

    gateway = Gateway(runtime, provider, performer_id)

    @asynccontextmanager
    async def lifespan(_server):
        try:
            provider_lifecycle = getattr(provider, "connected", None)
            if provider_lifecycle is None:
                async with runtime.run():
                    yield {}
            else:
                async with provider_lifecycle(), runtime.run():
                    yield {}
        finally:
            # runtime.run() cancels and awaits action tasks first. Their
            # cancellation handlers persist unknown outcomes before SQLite closes.
            runtime.close()

    server = MCPServer(name, lifespan=lifespan)

    async def checked(tool: str, call: Callable[[], Awaitable[object]]) -> object:
        await gateway.check(tool)
        try:
            return await call()
        except ExecutionError as exc:
            raise ToolError(exc.code) from None
        except Exception:  # noqa: BLE001
            raise ToolError("provider_error") from None

    @server.tool()
    async def device_info() -> dict[str, object]:
        """Discover the current device and its controls; metadata grants no authority."""
        result = await checked("device_info", provider.describe)
        return result.model_dump(mode="json")

    @server.tool()
    async def device_state() -> dict[str, object]:
        """Read current provider state and feedback semantics."""
        return await checked("device_state", provider.state)

    @server.tool()
    async def list_assets(
        offset: int = 0, limit: int = 50, kind: str | None = None
    ) -> dict[str, object]:
        """List catalog assets with pinned revisions; listing does not authorize playback."""
        if offset < 0 or limit < 1 or limit > 100:
            raise ToolError("invalid_pagination")
        if kind is not None and kind not in {
            "pose",
            "recording",
            "sequence",
            "behavior",
        }:
            raise ToolError("invalid_asset_kind")
        assets = await checked("list_assets", provider.list_assets)
        filtered = sorted(
            (a for a in assets if kind is None or a.kind == kind), key=lambda a: a.id
        )
        page = filtered[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "assets": [a.model_dump(mode="json") for a in page],
            "next_offset": next_offset if next_offset < len(filtered) else None,
        }

    @server.tool()
    async def action_lookup(action_id: str) -> dict[str, object]:
        """Look up an action; unknown outcomes do not authorize retry."""
        await gateway.check("action_lookup")
        try:
            return runtime.lookup(action_id).model_dump(mode="json")
        except ExecutionError as exc:
            raise ToolError(exc.code) from None

    @server.tool()
    async def action_cancel(action_id: str) -> dict[str, object]:
        """Cancel by device-wide stop, which also affects sibling actions."""
        await gateway.check("action_cancel")
        if action_id not in gateway.action_ids:
            raise ToolError("unknown_action")
        result = await checked("action_cancel", lambda: runtime.cancel(action_id))
        return {"confirmed": result.confirmed, "evidence": result.evidence}

    @server.tool()
    async def device_stop() -> dict[str, object]:
        """Request independent device stop even while an action is running."""
        result = await checked("device_stop", lambda: runtime.stop(performer_id))
        return {"confirmed": result.confirmed, "evidence": result.evidence}

    @server.tool()
    async def control_acquire(caller_id: str, session_id: str) -> dict[str, bool]:
        """Claim exclusive performer control for a scripted take."""
        try:
            runtime.acquire_control(performer_id, caller_id, session_id)
        except ExecutionError as exc:
            raise ToolError(exc.code) from None
        return {"acquired": True}

    @server.tool()
    async def control_release(caller_id: str, session_id: str) -> dict[str, bool]:
        """Release scripted performer control after all actions have stopped."""
        try:
            runtime.release_control(performer_id, caller_id, session_id)
        except ExecutionError as exc:
            raise ToolError(exc.code) from None
        return {"released": True}

    @server.tool()
    async def ambient_behavior(control: AmbientBehaviorControl) -> dict[str, bool]:
        """Explicitly enable bounded local idle eye blinks, or disable them."""
        await gateway.check("ambient_behavior")
        try:
            await runtime.set_ambient_behavior(performer_id, control)
        except ExecutionError as exc:
            raise ToolError(exc.code) from None
        return {"configured": True, "enabled": control.enabled}

    register_tools(server, gateway)
    if enabled_tools is not None:
        # Startup allowlist is operator supplied, never derived from a caller payload.
        registered = {tool.name for tool in server._tool_manager.list_tools()}
        if enabled_tools - registered:
            raise ValueError("unknown enabled tool")
        # Ownership is part of the execution safety contract and must accompany
        # any enabled action surface.
        for tool in registered - enabled_tools - {"control_acquire", "control_release"}:
            server.remove_tool(tool)
    return server
