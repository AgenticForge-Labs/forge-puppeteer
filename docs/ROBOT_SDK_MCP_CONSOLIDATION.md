# Ordered robot MCP consolidation: SO-ARM101 first, StackChan second

**Status (2026-10-31):** Engineering plan. This file is a dependency
contract, not proof that the SDK-owned MCP is already removed or that
StackChan has been refactored. All three robot SDKs remain independently
usable without AgenticForge Platform or Studio. MCP-only Puppeteer
must itself work without either larger system.

## Architecture and authority

```text
Any MCP client
      |
Forge Puppeteer MCP-only or full performance mode
      |          |           |
  SO-ARM101   StackChan    Reachy Mini       (device-specific adapters)
      |          |           |
 SDK broker   Control SDK   upstream SDK      (independent authorities)
      |          |           |
  arm         firmware      robot
```

Puppeteer owns MCP tool registration, discovery/presentation, resources,
prompts, semantic intent and shared runtime coordination. Device SDKs
own native capabilities, validation, firmware protocol, hardware session
management, safety/fault/stop evidence and operator authority. Do **not**
invent a generic dynamic MCP proxy or expose every SDK Python method
as an agent tool. An operator-selected allowlist intersects with
live firmware/broker capabilities. Docker and OpenShell are deployment
choices; neither is required to use the SDK or the MCP server.

## Dependency A — finish SO-ARM101 adapter before StackChan changes

Use `soarm101-motion-sdk` main **after merged SDK PR #98**, which
reconciled the MCP-independent `BROKER_CAPABILITY_ROUTES` alias and
preserved PR #97's pinned asset catalog and simulation-only replay.
Puppeteer's existing SO-ARM adapter currently calls an SDK-owned
`soarm101-mcp` subprocess. Replace the intermediary with a typed,
authenticated broker client in Puppeteer. There must be **one**
Puppeteer MCP server and the existing trusted SDK broker, not MCP→MCP.
The SDK still controls profile grants, human authority, watchdog,
independent STOP, replay policy/calibration and physical session.

Parity acceptance is broader than merely converting `go_pose`:
- Profile-filtered discovery, health, capabilities, observed state,
  camera capture with verified SHA-256 and safe image metadata.
- Existing named pose/joint/cartesian/gripper/sleep/sleep-up commands,
  with positive/bounded typed rates, exact broker route behavior and
  no agent arming or calibration override.
- Pinned `list_assets`, `replay_recording`, `replay_sequence`
  only under SDK simulation, preserving request/session/revision IDs,
  replay refusal, STOP fencing and ambiguous-outcome quarantine.
- `soarm101://guidance/*` resources and `operate_robot_task`
  prompt as semantic hints, never policy authority.
- Same common `ExecutionRuntime` owner locks, action journals,
  cancellation and independent STOP, and a minimal MCP-only launcher.
- Test a clean installed SDK wheel + Puppeteer wheel, not just
  editable sibling checkouts. Run fake broker, simulated SDK and
  STDIO MCP acceptance; no unattended hardware.
- Preserve SDK's existing OpenShell client packaging until its
  replacement is accepted. Follow with a dependent SDK cleanup PR
  that removes SDK-owned `mcp_server.py`, `mcp_guidance.py`,
  client-only MCP staging, `[mcp]` extra and executable, without
  moving the SDK's trusted supervisor or broker.

**Gate:** No StackChan adapter refactor, firmware change, or
generalized MCP-schema generation before the SO-ARM101 Puppeteer
replacement is complete and accepted with explicitly recorded
remaining physical qualification limitations.

## Dependency B — StackChan Control SDK + Puppeteer adapter

Current `stackchan-control-sdk` main:
`f92499bf59d072d7b2c1e514dae737d26c2ebc3a` (inspect newer
main before implementation). Already has:
- `ExternalControl` with `available_controls()`, live firmware
  discovery, typed avatar, position-only head and built-in/NeoHEX
  lighting, conservative native angle/speed checks and STOP fencing.
- Static JSON schemas `stackchan-external-control-v1` in
  `capabilities.py`, intersected with firmware responses.
