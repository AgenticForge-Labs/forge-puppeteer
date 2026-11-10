# SO-ARM conversational demo and broader MCP implementation plan

## Outcome

Deliver one coherent SO-ARM-only Conversationalist demo configuration with
approved idle/listening/speaking choreography and finite gestures. Preserve
Puppeteer's broader MCP SDK controls for operator work and benchmarks.
The demo must use the same execution, ownership, watchdog and STOP authority
already established in Puppeteer and the SDK.

The operator has explicitly authorized using this robot and accepts
`operator_choreography_only` for the conversational demo. Do not ask for the
same authorization again or treat an agent lease as universal. That mode has
no agent lease; broader agent control retains the SDK's applicable authority
contract. Authorization does not make unavailable capabilities available.

## Verified starting point (2026-11-09)

- Three sibling checkouts contain substantial uncommitted work. Preserve it:
  `forge-puppeteer`, `forge-conversationalist`, `soarm101-motion-sdk`.
- Conversationalist `.env.local` selects the private room under
  `~/.config/forge-puppeteer/soarm101/conversationalist-readonly/`.
  Its SO-ARM provider selects `existing_broker` with capabilities/state/STOP
  only, no choreography revisions, and no trusted-host credential binding.
- Loading that room locally produces idle/listening/speaking patterns with
  `mode=hold`, zero cues, and no startup. Connecting it cannot animate the arm.
- The v4 broker path supports exact finite choreography, but the in-process
  legacy path owns the new wake-to-neutral, breathe selection and timing
  features. The pose authoring exporter still writes the legacy structure.
- Puppeteer's installed SDK reports `0.1.0.dev8` but lacks `JointTarget`.
  The sibling SDK source contains it. Version text alone is insufficient.
- Focused v4/transport/ensemble/transition checks passed (73 tests).
  Adding `test_soarm_wake_to_neutral.py` fails collection on `JointTarget`.
- This review issued no physical commands and made no live readiness claim.

## Implementation sequence

1. **Reconcile the source and installed dependency chain.** Read the three
   repositories' applicable instructions and inspect existing local changes.
   Identify exact SDK APIs required by the chosen path, including trusted
   catalog, physical daemon, STOP and optional startup. Make installation
   reproducible from matching source/wheels; add an actionable capability
   preflight so an old wheel fails before a demo starts. Keep optional robot
   dependencies optional. Avoid unrelated GUI/Reachy changes.

2. **Finish the v4 SO-ARM demo path.** Use the existing SDK-owned daemon and
   private trusted-host interface with `operator_choreography_only` for the
   demo. Bind reviewed idle, listening and speaking assets and finite gestures
   through `ConversationEnsemble` and `ExecutionRuntime`. Extend the existing
   pose importer/exporter to produce the v4 robot/catalog artifacts needed by
   this path. Preserve revision, identity, calibration and whole-path checks.
   Make deliberate hold-only configuration visible as such; a requested motion
   demo missing motion assets must report that before starting voice.

3. **Resolve startup and transitions on that same path.** Inspect existing
   operator pose data without inventing calibration or motion approval. Either
   integrate the exact pinned SDK wake target into the v4 trusted path, or
   expose explicit operator-established neutral readiness; choose based on
   the existing SDK implementation and state the remaining gate clearly.
   Do not leave an apparent wake option that works only on another backend.
   Normal presence transitions must honor bounded completion; interruptions,
   disconnect and heartbeat loss retain immediate SDK STOP and quarantine.
   Do not assume a completed SDK action proves physical settling. Keep finite
   segments short enough for the established transition policy or explicitly
   report incompatible authored duration. Never silently retime reviewed data.

4. **Preserve broader MCP control and benchmarks.** Keep the existing direct
   broker MCP path for supported saved poses, Cartesian/joint jogs, gripper,
   cameras, sleep and permitted recordings. Do not falsely advertise physical
   replay if the broker supports it only in simulation. Provide explicit demo
   versus broader-control profiles/configurations and document ownership and
   session transitions. Operator-only sessions correctly reject broad agent
   writes; this is not grounds to remove broad MCP functionality. Prevent two
   controllers opening the same arm or a busy admission sending someone else's
   controller a STOP. Preserve actual broker lease rules in broader mode;
   do not bypass them or automatically grant/rearm to make a benchmark pass.

5. **Wire Conversationalist to the completed configuration.** Preserve its
   exact performer-ID and referenced-file validation. Make static preflight
   distinguish connected/hold-only from configured motion and separately from
   live readiness. Keep credentials out of configs/logs. Prepare a private
   SO-ARM demo configuration and reproducible launch command using actual
   reviewed assets when available. Back up private files before changing them;
   do not replace an active session or turn configuration edits into motion.
   Keep a usable broader MCP launch configuration. Update contradictory docs
   so v4, authoring, startup and operator-session instructions agree.

6. **Validate the exact installed path and deliver the handoff.** Use SDK
   simulation and a SO-ARM-only integration test through Conversationalist's
   actual ensemble client and Puppeteer's real MCP launcher, not only fake
   performers. Verify idle -> speaking -> idle, an approved finite gesture,
   interrupt/close STOP handling, stale heartbeat, failed admission and unknown
   STOP quarantine. Verify broad MCP operations remain available in their
   correct profile and denied in operator-only mode. Run focused regressions
   and an installed-wheel smoke using matching SDK and Puppeteer artifacts.
   Report exact commands, environment/API provenance and results.

## Completion criteria

- One documented command/config chain resolves to the intended SO-ARM demo
  rather than the old read-only room; no unrelated robots are constructed.
- Motion behavior names resolve to exact reviewed assets, and unsupported
  startup/timing/intents fail visibly before dispatch.
- Matching installed packages pass the actual MCP integration in simulation.
- Broader MCP/benchmark usage remains supported with explicit authority and
  exclusive device ownership; no new execution authority is introduced.
- No changes to safety limits, automatic retry/rearm, or invented approvals.
- Implementation is concrete and reviewable, with remaining physical
  acceptance limited to the actual hardware checks, not missing plumbing.

## Scope of this implementation handoff

Finish software, configuration preparation, simulation and packaging. Do not
move or energize hardware solely to validate plumbing. For an authorized
physical application run, Conversationalist starts Puppeteer's ensemble MCP
service and its configured SDK daemon; the separately attached interactive
SO-ARM MCP server is not a prerequisite. Starting that supported application
lifecycle through process commands is permitted. All semantic motion still
requires existing SDK/broker admission. Never bypass a rejected readiness or
motion check through direct device commands or another controller. Do not
commit private robot data or credentials. No publishing, pushing or PR creation
is required by this handoff. Do the implementation yourself without further
delegation.
