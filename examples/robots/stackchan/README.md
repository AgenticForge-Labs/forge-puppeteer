# StackChan behavior packs

## Standalone StackChan SDK + Puppeteer MCP

The SDK is independent and supplies native control through its
`stackchan-control` Python package. Forge Puppeteer supplies the
optional MCP interface for **any compatible agent**, without
AgenticForge Platform or Studio. See
[the implementation guide](../../../docs/STACKCHAN_NATIVE_MCP.md).

A non-motion configuration example is
[`readonly-mcp.example.json`](readonly-mcp.example.json).
The USB port is a marker; a trusted operator must choose an
actual port, install the **exact** SDK 0.3.0 wheel with its
serial extra, and deliberately start the MCP server. No motion
or firmware flash is authorized by this example.

Supported explicit MCP tools are native head/face/built-in
lights, one 37-pixel NeoHEX frame, pinned asset replay and
common device lifecycle actions. Operator grants and
firmware-advertised capabilities are intersected, and the SDK
rechecks all motion and STOP commands. Playback supports
head/face/built-in lights; NeoHEX is not currently a native
asset playback step.


`behavior-packs.json` is operator-authored configuration. Keep its path in the
trusted application configuration; expose only a pack ID and revision to a
script caller. The compiler rejects unknown IDs, stale file revisions, and
parameters that do not match the fixed typed StackChan head, face, and light
contracts.

```python
import hashlib
from forge_puppeteer.mcp.client import MCPClient, ServerConfig

manifest = "/etc/forge-puppeteer/stackchan/behavior-packs.json"  # operator setting
revision = "sha256:" + hashlib.sha256(open(manifest, "rb").read()).hexdigest()
servers = {"stackchan": ServerConfig(command="python", args=("-m", "my_stackchan_server"))}
client = MCPClient(servers, behavior_packs_path=manifest)
plan = client.compile_behavior_pack("greeting", revision, "take-2026-001", server="stackchan")
results = await client.execute(plan)
```

The caller supplies `greeting`, its expected revision, and a take ID. It cannot
choose a file path, server command, MCP tool, or arbitrary operation. Compiled
cue IDs and action IDs are stable for the take and pack cue index. The plan
manifest records the pack digest. Execution still performs live readiness,
authority, ownership, typed tool/resource, and any referenced asset revision
preflight before dispatch.
