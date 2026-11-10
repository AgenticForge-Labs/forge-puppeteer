# Agent instructions

Forge Puppeteer is the physical execution backend for AgenticForge. It owns
Stage (cameras, capture, lighting, calibration) and Performers (robot
embodiment and control). It must also work as a standalone live robot/show
system without Conversationalist or Platform.

## Working with the robots

These are small open-source desktop robots (SO-ARM101, Reachy Mini,
StackChan), not industrial machinery. Lean toward doing the real physical
work. Simulation is a quick sanity check, not a qualification process.

### Workflow

1. **Simulate once.** After changing motion or control code, run the relevant
   tests or mock/sim run **once**. If it passes, move on to hardware. Don't
   add more simulation rounds, review-only passes or "acceptance" reruns
   unless something failed or the user asks.
2. **Check the sleep position at session start.** Read the robot's current
   joint positions (read-only, no torque change) and compare them to its
   sleep pose below.
   - Every joint within tolerance (default **±5°**): proceed without asking.
   - Otherwise: report the per-joint deltas and ask the user.
3. **Do the physical work.** If the user asked for physical work in this
   session, that is the authorization. Don't re-ask for each step, action or
   restart, and don't ask for "physical qualification" first. Running it on
   the hardware *is* the test.
4. **End the session at sleep.** Move back to the sleep pose and confirm the
   position the same way before disconnecting.

### Standard poses (every robot)

Every robot defines `sleep` (parked start/end) and `neutral` (awake, ready).
Defaults are in `src/forge_puppeteer/poses/<kind>.yaml`; per-unit overrides go
in `~/.config/forge-puppeteer/standard_poses/<kind>.yaml`. Use
`forge_puppeteer.standard_poses.pose_check` for the comparison. If a robot
only reports commanded positions, or a pose isn't recorded yet, ask the user
once per session.

| Robot | sleep | neutral | feedback |
|---|---|---|---|
| SO-ARM101 | taught `sleep2`, shoulder_pan = 0 | taught `neutral_pup` | measured |
| Reachy Mini | SDK sleep constants | end of daemon wake-up (SDK init pose, antennas ±10°) | measured |
| StackChan | TBD | TBD | commanded only |

### Hard rules (the only ones)

- **One controller per robot.** Never run two controllers or MCP servers
  against the same device at once.
- **Faults stop the run.** On an SDK fault, collision, failed or uncertain
  STOP, or unexpected position: stop, report the actual error and ask. Don't
  retry the same motion in a loop and don't auto-rearm.
- **No bypassing.** Don't get around a fault or denial with raw serial, direct
  motor calls or a different controller.
- **No secrets in git.** Never commit credentials, Wi-Fi/device secrets,
  tokens or private network addresses.

### Not required

- Repeated permission prompts within a session.
- Multiple simulation passes for code that already passed tests.
- Hardware "qualification" or attended-acceptance gates before trying
  something on hardware.
- Reading `docs/ROBOT_NOTES.md` as a list of blockers. It is design
  background.

## Architecture

- **Stage vs Performer.** Stage handles cameras, recording, lighting, rigs,
  calibration and stage locks. Performers handle robot motion, gestures and
  multi-character coordination. Keep them separate.
- **Semantic contracts.** Exposed APIs use intents (`look_at`, `gesture`,
  `move_to_mark`, `record_take`). Vendor SDK calls, serial ports, servo units
  and motor coordinates stay inside device adapters.
- **One execution path.** Motion goes through the existing `ExecutionRuntime`
  and each robot's native SDK/broker. Don't add a parallel dispatcher or a
  direct-servo path. Manual and agentic use share the same primitives.
- **Standalone.** Puppeteer must not require Conversationalist or the
  AgenticForge Platform to run robots. Those are optional callers.
- **Provenance.** Physical takes keep production/scene/shot/take IDs when
  supplied, performer IDs, the semantic request, config/calibration refs,
  start/end times, the result and media references.
- **Reusable work.** A useful manual operation should become a tested
  function plus CLI/API before it goes into orchestration.

## SO-ARM101 notes

- The live demo is started through the configured Puppeteer MCP launcher
  (`mcp/ensemble_launcher.py`, v4 configs in `examples/robots/soarm101/`). It
  owns the SDK daemon and the single device connection.
- Named taught poses live in the SDK pose store
  (`~/.config/soarm101/poses/so101.json`). Expose them via `pose_targets`.
- The interactive `soarm101` MCP tools are fine for direct jog/pose/camera
  requests. Just don't run them at the same time as the ensemble launcher.

## Migration

This repo supersedes the physical parts of `robo-puppeteer` and the
Stage/capture parts of `robo-studio`. Keep external SDKs such as
`soarm101-motion-sdk` as dependencies, not copies. Migrate tests along with
behavior, and update `docs/MIGRATION.md` before calling an old repo
superseded.

Robot-specific design history (Reachy expressive packs, StackChan face scale,
ensemble schema versions, speech-reactive expression) is in
`docs/ROBOT_NOTES.md`.

## Planning and roadmap

Plans, roadmaps and status live in **GitHub issues** and the AgenticForge
Project board ("AgenticForge Roadmap",
https://github.com/users/AgenticForge-Labs/projects/1), not in markdown files.
The current roadmap is epic #54. New issues go on the board
(`gh project item-add 1 --owner AgenticForge-Labs --url <issue-url>`); move
the card's Status (Todo → In Progress → Done) as work progresses.

- **Before starting work,** find its issue (`gh issue list`, `gh issue view N`).
  If there is none, create one with a checkbox task list.
- **While working,** tick checkboxes in the issue as items land
  (`gh issue edit N --body-file …`). Add new findings as issue comments.
- **PRs** reference their issue with `Fixes #N` so merging closes it.
- **Don't create** `PLAN`, `STATUS`, `ROADMAP`, handoff or per-PR markdown files.
  `docs/` is for durable reference only (architecture, setup, how to run).
  History is git plus `CHANGELOG.md`.
- **Design discussions** go in the issue body (e.g. #53), not a new doc.
