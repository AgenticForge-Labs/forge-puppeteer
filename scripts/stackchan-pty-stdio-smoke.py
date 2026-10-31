"""Exercise the installed StackChan launcher through a fake serial PTY and MCP stdio."""

import json
import os
import pty
import select
import tempfile
import threading
import tty
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CAPABILITIES = [
    "avatar.update", "motion.update.position_only", "lights.update",
    "external_lights.update", "system.state", "system.stop",
]
STATE = {"native_angle_limits_tenths": {"yaw": [-500, 500], "pitch": [30, 870]}}


class FakeFirmware:
    def __init__(self, master_fd):
        self.master_fd = master_fd
        self.stop = threading.Event()
        self.commands = []
        self.events = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        pending = bytearray()
        while not self.stop.is_set():
            ready, _, _ = select.select([self.master_fd], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(self.master_fd, 4096)
            except OSError:
                return
            pending.extend(data)
            while b"\n" in pending:
                line, _, remainder = pending.partition(b"\n")
                pending = bytearray(remainder)
                if not line.startswith(b"@AF "):
                    continue
                command = json.loads(line[4:])
                self.commands.append(command)
                method = command["method"]
                result = None
                if method == "system.capabilities":
                    result = {"protocol_version": "1.0", "capabilities": CAPABILITIES}
                elif method == "system.state":
                    result = STATE
                # Exercise SDK event routing and terminal failure propagation,
                # rather than replying with a single synthetic completion.
                terminal = "failed" if method == "lights.update" else "completed"
                sequence = [
                    {"event": "accepted", "id": command["id"]},
                    {"event": "progress", "id": command["id"]},
                    {"event": terminal, "id": command["id"],
                     **({"detail": "fixture rejected lights"} if terminal == "failed" else {})},
                ]
                if result is not None:
                    sequence[-1]["result"] = result
                for event in sequence:
                    self.events.append((command["id"], event["event"]))
                    os.write(self.master_fd, b"@AF " + json.dumps(event).encode() + b"\n")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)


async def main():
    master_fd, slave_fd = pty.openpty()
    tty.setraw(slave_fd)
    port = os.ttyname(slave_fd)
    firmware = FakeFirmware(master_fd)
    try:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "stackchan.json"
            config_path.write_text(json.dumps({
                "backend": "stackchan_sdk",
                "device": "stackchan",
                "instance_id": "installed-wheel-stackchan",
                # This is a software test grant; only a local PTY is opened.
                "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
                "port": port,
                "sdk_version": "0.3.0",
                "allowed_tools": ["device_info", "device_state", "action_lookup",
                                  "face_update", "head_move", "lights_update",
                                  "neohex_update", "device_stop"],
            }))
            params = StdioServerParameters(
                command="forge-puppeteer-mcp", args=["--config", str(config_path)],
                env=os.environ.copy(),
            )
            async with (
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                available = {tool.name for tool in (await session.list_tools()).tools}
                assert {"device_info", "device_state", "face_update", "head_move",
                        "lights_update", "neohex_update", "device_stop"} <= available
                info = (await session.call_tool("device_info", {})).structured_content
                assert info["id"] == "installed-wheel-stackchan"
                assert info["connected"] and info["ready"]
                assert {"face", "head", "lights", "neohex"} <= set(info["supported_operations"])
                args = {
                    "action_id": "pty-face-update", "caller_id": "offline-smoke",
                    "session_id": "pty-smoke", "parameters": {"mouth": {"size": 4}},
                }
                accepted = await session.call_tool("face_update", args)
                assert not accepted.is_error, accepted
                async def submit_and_wait(tool, action_args, expected):
                    result = await session.call_tool(tool, action_args)
                    assert not result.is_error, result
                    with anyio.fail_after(5):
                        while True:
                            status = (await session.call_tool("action_lookup", {
                                "action_id": action_args["action_id"],
                            })).structured_content
                            if status["state"] in {"completed", "failed", "unknown"}:
                                break
                            await anyio.sleep(0.01)
                    assert status["state"] == expected, status
                    return status

                action = await submit_and_wait("face_update", args, "completed")
                assert "physical settling not measured" in action["completion_evidence"]
                await submit_and_wait("head_move", {
                    "action_id": "pty-head-move", "caller_id": "offline-smoke",
                    "session_id": "pty-smoke",
                    "parameters": {"yaw": {"angle": 100, "speed": 20}},
                }, "completed")
                await submit_and_wait("neohex_update", {
                    "action_id": "pty-neohex", "caller_id": "offline-smoke",
                    "session_id": "pty-smoke",
                    "parameters": {
                        "pixels": ["#00ff33"] * 37, "brightness": 8,
                    },
                }, "completed")
                failed = await submit_and_wait("lights_update", {
                    "action_id": "pty-lights-failure", "caller_id": "offline-smoke",
                    "session_id": "pty-smoke",
                    "parameters": {"left_color": "#112233", "left_duration": 0.1},
                }, "unknown")
                # A rejected terminal device event is conservatively unknown at
                # the runtime boundary; it must not be represented as success.
                assert failed["error"] == "execution_outcome_unknown", failed
                stopped = (await session.call_tool("device_stop", {})).structured_content
                assert stopped["confirmed"] is True, stopped
            methods = [command["method"] for command in firmware.commands]
            assert {"avatar.update", "motion.update", "lights.update",
                    "external_lights.update"} <= set(methods), methods
            assert "system.stop" in methods, methods
            for command in firmware.commands:
                if command["method"] in {"avatar.update", "motion.update", "lights.update", "external_lights.update",
                                      "system.stop"}:
                    ordered = [name for ident, name in firmware.events if ident == command["id"]]
                    terminal = "failed" if command["method"] == "lights.update" else "completed"
                    assert ordered == ["accepted", "progress", terminal], (command, ordered)
            print("StackChan SDK launcher PTY/MCP smoke passed; serial methods:", ", ".join(methods))
    finally:
        firmware.close()
        os.close(slave_fd)
        os.close(master_fd)


anyio.run(main)
