# StackChan behavior packs

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
