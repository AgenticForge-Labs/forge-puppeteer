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

- **SDK public execution-generation contract:** the current SDK exposes
  `execution_state().generation` and accepts `expected_generation=`
  for guarded `update_face`, `move_head`, `update_lights`,
  `update_neohex` and pinned `play_asset`. The earlier private
  `_generation` method and legacy keyword remain compatibility details,
  not Puppeteer's public dependency. Native playback requires an exact
  `expected_revision`, and firmware events are not measured settling.
  Integration tests use the actual current SDK package.
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


## Shared semantic expression repertoire

The present StackChan behavior pack maps device-independent names to the
existing SDK-supported native `face` and `head` cues. In addition to the
core speaking/listening/idle/yes/no and happy/curious/confused behaviors,
Puppeteer now authors concerned, excited, surprised and sad. The native SDK
itself does **not** own emotion names or expose a stock emotion library; it
owns only the feature values, native units, firmware capability discovery,
serialization, STOP and physical device limits. The same semantic names can
be implemented differently for Reachy, SO-ARM, or a future performer.

The display-only 100%/170% face scale is deliberately **not** an emotion or
motion amplitude; it belongs to the EXT CONTROL firmware/SDK and is a separate
explicit operator choice. The prior enlarged 1.7× face is not required for
these expressions. Existing firmware remains compatible and lacks this
optional capability until the native SDK/firmware change is accepted.

These new authored performances are software-only until their actual appearance
and motion are observed and approved on a connected StackChan.


### Source-grounded stock modifier behavior

The pinned upstream `m5stack/StackChan` revision
`b72b3ede38b32d54f0b6ba51c62cfcef2ec3ae1e` contains
`modifiers/speaking.h`, `blink.h`, `idle_expression.h` and
`idle_motion.h`. The stock AI.AGENT app starts the Xiaozhi service;
it is not a host-facing catalog of named semantic emotions. Its speaking
modifier animates **mouth weight** rather than size, optionally adding
randomized head motions. Blink uses eye weight (closed weight 25) and
idle expression changes eye position, mouth position and rotation.

Puppeteer now includes deterministic, bounded native face-only
`blink`, `glance_left`, `glance_right` cues alongside the other
semantically named expression patterns. Its default existing speaking
loop still uses mouth size for backward compatibility. An operator may
explicitly choose `stackchan_speaking_mode: "native_weight"` in the
three-robot ensemble configuration. That chooses deterministic mouth
weight values at bounded cadence using only the existing SDK
`avatar.update` operation; it does **not** start the firmware's random
SpeakingModifier or another motion process. Eye baseline/appearance and
physical rendering must be attended-reviewed.

The optional audio-responsive `StackChanSpeechFacePreview` likewise
uses normalized mouth intensity to propose native mouth **weight**
updates, not a duplicate speaking engine. It remains preview-only.


## Optional display-scale capability (SDK 0.4.0; requires firmware support)

The native SDK 0.4.0 now supports `display.face_scale` for either normal
(100%) or enlarged (170%) LVGL face presentation. This is **not** an emotion,
speech-reactivity setting, motion envelope, or calibration change. Older 0.3.0
firmware neither advertises nor accepts this optional native command.

Puppeteer exposes `face_scale_update` only through its existing room-owned
MCP/action-journal/ExecutionRuntime path. Trusted operator configuration must
include the explicit `face_scale_update` tool grant, exact installed
`stackchan-control==0.4.0`, chosen USB port, and
`I_AUTHORIZE_STACKCHAN_CONTROL` physical-authority marker; merely having
version 0.4.0 installed does **not** grant display control. The provider
intersects that grant with connected firmware's `display.face_scale`
capability and the SDK's published native control registry. Older firmware
works with prior controls and simply lacks face-scale support.

Call the existing MCP tool with a stable, unique action ID and a strictly
validated `parameters: {"percent": 170}` to enlarge, or percent 100 to
restore. These are the only accepted percentages. Device discovery reports a
`face_scale` operation with visual effect and `face.display` resource.
The command is serialized against all other StackChan SDK operations and
passes the same STOP-generation snapshot used by the existing controls.
A completed action represents firmware command completion, not independent
visual verification; any unknown outcome must retain the normal action
journal state and **never be retried automatically**.

There is **no automatic startup change** and no resize from emotion, voice,
or speech-intensity signals. Stock 100% is the new firmware default until an
operator requests 170%. The legacy 0.3.0 firmware is left untouched. Attended
firmware installation and inspection of 100%/170% rendering, home/status UI,
and mouth clearing are outstanding.

Local software validation before attended hardware use:

```bash
cd ~/code/stackchan-control-sdk
python -m pytest tests/test_external_control.py tests/test_capability_schemas.py tests/test_cli.py -q

cd ~/code/forge-puppeteer
python -m pytest tests/robots/test_stackchan_provider.py tests/robots/test_stackchan_mcp.py tests/robots/test_packaged_launcher_stackchan.py -q
```


## Optional display presets inside the three-robot room

The standalone StackChan MCP gateway supports `face_scale_update` through
the SDK 0.4.0 operation. For a shared Conversationalist room, **do not run
another StackChan gateway against the same USB device**. The existing
`ConversationEnsemble` may expose `ensemble_presentation` with
`performer_id`, source-controlled `preset` and unique `action_id`.
The preset never accepts a raw percent, SDK method name or servo target.

This is disabled by default. A trusted three-robot ensemble config can set
`stackchan_display_presets_enabled: true` **only** when its nested StackChan
config pins `sdk_version: "0.4.0"` and explicitly lists
`face_scale_update` in `allowed_tools` with existing physical authority.
The only authored presets are `normal` (100%) and `enlarged` (170%), both
display-only. Neither is added to the emotion/expression catalog.

The room-owned scheduler preflights the provider before it yields the current
StackChan ambient cue. Missing SDK methods, old firmware, denied grants or
unavailable performers return a denied/unsupported result without issuing
an unrelated STOP. Successful admission follows the existing
`ExecutionRuntime`, action journal and native SDK generation fence, and
then resumes the latest presence behavior. Unknown outcomes keep the
same quarantine semantics as other actions. Reachy and SO-ARM are not
affected. No startup action is performed automatically.

Firmware completion does not establish visual acceptance of the scaled
LVGL face. Physical display testing and any installation remain explicitly
attended and deferred.
