# Studios and forge-reachy handoff examples

These examples show the current ownership boundary around authored robot shots.
Studios remains the authored-shot caller. Forge Puppeteer owns typed execution,
MCP packaging, catalog checks and take results. `forge-reachy` remains the
integration/configuration layer around the stock Pollen SDK and Conversation
App; it does not acquire robot-control ownership from either upstream project.

## Offline, runnable Reachy-shaped MCP example

The offline fixture under [`examples/robots/reachy`](../examples/robots/reachy)
uses the packaged `forge-puppeteer-mcp` launcher with `backend: fake`. Its
operator config grants only discovery, ownership and `play_move`; it opens no
Reachy SDK, daemon, camera, network connection or hardware. The catalog pin is
`reachy:hello` at `sha256:forge-puppeteer-fake-v1`, and the behavior-pack file
itself is pinned to
`sha256:b00e4ba19de87212313a5b75845471694d0fdb2a460b90ec55c9021fc3399b0d`.
Changing the pack requires recalculating that file SHA-256 in the consuming
handoff.

Install and run it from a clean environment with the MCP extra:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
# From the forge-puppeteer repository root; pip builds and installs the package.
python -m pip install '.[mcp]'
python scripts/downstream-integration-smoke.py
```

For a fresh wheel install, run `bash scripts/offline-package-smoke.sh`; it builds
the wheel and installs it with its dependency closure into a temporary Python
3.12 environment before running this example. The `0.1.0` wheel used there is
locally built; no published release is assumed.

The smoke launches the installed console entry point, compiles the authored
pack, checks exact pack and asset revisions, runs actual MCP stdio preflight,
executes one deterministic fake action, and verifies a stale pack pin is
rejected. It prints the selected take ID and pins. To query only the same fake
catalog manually, run `forge-puppeteer-mcp --config
examples/robots/reachy/mock-mcp-server.json` and use any MCP stdio client. The
server config is trusted operator input; request data cannot choose a process,
filesystem path or MCP method.

This is wire/package evidence, not a Reachy simulation or hardware test. The
fake catalog revision is local to this fake profile and must never be presented
as a staged SDK asset revision.

## Studios authored-shot handoff

[`reachy-shot-handoff.json`](../examples/integrations/studios/reachy-shot-handoff.json)
shows a Studios-shaped `forge_puppeteer_request_v1` carrying production,
episode, shot and authored performance intent, with an explicit operator-owned
mapping to the behavior pack, performer instance and exact catalog pin. Keep
the mapping alongside the resolved shot/script reference; do not ask an LLM to
translate prose into device methods or infer a recording from shot text.

The legacy `forge_studios.puppeteer_bridge.dispatch` path remains available.
Studios also exposes an opt-in
`forge_studios.puppeteer_bridge.dispatch_behavior_pack` API that calls the
installed Forge Puppeteer `MCPClient` directly. The caller must provide the
operator-authored server configuration, behavior-pack manifest, pack ID, exact
pack SHA-256 revision, server name and take ID. Studios does not resolve pack
mappings from shot prose. Puppeteer remains the owner of pack compilation,
capability/readiness/authority preflight, ownership, tool dispatch and take
execution; Studios records the returned cue evidence as a candidate physical
take asset. The integration uses the same mock profile described above for
offline end-to-end checks. There is no automatic physical route from normal
episode execution, and hardware still requires explicit active authority and
broker approval.

Forge Studios keeps a downstream integration regression at
`tests/current/test_puppeteer_bridge.py::test_installed_puppeteer_wheel_stdio_handoff_and_stale_pin`.
Run it with the installed `forge-puppeteer[mcp]` wheel and
`forge-puppeteer-mcp` on `PATH`. The test writes its own fake server and pack
manifests, computes the exact pack SHA-256, executes one fake cue over stdio,
then verifies a stale pin is rejected before server launch. It does not require
or import this repository's source checkout.

## forge-reachy coexistence

Keep the stock Conversation App as the owner of realtime conversation, audio,
vision, native Reachy tools and motion dispatch. The Puppeteer MCP example is a
separate, explicit authored-performance handoff. An operator must select one
writer for a Reachy instance and stop/handoff the other before execution; do
not run Conversation App motion and Puppeteer motion concurrently.

For offline staged-catalog inspection, use the packaged `reachy_catalog`
backend documented in [Robot deployment](ROBOT_DEPLOYMENT.md): stage a verified
snapshot, configure its absolute `asset_root` and `manifest`, then allow only
`device_info`, `device_state`, `list_assets` and `action_lookup`. That backend
does not expose playback. An operator may copy an exact discovered
`reachy/<name>` asset ID and returned SHA-256 revision into an authored pack;
the pack file itself must also be repinned after editing. Runtime playback
requires the separately gated `reachy_sdk` backend, exact installed
`reachy-mini==1.10.0rc5`, explicit host/port/connection mode, and the launcher
authority declaration. That declaration is only a local configuration gate;
it is not attended hardware qualification or verified stop behavior.

The stock Conversation App does not currently load Puppeteer behavior packs
through its external profile/tool extension. Integrating such a route requires
a separately reviewed thin adapter and explicit lifecycle handoff; do not add a
second conversation format or replace the upstream launch path.
