# Robot design notes (background)

These notes record design decisions and integration history for the robot
adapters. They were moved out of `AGENTS.md` on 2026-11-10. They describe how
the code is structured and why. They are **not** extra gates on physical
testing. The working rules for agents are in `AGENTS.md`.

## SO-ARM MCP interfaces and authorized application startup

The interactive `soarm101` MCP server attached to a coding session and
Puppeteer's application-owned ensemble MCP server are different interfaces.
Do not require the interactive server to be attached before starting an
authorized Conversationalist -> Puppeteer ensemble -> SDK session.

When the user requests this application/demo, start its configured Puppeteer
MCP launcher through Conversationalist's existing client or a stdio MCP client.
The launcher may own the configured SDK daemon and its one device connection.
Using normal process commands for that supported lifecycle is permitted; it
is not a fallback to direct shell-driven robot motion. Inspect readiness
through this application-owned SDK/broker path before dispatching semantic
actions. Preserve all live calibration, ownership, rate, watchdog and STOP
checks. Operator-only choreography mode does not require an agent lease.
An explicit operator grant may also select the SDK daemon's
`establish_hold_on_startup` option. That option latches measured positions and
enables torque through the existing SDK once, under its watchdog, before
advertising readiness or starting the pinned wake target. It is not agent
arming, an implicit reconnect, or permission to retry a failed HOLD.

For direct interactive camera/pose/jog requests, prefer the connected
`soarm101` tools when available. Their absence does not disable the separate
application-owned ensemble path. A denial or fault from either path must
never be bypassed through another controller, raw serial access, direct motor
calls or a different movement. Do not run two controllers against the arm.

An existing explicit user instruction to run the demo authorizes its bounded
configured semantic routines; do not repeatedly request the same permission.
Review-only generators still must not manufacture authorization. A runtime
readiness or motion failure must report the actual SDK/broker reason and stop.

## Company authority and current qualification focus (2026-11-04)

Puppeteer owns the semantic/Stage physical performance interface but is **not**
the company Executive or operational WorkItem store. Conversationalist owns
voice; Business Engine owns role/work intent; Platform owns approvals, tool
federation and company evidence. A company notification or MCP discovery
result does not authorize physical movement. Accept only explicit, bounded,
validated performer capabilities through the existing SDK/broker authority.

Until operator-attended validation establishes real sensed neutral/settled
state and safe room-close/interrupt behavior, retain unknown-outcome
quarantine and no automatic retry/rearm. PR #20 remains an independent
hardware-gated branch, not a behavior to assume merged. Puppeteer's
standalone no-company-mode must continue to work. RoboConduit is an
independent future transport/session layer rather than a substitute
for hardware STOP/leases.

