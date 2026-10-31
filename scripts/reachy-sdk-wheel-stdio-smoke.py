"""Exercise an installed MCP wheel with a fake Reachy SDK over stdio.

Run inside a fresh environment containing forge-puppeteer[mcp]. The SDK itself
is injected as a tiny fake package, so this never connects to a daemon/device.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FAKE_ROBOT = '''
class ReachyMini:
    def __init__(self, **kwargs):
        assert kwargs == {"host": "127.0.0.1", "port": 8000,
            "connection_mode": "localhost_only", "spawn_daemon": False,
            "use_sim": False, "media_backend": "no_media"}
        self.kwargs = kwargs
    def __exit__(self, *_args):
        from pathlib import Path
        Path(%r).write_text("closed")
    async def async_play_move(self, move, *, sound=False):
        return None
    def cancel_move(self):
        pass
'''
FAKE_MOVE = '''
class RecordedMove:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]
        self.sound_path = sound_path
'''


async def exercise() -> None:
    with tempfile.TemporaryDirectory(prefix="reachy-sdk-wheel-smoke-") as temp:
        root = Path(temp)
        fake = root / "fake"
        (fake / "reachy_mini/motion").mkdir(parents=True)
        closed = root / "closed"
        (fake / "reachy_mini/__init__.py").write_text(FAKE_ROBOT % str(closed))
        (fake / "reachy_mini/motion/__init__.py").write_text("")
        (fake / "reachy_mini/motion/recorded_move.py").write_text(FAKE_MOVE)
        dist = fake / "reachy_mini-1.10.0rc5.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: reachy-mini\nVersion: 1.10.0rc5\n"
        )

        assets = root / "assets"
        assets.mkdir()
        payload = json.dumps({"description": "smoke", "duration": 0.2}).encode()
        import hashlib

        (assets / "smoke.json").write_bytes(payload)
        manifest = {
            "version": 1,
            "dataset_revision": "wheel-smoke",
            "sdk_version": "1.10.0rc5",
            "source_url": "local fixture",
            "license": "fixture",
            "notices": "fixture",
            "files": {"smoke.json": hashlib.sha256(payload).hexdigest()},
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        config = root / "server.json"
        config.write_text(json.dumps({
            "backend": "reachy_sdk",
            "device": "reachy",
            "instance_id": "reachy-wheel-smoke",
            "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
            "sdk_version": "1.10.0rc5",
            "host": "127.0.0.1",
            "daemon_port": 8000,
            "connection_mode": "localhost_only",
            "asset_root": str(assets),
            "manifest": str(manifest_path),
            "allowed_tools": ["device_info", "device_state", "list_assets", "play_move"],
        }))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(fake)
        params = StdioServerParameters(
            command="forge-puppeteer-mcp", args=["--config", str(config)], env=env,
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"device_info", "list_assets", "play_move"} <= names
            assert "head_pose" not in names and "device_stop" not in names
            info = (await session.call_tool("device_info", {})).structured_content
            assert info["sdk_version"] == "1.10.0rc5"
            assets_response = (await session.call_tool("list_assets", {})).structured_content
            asset = assets_response["assets"][0]
            result = await session.call_tool("play_move", {
                "action_id": "wheel-smoke-action",
                "caller_id": "offline-smoke",
                "session_id": "wheel-smoke",
                "asset_id": asset["id"],
                "revision": asset["revision"],
            })
            assert not result.is_error, result
        assert closed.read_text() == "closed", "SDK lifecycle cleanup did not run"
        print("installed Reachy MCP wheel stdio lifecycle passed with injected fake SDK")


if __name__ == "__main__":
    anyio.run(exercise)
