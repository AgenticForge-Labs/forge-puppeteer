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