- `RoutedSerialClient` with one reader, per-command IDs,
  interleaved events and `UnknownOutcome` on lost transport.
- Immutable asset/revision checking and sequential host playback
  through the same validated operations; host timings and firmware
  completion do not establish independent physical position.
- Initial supervised USB hardware evidence of face, lights,
  yaw/pitch movement and active stop acknowledgement (2026-10-28),
  not yet host-loss/reconnect or measured positioning qualification.
- Puppeteer already has a `StackChanSDKProvider` and native MCP
  adapter; **reuse** those rather than creating a second MCP stack.

StackChan implementation, only after dependency A:

1. **Verify SDK/firmware contract.** Audit current main and the
   installed wheel/overlay version; explicitly pin firmware
   protocol `1.0`, current capability registry and hardware
   identity. No firmware flashing as part of MCP development.
2. **Common capability discovery.** Map `NativeCapability`
   IDs/schema/effect/resources to current Puppeteer
   `DeviceDescriptor`/`ControlDescriptor`; intersect the
   operator profile with fresh `available_controls()`, never
   promote static schema to effective authority. Use native
   pitch/yaw *tenths* internally, standard degrees only for
   reviewed Puppeteer semantic actions. Ensure deterministic
   typed tool registrations, no arbitrary raw serial commands.
3. **Control and outcome ownership.** Preserve the one-reader
   `RoutedSerialClient`, command IDs and serial ownership.
   Return accepted/unknown/completed/cancelled/failed distinctly;
   do not report physical motion complete merely because a
   firmware acknowledgement arrived. Keep SDK-side STOP
   inhibit/quarantine on failure; Puppeteer may not clear it.
4. **Host-loss/lifecycle.** Validate disconnect, USB port churn,
   program termination, restart and lease ownership. Introduce
   an SDK/firmware-owned timeout/watchdog only after reviewing
   existing hardware capabilities and testing attended; do
   not simulate safety-rated hardware STOP. Reconnect must not
   silently resume a lost motion or duplicate a submitted action.
5. **Assets and native media.** Reuse SDK asset revisions and
   validated `play_asset` for face/head/lights and NeoHEX,
   preserving partial-keyframe rejection and host schedule
   provenance. Keep voices/personality and choreography above
   the SDK in Puppeteer. Avoid new agent-triggered firmware
   update or raw WAV/USB playback tools.
6. **MCP-only and full mode.** Use the same Puppeteer server
   builder, `ExecutionRuntime`, ownership and journaling for
   one-robot MCP-only operation and multi-robot performances;
   the optional `[stackchan]` extra/installed wheel is
   sufficient, no Platform/Studio dependency.
7. **Acceptance.** Use memory/fake serial SDK tests,
   a simulated concurrent stop/host-loss failure matrix,
   real STDIO MCP requests, and clean installed wheels.
   Then provide a separate attended serial/firmware qualification
   checklist for the physical robot. Never auto-flash or
   auto-move hardware in CI/Codex.

**Intended deliverables:** one StackChan adapter update in Puppeteer
and only narrowly justified control SDK/firmware source changes,
with tests/docs. Extracting a standalone `forge-robot-mcp`
package is explicitly deferred until several adapters validate the
same interface; Intel's MCP generation templates are an optional
*build-time* comparison, not a runtime dependency.

## Dependency C — common multi-robot behavior

Only after both adapters pass their individual contract gates:
add shared `listening`, `speaking`, `idle`, `acknowledge`
behaviors across SO-ARM101, StackChan and Reachy. A performer may
intentionally decline or omit an expressive movement when busy.
Maintain deterministic resource arbitration and separate hardware
STOP outcomes. Archive original observable action inputs and
outcomes through Platform PR 7 when integrated; no independent
shadow trace store.

## Development and release status

No physical robot actuation, calibration, flashing, new
secrets or production deployment is authorized by this plan.
Keep a mostly linear sequence: finish the SO-ARM101 Puppeteer
PR → merge/accept → dependent SDK MCP cleanup → StackChan
Puppeteer/SDK implementation → multi-robot semantics.
Use locally tested exact SHAs for any hardware handoff.