See [company controls](https://github.com/AgenticForge-Labs/agenticforge-platform/blob/main/docs/company-control-and-delegation.md).
This is documentation, not hardware qualification.

Forge Puppeteer is the public physical execution backend for AgenticForge. It owns Stage environment/capture and performer embodiment/control. It must also remain useful as a standalone live robot/show system.

Physical safety takes precedence over autonomy.


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
- PR 4 (Puppeteer PR #13) binds these software-only behavior packs to
  explicit native SDK sessions through a three-performer MCP launcher.
  No physical hardware operation is qualified by CI. PR 5 adds
  Conversationalist as an optional caller; Puppeteer remains standalone.


## Reachy expressive pack (merged PR #16, awaiting hardware acceptance)

- PR #16 merged the opt-in expressive style into `main` at
  `c83efcbbf470f9f72052d2090398f1092e6670dd`. Do not present
  its physical motion envelope as qualified until attended testing.
- `classic` is the unchanged default; yes/no use the existing SHA-pinned
  `play_move` path and must not regress.
- `pose_and_antennas` composes both bounded targets in one pinned SDK
  `goto_target` through the **existing** ExecutionRuntime. It is not a
  raw target MCP tool and requires an explicit operator grant.
- An operator must independently review antenna neutral and head geometry.
  Breathing defaults to zero displacement, and numerical bounds do not
  establish safe clearance or physical settling.
- Never launch the upstream Conversation App motion manager concurrently
  with Puppeteer; retain uncertain STOP quarantine, no autorearm, no
  auto-retry and honest `agent_state` timing provenance.
- Physical acceptance and audio/playback synchronization are separate
  gates. See `docs/REACHY_EXPRESSIVE_MOTION.md`.

## Two-performer native ensemble

- Schema v3 deliberately includes Reachy and StackChan only. It must not
  instantiate, configure, import, or falsely report a simulated SO-ARM101
  performer. Keep one room, one journal, one SDK session per selected robot.
- The original v1 three-robot and v2 Reachy-only configs remain supported
  without semantic conversion, new controller ownership or weaker grants.
- The review-only generator cannot provide motion permission or guess
  Reachy's neutral transform, daemon identity, StackChan serial port,
  firmware revision, or calibration. Local Codex must verify the actual
  hardware before any physical trial.
- Conversationalist's validated `performer_ids` must match exactly what
  Puppeteer advertises. Robot omission is explicit, not a startup fallback.

## Three-performer native ensemble (PR 4/5)

- `mcp/ensemble_launcher.py` is the operator-configured standalone MCP
  room owner. The existing `ConversationEnsemble` and `ExecutionRuntime`
  are its only action/state/ownership path; do not add direct servo motion
  or a parallel dispatcher. Reachy/StackChan SDK sessions are explicit.
- `devices/soarm101_choreography.py` must receive one existing persistent
  SDKAgentExecutor, its bound BrokerWatchdog and immutable ChoreographyPolicy.
  In autonomous-agent mode, requests also require the SDK's active human lease.
  The v4 `operator_choreography_only` daemon mode instead has no agent lease:
  it denies agent state changes and allows exact pinned programs only through
  the authenticated trusted-host route. Read-only discovery of an existing
  broker never authorizes motion. The adapter is **not** a new untrusted agent
  broker/MCP capability.
- SO-ARM candidate idle/listen/talk/nod/shake definitions belong in
  `conversation_soarm.py` as authored semantic content. SDK owns full
  path validation, measured start, calibration, watchdog,
  gripper-free trajectory execution, HOLD and STOP.
- A *normal* semantic transition may wait up to the provider's bounded
  completion grace before canceling a finite native segment; interrupt,
  disconnect, heartbeat expiry and shutdown use immediate SDK STOP.
  Never claim coroutine cancellation has stopped motors. If a device's
  STOP terminalizes its session, return unavailability/quarantine and
  require operator reconciliation; do not automatically rearm/reconnect.
- The review-only generator must not emit authorization markers by default,
  open ports, start robots or grant physical motion. All physical clearance,
  sound timing and STOP drift checks remain for the final Codex handoff.
- Only the *caller* defines current 4G persona and tool grants; Puppeteer
  remains useful independently of Conversationalist or Platform.


## Shared speech-reactive expression contract

- The source-controlled speech_expression.py is a pure, device-independent
  renderer for generated-PCM envelope features from Conversationalist. All
  channels are unitless; they never grant motor or speaker access.
- Do not put PCM, audio sinks, microphone access, personas or LLM providers in
  Puppeteer. Do not copy the upstream Reachy HeadWobbler motor scheduler or
  bypass the existing ExecutionRuntime and SDK authority.
- An unconfirmed STOP or unsensed neutral/sleep state blocks any new physical
  reactive movement; never automatically retry or rearm.
- The optional ensemble_speech_envelope MCP tool can collect bounded features
  only inside the existing room. It must never dispatch physical actions,
  rearm unknown devices, or run a second task scheduler.
- Per-performer mapping remains separately operator-authored and
  SDK-preflighted. SO-ARM101 may only use exact approved finite choreography,
  never an arbitrary per-hop joint stream.
- See docs/SPEECH_DRIVEN_EMBODIMENT.md for pending live transport,
  integration, timing calibration and physical qualification.


## StackChan native expressions versus face presentation scale

- Shared semantic emotion/gesture names belong in Puppeteer's authored behavior
  packs; StackChan SDK owns native eye/mouth/head/light values and validation.
  New finite StackChan faces may be authored with existing operations, but are
  not an SDK-provided built-in emotion engine.
- A separate optional SDK/firmware `display.face_scale` 100%/170% setting
  changes only the LVGL face panel, not geometry, motor ranges, expressions or
  calibration. Never silently enlarge faces from dialogue or model output.
- Do not require a firmware update to run existing native expressions, and do
  not infer physical approval of software-authored expressions from CI.


- The StackChanSpeechFacePreview is a hardware-free candidate mapper and
  MUST NOT be treated as a live face animation driver. It emits SDK-valid
  native mouth cue shapes with bounded rate and release semantics; only the
  existing ExecutionRuntime may dispatch after a separate operator grant.


- Source-grounded StackChan behaviors may use pinned upstream modifier
  semantics (mouth WEIGHT for speaking, eye weight for blink, paired eye
  x/y for glance) but may not run a second firmware modifier controller.
  The optional `stackchan_speaking_mode: native_weight` is operator-only,
  while `legacy_size` remains the default. Never silently switch styles
  based on model interpretation or infer measured face appearance.

## Optional StackChan display scale in Puppeteer

- The merged SDK 0.4.0 defines `display.face_scale` (100 or 170 percent).
  Puppeteer's corresponding `face_scale_update` is a separate operator-granted,
  journaled visual operation. Do not add it to semantic emotion packs, infer it
  from speech, or automatically change it at startup.
- Require the installed SDK 0.4.0 and current firmware's advertised capability;
  older firmware must fail closed for this operation without losing other
  existing controls. Each display request still passes SDK STOP-generation
  fencing and shared resource serialization; firmware completion is not visual
  inspection. No reconnect, automatic retry, firmware flashing, or physical
  qualification is implied by CI.


## Three-robot presentation presets

- The optional room-owned `ensemble_presentation` is for trusted authored
  visual presets, not free-form SDK commands. StackChan normal/enlarged
  face options require `stackchan_display_presets_enabled: true`, installed
  native SDK 0.4.0, and the existing explicit `face_scale_update` grant.
- Reuse `ConversationEnsemble.present` and the same `_play`/ExecutionRuntime
  scheduling, journal, ownership and quarantine as finite expressions.
  Preflight before replacing an ambient behavior. A denied or unsupported
  display operation must not issue motor STOP or alter Reachy/SO-ARM.
- Do not start a second StackChan MCP server for a three-robot room, add
  arbitrary percentages to MCP, or interpret a firmware command-completed
  result as measured face rendering.
