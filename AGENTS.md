# Agent / Codex instructions

Forge Puppeteer is the public physical execution backend for AgenticForge. It owns Stage environment/capture and performer embodiment/control. It must also remain useful as a standalone live robot/show system.

Physical safety takes precedence over autonomy.

## Operating modes

### Manual / rehearsal mode — default

Use this mode while adding hardware, calibrating a camera/performer, validating a semantic action, rehearsing a shot, or whenever explicit autonomous hardware permission has not been granted.

Rules:

- Prefer mock/dry-run execution first.
- Inspect capabilities and calibration before commanding a device.
- Run one semantic action/take at a time while developing new behavior.
- Keep semantic commands separate from device coordinates/motor packets.
- Capture the requested semantic performance, resolved device action, timestamps, configuration/calibration references, result, and take lineage.
- Never infer that hardware is safe/available from configuration alone; adapters must report readiness.
- Fail closed on missing calibration, unavailable devices, unsafe ranges, unsupported capabilities, or ambiguous targets.
- Do not move/energize physical hardware solely to test higher-level plumbing when mocks can test it.
- A useful manual operation should become a reusable function + CLI/API + test before it is added to orchestration.

### Agentic mode

Use only when an upstream caller/user has explicitly authorized physical execution.

Rules:

- Agentic orchestration may sequence already validated semantic actions; it does not bypass adapter safety checks.
- Hardware permission is necessary but not sufficient: readiness, calibration, capability, resource locks, and safety checks must all pass.
- Keep bounded retries. Never repeatedly re-drive a failed physical motion without a safe retry policy.
- Stop and return a structured failure on collision/safety/calibration/device errors.
- Never silently substitute a different performer, motion, camera, or stage configuration if that changes creative or safety intent.
- Autonomous camera/performer coordination must use the same Stage/Performer primitives exercised manually.

## Stage vs Puppeteer boundary

**Stage** controls/observes the environment and capture system:

- cameras and recording,
- camera motion,
- lighting,
- sets/props/rigs,
- calibration,
- stage state and resource locks.

**Performers/Puppeteer** control embodied performers:

- semantic character actions,
- robot motion/performance,
- gestures/blocking,
- multi-character coordination,
- embodiment-specific execution.

Do not merge these abstractions merely because one production uses both.

## Semantic contract rule

Contracts exposed to Forge Studios should express intent such as `look_at`, `move_to_mark`, `speak`, `gesture`, `record_take`, or a semantic performance description. Vendor SDK calls, serial ports, servo units, raw PTZ values, and motor coordinates stay inside adapters.

## Manual -> agentic implementation rule

```text
manual rehearsal
→ stable semantic action
→ plain tested function
→ adapter capability + safety contract
→ CLI/API exposure
→ orchestration step
→ agentic policy authorization
```

Do not introduce an autonomous-only hardware path.

## Migration / legacy transfer

This repository supersedes physical functionality from `robo-puppeteer` and the Stage/camera/capture portions of `robo-studio`.

When transferring:

- preserve reusable action/event/resource abstractions, timeline coordination, plugin/embodiment interfaces, voice/speech support, camera calibration/capture/motion, production profiles, and SO-ARM adapters where useful;
- rename/consolidate concepts into Stage vs Performer rather than preserving old repo boundaries;
- retain external SDK packages such as `soarm101-motion-sdk` as adapters/dependencies rather than copying them unless there is a concrete reason;
- migrate tests with behavior;
- update the migration matrix before considering old repos superseded.

## Provenance

Every physical take should retain stable production/episode/scene/shot/take IDs when supplied, performer IDs, semantic request, device/config/calibration references, start/end times, success/failure, retake parent, selected state, and captured media references.

## Secrets and safety

Never commit credentials, Wi-Fi/device secrets, private network addresses, tokens, or local calibration secrets. Do not weaken safety limits for convenience or autonomous throughput.


## Shared conversational behavior implementation (2026-11-02)

- 4G is a conversation/persona name, **not** a Reachy, StackChan or SO-ARM
  hardware identity. The same conversational source may address all three.
  Do not encode persona prompts, voice models, Platform tools or company
  permissions inside device behavior packs.
- `forge.behavior.v1` and `ConversationEnsemble` are the PR 1 semantic
  authoring and orchestration foundation. Reuse the existing
  `ExecutionRuntime`, action IDs, journal, resource locks and provider
  permission checks; do not add a second servo or MCP execution authority.
- A procedural loop is a finite SDK-validated cue repeated only while the
  host session remains fresh. Never approximate untrusted agent jog limits
  by chaining tiny commands, or infer physical STOP from task cancellation.
- When command admission is denied by resource ownership, **never** send an
  automatic device STOP that could disrupt a manipulation controller.
  Only potentially dispatched work owned by this coordinator warrants
  its existing STOP/HOLD escalation path.
- The new foundation is software-only until the next PRs bind each robot's
  native SDK. Conversationalist remains an optional caller; standalone
  releases must not import it or AgenticForge Platform to run robots.


## Native Reachy/StackChan expressive packs (PR 2)

- `conversation_native.py` is the only owner of these named expressive
  behaviors. Keep motion definitions out of the native SDK libraries and do
  not copy the Reachy Conversation App's runtime/movement manager.
- The Reachy dataset importer uses the source-controlled official manifest,
  exact upstream commit and SHA-256 inventory. No auto-refresh and no
  replacement of an installed snapshot; do not run a download at normal
  robot startup. Associated recording audio is disabled by default.
- Reachy's neutral head pose is operator-provided and reviewed; the default
  vertical breathing amplitude is zero pending physical qualification. Do
  not infer safe world +Z or physical settling from SDK completion.
- StackChan's pose authoring uses tenths-of-degree native semantics and the
  same existing provider/SDK STOP-generation contract. Never put pitch zero
  into a native head pose. Live firmware limits and operator grants still
  override the authoring envelope.
- These are software-only packs until PR 4's reviewed ensemble launcher
  binds them to explicit SDK sessions. PR 5 adds Conversationalist as one
  optional caller; the Puppet SDK/runtime remains standalone.
