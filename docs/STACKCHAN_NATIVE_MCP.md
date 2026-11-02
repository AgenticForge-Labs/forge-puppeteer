# StackChan Control SDK → Forge Puppeteer MCP

**Implementation state (2026-11-02):** The native StackChan MCP provider
is merged into Puppeteer's `main` through PR #7. PR #12 adds its
personality-neutral conversation behavior packs (talk/listen/idle/yes/no)
using the same provider and installed StackChan SDK. Software CI does not
qualify connected firmware, real USB motion, physical STOP or settling.
Local installed-wheel, PTY and supervised hardware acceptance remain
outstanding, and will be bundled into the planned final Codex handoff.

## Product boundary

A user installs `stackchan-control` for native Python, CLI, asset
playback and firmware access. It does **not** need Puppeteer, Platform
or Studio. To use StackChan from an agent, add
`forge-puppeteer[mcp]` and select the trusted `stackchan_sdk` backend.
No separate StackChan MCP proxy, duplicated hardware controller,
dynamic Python-method registration or platform gateway is needed.

```text
Any MCP agent (Hermes, Codex, etc.)
             |
Forge Puppeteer MCP / typed action records / ownership
             |
Existing StackChanProvider (one SDK facade and serial connection)
             |
stackchan-control ExternalControl + RoutedSerialClient
             |
operator-selected ESP32-S3 external-control firmware, protocol 1.0
```

## Implemented in the merged native MCP provider (PR #7)

- **Actual SDK API reconciliation:** native `ExternalControl._generation()`
  returns generation and stop event. Existing `update_face`,
  `move_head` and `update_lights` accept `_generation=`, not
  `expected_generation=`. Native `play_asset` requires pinned
  `expected_revision` and uses its own generation fence; Puppeteer
  no longer passes a nonexistent parameter to it. Do not mistake
  fake-adapter compatibility for a working installed SDK integration.
- **Operator grants on every surface:** direct Python provider
  `validate` and `execute` now enforce the same pinned tool
  allowlist used by MCP. Read-only inspection may run without a
  motion declaration; head, face, light, NeoHEX, playback, STOP
  and cancellation require explicit trusted physical-control
  authorization. SDK/firmware validation independently applies.
- **Schema matching:** effective control discovery is the intersection
  of installed SDK `CAPABILITIES`, currently reported firmware
  capabilities and selected operator tools. Puppeteer publishes the
  *adapter-facing* schemas (`yaw`/`pitch`, not firmware
  `yawServo`/`pitchServo`) but native effects/resources remain
  sourced from the SDK. Pydantic models encode native
  tenths-of-a-degree bounds and paired face x/y restrictions.
- **NeoHEX MCP:** `neohex_update` accepts exactly 37 validated
  `#RRGGBB` colors and integer brightness 0–32, converted into
  SDK `NeoHexFrame`. Uses `ExternalControl.update_neohex`,
  never raw serial commands, arbitrarily selected firmware
  methods, continuous rotation or firmware updates.
- **Assets:** reviewed immutable native pose/sequence revisions,
  current SDK `play_asset`, no in-place generated-asset repairs.
  Native SDK assets currently support head, face and built-in
  lights; NeoHEX is a separate native control, **not** an
  arbitrary step type added to the asset parser here.
- **Stop/outcomes:** existing SDK `RoutedSerialClient` owns
  one reader, command IDs and terminal events. Puppeteer retains
  its shared action journal, performer ownership and unknown
  outcome quarantine. Native completed means **firmware command
  completion, never measured physical settling**. A failed STOP
  remains unconfirmed and must not clear the runtime fence.
- **Lifecycle:** no automatic port discovery, reconnect,
  firmware flash, motion retry, or second controller. The
  existing provider refuses to close while worker threads
  remain active, even after caller cancellation.

## Safe operator start

A non-motion example is
`examples/robots/stackchan/readonly-mcp.example.json`.
It is intentionally *not* a live configuration: the USB port
is a `REPLACE_WITH_OPERATOR_CHOSEN_PORT` marker. Only a trusted
operator selects an actually present device and its exact
`stackchan-control==0.3.0` installed version.

```bash
# Both packages are independently installed from trusted wheels.
# Install stackchan-control[serial]==0.3.0 from its reviewed SDK checkout.
# Install forge-puppeteer[mcp] from its reviewed Puppeteer checkout.
forge-puppeteer-mcp \
  --config /absolute/private/path/to/reviewed-stackchan-readonly.json
```

Only `device_info`, `device_state`, `list_assets` and
`action_lookup` are selected in that example. Merely
discovering a tool is never physical authority. Do not
silently switch `physical_authority` on: the operator must
review the scope, firmware capabilities and actual robot,
then opt in explicitly to any face/light/head/STOP/playback
capabilities. Docker and OpenShell are optional; neither is
a requirement of either SDK or Puppeteer MCP.

## Acceptance checklist for local Codex (no physical robot)

Repositories:
- `AgenticForge-Labs/forge-puppeteer`, merge PR #7 for MCP provider and
  PR #12 for native conversation pack authoring (use exact merged SHAs
  when making the final local Codex handoff).
- `AgenticForge-Labs/stackchan-control-sdk` verified `main`
  `391ccb9b5ff777fbd88df6a97e2477161a34c7ec` (SDK v0.3.0).
- SO-ARM101 deterministic choreography is a **different** SDK PR 3,
  not a prerequisite to running standalone StackChan tests.

```bash
cd ~/code/stackchan-control-sdk
python -m pytest tests/test_external_control.py \
  tests/test_playback.py tests/test_routed_serial.py \
  tests/test_capability_schemas.py -q

cd ~/code/forge-puppeteer
uv run --extra mcp --extra dev python -m pytest \
  tests/robots/test_stackchan_provider.py \
  tests/robots/test_stackchan_mcp.py \
  tests/robots/test_packaged_launcher_stackchan.py -q
uv run --extra mcp --extra dev python -m pytest tests/robots -q
uv run --extra mcp --extra dev ruff check .
bash scripts/offline-package-smoke.sh
git diff --check
```

Also run `scripts/stackchan-pty-stdio-smoke.py` against the
isolated emulator; test face, lights, NeoHEX, bounded head movement
and STOP, partial writes, timeouts, unknown results, stale
asset revision, missing firmware capability, cancellation during
preflight, simultaneous callers, and no unapproved operations.
Do not run automated trials against an actual USB device or flash
firmware. Capture all exact SHAs, installed wheel metadata,
commands, test counts/failures and any unqualified cases.

## Explicit follow-up limitations

The SDK now exposes a public `execution_state()` generation
snapshot and optional `expected_generation` parameters for guarded
native operations; the Puppeteer provider uses those instead of a
private `_generation` call. This strengthens preflight fencing but
is **not** proof of physical STOP, servo settling or safety-rated
shutdown across firmware outages and power loss.

Firmware host-loss/reconnect/watchdog, servo settling
measurement, physical STOP latency and true exclusive device
ownership across independent host processes remain **unqualified**.
No automatic reconnect or physical-test instruction is added.
Address those in the owning SDK/firmware only, followed by
attended, explicitly approved hardware validation. A firmware
command-completed event must never be described as a
measured physical stop.
