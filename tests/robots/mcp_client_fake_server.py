"""Configurable software-only MCP server for real stdio client tests."""

import json
import sys
from pathlib import Path

from mcp.server import MCPServer


def main():
    instance = sys.argv[1] if len(sys.argv) > 1 else "client-fake"
    authority_state = sys.argv[2] if len(sys.argv) > 2 else "granted"
    event_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    server = MCPServer(f"client fixture {instance}")
    owner = None
    pending_actions = {}

    def record(event):
        if event_path:
            with event_path.open("a") as stream:
                stream.write(json.dumps(event) + "\n")

    @server.tool()
    async def device_info():
        return {"id": instance, "connected": True, "ready": True,
                "authority": {"motion": authority_state},
                "controls": [{"id": "perform", "resources": ["arm"]},
                             {"id": "perform_async", "resources": ["arm"]},
                             {"id": "head_move", "resources": ["head"]},
                             {"id": "face_update", "resources": ["face"]},
                             {"id": "lights_update", "resources": ["lights"]}]}

    @server.tool()
    async def control_acquire(caller_id: str, session_id: str):
        nonlocal owner
        requested = (caller_id, session_id)
        record({"event": "acquire", "device": instance, "caller_id": caller_id,
                "session_id": session_id})
        if instance == "busy":
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("ownership_busy")
        if owner is not None and owner != requested:
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("ownership_busy")
        owner = requested
        return {"acquired": True}

    @server.tool()
    async def control_release(caller_id: str, session_id: str):
        nonlocal owner
        record({"event": "release", "device": instance, "caller_id": caller_id,
                "session_id": session_id})
        if owner != (caller_id, session_id):
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("not_owner")
        owner = None
        return {"released": True}

    @server.tool()
    async def list_assets(offset: int = 0, limit: int = 50):
        assets = [{"id": "fixture:pose", "revision": "sha256:one"}]
        page = assets[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "assets": page,
            "next_offset": next_offset if next_offset < len(assets) else None,
        }

    @server.tool()
    async def perform(action_id: str, asset_id: str, revision: str,
                      caller_id: str, session_id: str):
        if owner is not None and owner != (caller_id, session_id):
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("ownership_busy")
        record({"event": "perform", "device": instance, "action_id": action_id})
        if action_id == "fail":
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("fixture_failure")
        return {"action_id": action_id, "asset_id": asset_id, "revision": revision}

    @server.tool()
    async def perform_async(action_id: str, caller_id: str, session_id: str):
        pending_actions[action_id] = 0
        return {"id": action_id, "state": "submitted"}

    @server.tool()
    async def action_lookup(action_id: str):
        if action_id not in pending_actions:
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("unknown_action")
        pending_actions[action_id] += 1
        if action_id == "never":
            return {"id": action_id, "state": "running"}
        return {"id": action_id, "state": "completed", "completion_evidence": "fixture_done"}

    async def typed_action(action_id: str, caller_id: str, session_id: str,
                           parameters: dict, operation: str):
        if owner is not None and owner != (caller_id, session_id):
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("ownership_busy")
        record({"event": operation, "device": instance, "action_id": action_id,
                "parameters": parameters})
        return {"action_id": action_id, "parameters": parameters}

    @server.tool()
    async def head_move(action_id: str, caller_id: str, session_id: str, parameters: dict):
        return await typed_action(action_id, caller_id, session_id, parameters, "head_move")

    @server.tool()
    async def face_update(action_id: str, caller_id: str, session_id: str, parameters: dict):
        return await typed_action(action_id, caller_id, session_id, parameters, "face_update")

    @server.tool()
    async def lights_update(action_id: str, caller_id: str, session_id: str, parameters: dict):
        return await typed_action(action_id, caller_id, session_id, parameters, "lights_update")

    @server.tool()
    async def device_stop():
        record({"event": "stop", "device": instance})
        return {"confirmed": True, "evidence": "fixture stop"}

    @server.tool()
    async def conversational_perform(action_id: str):
        if owner is not None:
            from mcp.server.mcpserver.exceptions import ToolError
            raise ToolError("ownership_busy")
        record({"event": "conversational", "device": instance, "action_id": action_id})
        return {"accepted": True}

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
