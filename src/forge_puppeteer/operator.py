"""Explicit Python entry point for actions through configured provider adapters."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path

import anyio

from .execution import ActionRequest, ExecutionError
from .mcp.launcher import create_execution, load_config


async def execute_configured_action_async(config_path: str | Path, request: ActionRequest):
    """Run one action through the same configured adapter and runtime as MCP.

    Caller/session identity and operation grants must match trusted operator
    configuration. Acquiring control is explicit at the runtime boundary.
    """
    config = load_config(config_path)
    provider, runtime = create_execution(config)
    if request.performer_id != config.instance_id:
        runtime.close()
        raise ExecutionError("unknown_performer")
    if request.operation not in config.allowed_tools:
        runtime.close()
        raise ExecutionError("permission_denied")
    lifecycle = getattr(provider, "connected", None)
    try:
        async with AsyncExitStack() as stack:
            if lifecycle is not None:
                await stack.enter_async_context(lifecycle())
            async with runtime.run():
                runtime.acquire_control(request.performer_id, request.caller_id, request.session_id)
                try:
                    await runtime.submit(request)
                    return await runtime.wait(request.id)
                finally:
                    runtime.release_control(request.performer_id, request.caller_id, request.session_id)
    finally:
        runtime.close()


def execute_configured_action(config_path: str | Path, request: ActionRequest):
    """Synchronous convenience wrapper for scripts that do not own an event loop."""
    return anyio.run(execute_configured_action_async, config_path, request)
