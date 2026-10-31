"""Fake SO-ARM broker process used only by packaged launcher tests."""
from mcp.server import MCPServer

server = MCPServer("SO-ARM broker fixture")

@server.tool()
async def robot_capabilities():
    return {"authority": {"motion": "test-granted"}, "capabilities": ["go_pose"]}

@server.tool()
async def robot_state():
    return {"state": "ready"}

@server.tool()
async def list_assets(offset: int = 0, limit: int = 100):
    return {"items": [], "offset": offset, "next_offset": None}

@server.tool()
async def go_pose(name: str, speed_deg_s: float | None = None,
                  acceleration_deg_s2: float | None = None):
    return {"status": "completed", "completion_evidence": "fake broker completion"}

@server.tool()
async def jog_cartesian(frame: str, x_mm: float = 0, y_mm: float = 0, z_mm: float = 0,
                        speed_mm_s: float | None = None, acceleration_mm_s2: float | None = None):
    return {"status": "completed"}

@server.tool()
async def sleep():
    return {"status": "completed"}

@server.tool()
async def stop():
    return {"confirmed": True, "status": "stopped"}

if __name__ == "__main__":
    server.run(transport="stdio")
