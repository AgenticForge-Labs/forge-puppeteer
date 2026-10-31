# Robot SDK and deterministic MCP implementation plan

Status: implementation in progress; no milestone is complete. The progress below
records software evidence only. This work has caused no physical robot actions;
physical operation and firmware flashing are not authorized by this plan. Each
device still requires its own attended physical qualification and explicit
authorization after software acceptance.

Progress (2026-10-30):

- M4 shared provider entry points (2026-10-30): retained the legacy shot
  `validate/execute` CLI and added an explicitly selected
  `provider-action --provider-config ... --action ...` path plus Python
  `execute_configured_action` API. Both load the same strict operator config,
  instantiate the same backend adapter factory and `ExecutionRuntime` used by
  the MCP launcher, acquire caller/session control, and dispatch one typed
  `ActionRequest`; operator tool grants, adapter validation, runtime ownership,
  resource locks, and stop/quarantine behavior remain authoritative. Added
  fake-backed CLI/Python completion equivalence and grant-denial regressions.
  The path intentionally handles one action, not legacy shot requests or
  performer timelines; lifecycle-bound physical providers still require their
  existing explicit operator authority and SDK/broker setup. No hardware was
  accessed. Verification: `uv run pytest -q` passed (143 tests),
  `uv run ruff check .` passed, and `git diff --check` passed. MCP continues to
  use the same `create_execution` factory; the new regression compares CLI and
  Python fake-backed runtime records and denial behavior. Existing fake stdio
  MCP tests continue to cover the MCP gateway and adapter route independently.

- M4 MCP caller handoff acceptance (2026-10-30): added a real MCP stdio
  fake-device roundtrip for two distinct caller/session identities. The test
  acquires control for caller A, verifies caller B's motion receives
  `ownership_busy`, verifies release receives `performer_busy` while A's action
  is active, then uses action cancellation with confirmed device-stop evidence
  to cancel the action, release A, and admit B. This closes the wire-level
  concurrent-caller/control-handoff acceptance gap; runtime unit tests already
  cover resource-conflict and duplicate-ID races, and cross-process ownership
  tests cover durable arbitration. `uv run pytest -q` passed (141 tests),
  `uv run ruff check .` passed, and `git diff --check` passed. Remaining M4
  integration limit: the legacy `forge-puppeteer validate/execute` CLI and
  `Puppeteer` Python API still run their legacy stage path; they do not route
  through `ExecutionRuntime` or the MCP provider adapters. Thus this test proves
  runtime/MCP behavior, not the broader acceptance claim that CLI, Python, and
  MCP all exercise identical provider code. No hardware was accessed.

- M3/M5 Reachy independent staging and pack acceptance (2026-10-30): added an
  operator fixture with distinct upstream and custom Reachy pack entries and
  compilation coverage preserving each exact asset ID/revision and pack hash.
  Expanded real fake-server MCP stdio coverage to run two independently staged
  catalogs in separate subprocesses, verify each catalog's source, revision,
  and local WAV association, accept exact catalog revisions, and deny stale
  revisions and mutated audio sidecars before playback. Existing snapshot
  verification covers locked inventory, missing/extra files, SDK pin, and
  mutation; this adds the MCP boundary acceptance. `uv run pytest -q` passed
  (140 tests), `uv run ruff check .` passed, and `git diff --check` passed. All stdio servers use fake SDK
  classes; no daemon or hardware was opened. The upstream/custom pack example
  requires operator staging and substitution of each returned asset revision.
  Reachy sound playback remains disabled by the launcher policy, SDK playback
  completion is only coroutine return, and stop/cancellation lacks confirmed
  physical semantics; these limits remain open for attended qualification.

- Forge Studios installed-wheel behavior-pack handoff (2026-10-30): the
  downstream bridge now reports a concrete `forge-puppeteer[mcp]` installation
  command when optional MCP support is absent. Added a Studios regression that
  uses the installed Puppeteer wheel/launcher and generated fake-only operator
  config, computes and checks the exact pack SHA-256, executes one cue over MCP
  stdio, and verifies a deliberately stale pin is denied before launcher start.
  `test_installed_puppeteer_wheel_stdio_handoff_and_stale_pin` passed (1 test)
  from the Forge Studios source tree while importing the installed wheel; it
  does not depend on the Puppeteer checkout. No robot hardware was accessed.

- M5 deterministic dependencies and timing contracts (2026-10-30): scripts now
  accept typed `depends_on` cue IDs and optional `start_tolerance`. Compilation
  rejects unknown, duplicate, cyclic, and non-preceding dependencies before
  MCP startup. Runtime dependency barriers release only after prerequisite
  terminal success; skipped cues count as successful terminal outcomes, while
  failures/cancellation retain existing shielded stop and ownership cleanup.
  Explicit start tolerances fail closed when the observed monotonic start is
  outside the bound. Results retain `requested_at` and the existing `observed_at`
  meaning, and add `observed_start_at` / `observed_finish_at`. Added actual stdio
  dependency coverage and fake-clock timing rejection coverage. This is a
  best-effort scheduler guarantee: general MCP transport cannot guarantee
  hard-real-time starts, and dependencies are required to precede their cue in
  the authored timeline. `uv run pytest -q` passed (137 tests), `uv run ruff
  check .` passed, and `git diff --check` passed. No hardware was accessed.

- M5 SO-ARM replay behavior-pack broker acceptance (2026-10-30): extended the
  installed-wheel simulation smoke to compile, preflight, and execute an
  operator-shaped `replay_recording` behavior pack through the installed Forge
  client, launcher, SDK MCP server, authenticated broker, and SDK simulation.
  A deliberately stale revision is rejected during MCPClient preflight before
  dispatch; the exact catalog pin completes with matching asset identity and
  revision, followed by holding-confirmed STOP. The existing sequence case
  exercises the same behavior-pack path. `bash scripts/soarm101-wheel-smoke.sh`
  passed, building/installing fresh wheels and running both pack cases against
  SDK simulation; `uv run --offline pytest -q` passed (134 tests),
  `uv run --offline ruff check .` passed, and `git diff --check` passed. No
  hardware is opened. The smoke covers these pack routes in the installed-wheel
  acceptance;
  the checked-in fixture retains a replacement marker until an operator selects
  a catalog-approved revision. Verification results are recorded after this
  entry once the full acceptance and repository checks complete.

- StackChan `list_assets` suite failure recheck (2026-10-30): investigated the
  previously recorded single full-suite provider error after the SO-ARM behavior
  pack integration. It does not reproduce in the current worktree: the full
  Forge suite passed with the environment's installed SDK (134 tests) and again
  with `/home/larry/code/stackchan-control-sdk/src` explicitly first on
  `PYTHONPATH` (134 tests). The real-stdio StackChan MCP test, including catalog
  listing and pinned playback, passed in both runs. `ruff check .` and
  `git diff --check` passed. No provider change was justified by the current
  evidence, so the earlier error's root cause remains undetermined; this entry
  supersedes the old suite-result note below but does not claim the historical
  transient was fixed. No hardware was accessed.

- M5 SO-ARM replay behavior-pack authoring (2026-10-30): behavior-pack JSON
  now accepts typed `replay_recording` and `replay_sequence` cues for SO-ARM.
  Compilation requires the exact asset ID/revision and validates the replay
  speed contract, then emits the broker MCP argument shape while retaining the
  pins for live catalog preflight. Added an operator fixture for a bounded
  recording replay and regressions for exact argument/pin preservation and
  rejection of unpinned replay. `uv run --offline pytest -q
  tests/robots/test_mcp_client.py` passed (28 tests). The fixture's asset
  identity is intentionally a replacement marker, so an operator must select
  an approved catalog revision; this tests authored compilation, not an
  end-to-end replay against the SDK broker. No hardware was accessed.

- M4 packaged action-journal recovery (2026-10-30): the operator launcher now
  accepts an explicit `action_journal` path and passes it to the shared runtime;
  MCP `action_lookup` can resolve journal-restored IDs after process restart
  while still returning `unknown_action` for absent IDs. A packaged launcher
  stdio regression runs a typed fake StackChan action, closes that server,
  starts a new server process against the same SQLite journal, and verifies the
  completed record's state and identity. Focused launcher/server tests passed
  (5 tests); the full Forge suite passed (132 tests), `ruff check .` and
  `git diff --check` passed. This verifies completed-record persistence across
  the packaged process boundary; crash-interrupted actions still require an
  independently confirmed stop, and that physical reconciliation is not
  established here. No hardware was accessed.

- M5 local speech cue execution (2026-10-30): the deterministic MCP script
  compiler now accepts typed `speak` cues using the existing `SpeakAction`
  contract, without routing text or voice options to a device MCP tool. The
  executor schedules local voice output on the take's monotonic timeline and
  runs it concurrently with configured stdio device actions; authored
  `sample_duration` holds the speech cue's timing window. Missing voice
  providers and invalid speech payloads fail preflight. A voice failure stops
  voice output and fans independent device STOP calls before releasing acquired
  ownership. The single injected voice provider is now treated as a shared
  resource: overlapping speech serializes, and late policy is applied after
  waiting for that resource. Cancellation of local speech stops both voice and
  configured devices under a shield; incomplete takes also run shielded device
  cleanup before ownership release. Real stdio regressions cover same-time
  speech/device cues, voice option forwarding, sample-duration timing, and
  voice/device stop after speech failure; fake-clock/provider coverage checks
  voice serialization, late-skip behavior, and cancellation cleanup. Behavior
  pack JSON now accepts typed local speech cues alongside performer actions,
  with the same voice provider and timeline rules. Focused
  `tests/robots/test_mcp_client.py` passed (26 tests); the full Forge suite
  passed (131 tests), `ruff check .`, and `git diff --check`. The full StackChan
  SDK suite passed (110 tests), `ruff check .`, `mypy`, and `git diff --check`.
  This adds executor-level
  coordination only: the provider owns actual audio scheduling/output and its
  device-side start/finish latency is not independently measured. Behavior-pack
  speech still needs an injected local `VoiceProvider`. No audio device or robot
  hardware was accessed.

- M2/M4 SO-ARM installed-wheel integration smoke (2026-10-30): added
  `scripts/soarm101-wheel-smoke.sh`, which copies current Forge and SDK source
  snapshots, builds fresh wheels, stages their dependency closure, and installs
  offline into a clean Python 3.12 environment. The smoke uses the installed
  Forge MCP launcher, installed SDK MCP server, authenticated SDK broker and
  SDK simulation. The sequence case now runs through Forge's actual `MCPClient`
  behavior-pack compiler, preflight and executor; it checks exact pinned replay
  and stale revision rejection before dispatch. A bounded simulated joint action
  and holding-confirmed STOP are checked through the installed launcher. It
  sets isolated Python import behavior so runtime processes cannot load either
  sibling checkout. A separate fresh SDK simulation broker session exercises
  exact pinned recording replay, stale recording revision rejection, terminal
  lookup, and holding-confirmed STOP; its distinct process respects the
  broker's one-replay-per-session STOP lifecycle. Integration exposed that SDK
  recording success is nested at `completion.completed`, unlike sequence
  replay's root-level `completed`; Forge now recognizes both explicit terminal
  shapes, with a provider regression for the nested result. The installed-wheel
  smoke passed for both sequence and recording paths. The MCPClient path exposed
  broker-incompatible colon separators in generated action IDs and a terminal
  lookup/control-release race; IDs now use broker-safe period separators and
  release retries the brief `performer_busy` cleanup window, and completion
  polling treats `accepted`/`queued` as nonterminal. Focused Forge MCP client,
  provider and packaged launcher tests passed (47 tests); Ruff on changed
  files and `git diff --check` passed. Both compatible artifacts are
  unpublished (`forge-puppeteer==0.1.0` and `soarm101-motion-sdk==0.1.0.dev8`);
  wheel metadata does not capture Git revisions, and unconstrained dependency
  staging may resolve differently later. The full Forge suite with the
  StackChan source import path had 133 passes and one unrelated StackChan MCP
  `list_assets` provider error; the focused SO-ARM tests are green. Exact reruns
  require preserved source snapshots and wheelhouse files/hashes. No hardware
  or physical mode was used.

- M2/M4 SO-ARM Forge replay integration (2026-10-30): connected the existing
  SDK simulation-only pinned sequence replay route to Forge's packaged SO-ARM
  MCP adapter and shared `ExecutionRuntime`. Forge exposes typed recording and
  sequence replay operations carrying the exact `asset_id`, `sha256:` revision,
  and bounded speed scale; dispatch additionally resolves the live SDK session
  identity and accepts only explicit terminal completion. Replay is unavailable
  unless the launcher is configured `simulation_only`; SDK profile grants,
  catalog policy pins, watchdog heartbeat, STOP epoch fencing, and existing
  SDK replay validation remain authoritative. Added a real process stdio
  integration from Forge launcher through the SDK MCP stdio server and
  authenticated broker to SDK simulation. It discovers and replays
  `soarm101/sequence/stdio-e2e` at exact revision
  `sha256:5678990e2638b441b2fb61a8b77057eb3c53b9c43c53c43901eced3b11db4a85`,
  executes its bounded simulated gripper and wait steps, looks up terminal
  status, denies a stale revision before runtime dispatch, and confirms
  independent STOP while unarmed. The
  SDK simulation replay watchdog now validates the bound simulation session
  without requiring a human physical lease; its profile and exact replay policy
  remain required. Updated stop/heartbeat race tests exercise late-result
  fencing. Focused tests passed: SDK replay/broker/watchdog (66 tests,
  `--no-cov`), Forge provider and packaged launcher stdio (18 tests, including
  the real broker process integration); the Forge suite passed (124 tests). With
  `.venv/bin` added to `PATH`, the full SDK suite passed (870 tests, 82.64%
  coverage). SDK Ruff and both repositories' `git diff --check` passed.
  SDK-wide mypy reports 286 existing diagnostics across unrelated modules;
  focused mypy on changed replay modules has no added diagnostics (the existing
  `broker_sdk.py` module reports 10 pre-existing errors).
  Source checkout base revisions used for this software evidence were Forge
  `f629a15481d70bb4e8cc0c55c6fb19dcde0d8f9d` and SDK
  `cf97dfcee41c5015aacbfb99412c1c2c73dea89f`; both worktrees already contained
  unrelated uncommitted changes, and this task leaves all work uncommitted.
  The stdio acceptance launches the sibling SDK checkout and its venv; no
  released SDK artifact or deployment dependency pin exists for this modified
  code, so the recorded Git base revisions are evidence anchors, not a packaged
  reproducibility guarantee (M6 remains open).
  No hardware or physical mode was accessed. This does not establish physical
  replay, production broker deployment, device braking, or attended acceptance.

- M1 StackChan SDK transport/MCP roundtrip (2026-10-30): expanded the installed
  wheel PTY smoke to run the packaged Forge MCP launcher against the SDK's real
  `RoutedSerialClient` and `ExternalControl`, opening a local PTY as the serial
  device. The fake firmware now emits ordered `accepted`, `progress`, and
  terminal events. Stdio discovery and action lookup cover face and head
  completion, a firmware `failed` event on lights, and a subsequent confirmed
  `system.stop`; the runtime conservatively records the raised firmware
  rejection as `unknown` / `execution_outcome_unknown`. The complete offline
  package smoke passed, including this installed Forge+SDK wheel path and the
  other packaged stdio smokes. Puppeteer suite: 122 passed; `ruff check .` and
  `git diff --check` passed. This closes the software MCP/SDK transport
  roundtrip against fake firmware, including command event ordering and
  failure/stop handling. It does not establish real firmware compatibility,
  USB behavior, physical completion, or actual stopping behavior; physical
  qualification remains separate. No hardware, commits or pushes were used.

- M1 StackChan catalog metadata verification (2026-10-30): the descriptor's
  `duration_s` is the authored schedule span (final step `at_s`), not measured
  device runtime. Kept the SDK README aligned with that contract and added a
  `stackchan assets` CLI regression proving the span is present without opening
  USB. In `/home/larry/code/stackchan-control-sdk`, `.venv/bin/pytest` passed
  (110 tests), `.venv/bin/ruff check .`, `.venv/bin/mypy`, and `git diff
  --check` passed. This supersedes the earlier environment limitation below:
  the existing Python 3.14 `.venv` has the editable package and required test
  and lint dependencies. No hardware was accessed.

- M1 StackChan catalog metadata follow-up (2026-10-30): asset descriptors now
  report the authored schedule span (`duration_s`, the final step's `at_s`)
  instead of returning `null` for every asset. This is timeline metadata, not a
  promise about device execution time; playback still reports host-observed
  command timings and firmware events. Added pose and sequence descriptor
  regression coverage. `PYTHONPATH=src pytest -q tests/test_assets.py` passed
  (6 tests), `ruff check .` and `git diff --check` passed. Full-suite collection
  could not run in the available shared venv: the package is not installed and
  `jsonschema` is absent; `mypy` is also unavailable there. No hardware was
  accessed.

- M4 SO-ARM actual SDK simulation stop-contract recheck (2026-10-30): reran
  the packaged Forge launcher through the SDK MCP stdio server and its real
  `--sdk-simulation-actions` HTTP broker. The flow verified a bounded simulated
  joint action completes, SDK STOP returns the `completed=true` plus
  `holding=true` confirmation accepted by the Forge provider, and the existing
  failure/recovery stdio scenario still denies motion after a weak STOP and
  permits a new action after holding-confirmed STOP. The actual SDK simulation
  backend has no in-session motion recovery after STOP: it latches the broker
  session stopped, so recovery there requires a new broker session. Its replay
  quarantine/restart reconciliation remains covered by SDK-specific tests,
  separately from Forge runtime quarantine. Focused Forge stop-contract tests
  passed (10 tests); SDK broker/replay focused tests passed (78 tests), though
  their isolated run reports the repository's global 70% coverage threshold
  because unrelated modules are not loaded. Ruff and diff checks passed. No
  hardware was accessed. This verifies software response handling only; it does
  not establish production broker semantics, actual braking, or physical safety.

- M4 SO-ARM stop/recovery acceptance (2026-10-30): tightened broker stop
  reconciliation so `status="completed"` alone cannot release the runtime's
  unknown-action quarantine; accepted confirmation now requires explicit
  `confirmed=true`, `status="stopped"`, or `completed=true` together with
  `holding=true`. Added a real MCP stdio failure/recovery scenario: an action
  returns indeterminate completion, the weak stop is rejected and retry denied,
  then a holding-confirmed stop permits a fresh action. Focused SO-ARM provider
  and stdio tests passed (12 tests); the full repository suite passed (122
  tests), and `ruff check .` passed. No hardware was accessed. This verifies
  software contract handling against a fake broker only; production broker
  response semantics, actual stopping behavior and attended physical
  qualification remain open.

- M4/M6 StackChan operator launcher follow-up (2026-10-30): added an
  `stackchan_sdk` backend that accepts only the fixed StackChan tool set,
  requires an exact installed SDK distribution version, explicit USB port and
  explicit authority declaration, and opens/closes the provider within the MCP
  server lifecycle. Fake-transport tests exercise the configured connection,
  allowlist, stop and cleanup paths. `pip index versions stackchan-control`
  returned no matching distribution, so no package extra or fabricated version
  pin was added; deployment requires an operator-supplied compatible wheel.
  The launcher/provider/MCP focused suite passed (9 tests), the full Puppeteer
  suite passed (116 tests), and Ruff plus `git diff --check` passed. The SDK
  repository clean-wheel smoke built `stackchan-control==0.3.0`, installed it
  into a fresh Python 3.12 environment and exercised discovery, state and head
  commands with a fake serial client; all 108 SDK tests, Ruff and mypy passed.
  The wheel smoke verifies SDK packaging and CLI operation, while the launcher
  fake-transport tests verify adapter lifecycle; this does not establish a
  combined published Puppeteer-plus-SDK wheel deployment because no StackChan
  SDK release exists. No hardware was connected.

- M1/M6 StackChan SDK-only wheel deployment smoke (2026-10-30): added
  `stackchan-control-sdk/scripts/stackchan-wheel-smoke.sh`. It builds the SDK
  wheel offline, installs it without dependencies into a fresh Python 3.12
  environment, invokes the installed `stackchan` and `stackchan-probe` entry
  points, and exercises installed discovery, state and bounded head-control CLI
  paths against a fake serial client. It also checks the wheel metadata does not
  require Puppeteer or MCP. The offline wheel smoke passed; the SDK suite passed
  (108 tests), `ruff check .`, `mypy`, and `git diff --check` passed. An initial
  `uv run --offline pytest` attempt could not resolve the uncached `mypy` dev
  dependency; using the repository's existing `.venv` ran all prescribed checks
  successfully. No hardware, commits or pushes were used. This closes clean
  SDK-only wheel installation and CLI interoperability against a fake transport;
  actual device firmware compatibility, USB behavior and physical qualification
  remain open. The packaged launcher now exposes the runtime's optional action
  journal explicitly and real stdio verifies completed action lookup after a
  server restart; restart lookup is therefore covered for completed fake
  actions. Full adapter-path crash-interrupted recovery and physical robot
  acceptance remain open.

- M4 SO-ARM actual broker simulation integration (2026-10-30): added a
  repeatable process-level acceptance test from Forge's packaged MCP launcher,
  through the SDK's real MCP stdio server and authenticated HTTP broker, into
  the persistent `--sdk-simulation-actions` SDK backend. The test discovers
  capabilities/state, confirms `broker_session.mode=simulation_actions` and
  `authority.armed=false`, checks the allowlist omits Cartesian motion, performs
  a profile-capped 0.5 degree simulated joint jog, reconciles terminal action
  status, and calls the broker STOP. It exposed that SDK simulation still
  required a human physical authority lease; simulation actions now use explicit
  unarmed simulation metadata and retain the broker profile and action bounds.
  Forge now exposes typed `jog_joint` and has an explicit `simulation_only`
  launcher mode that verifies the downstream SDK session mode before discovery
  or action. The real-process integration and launcher contract suite passed
  (7 tests); the full Puppeteer suite passed (114 tests), and the full SDK
  non-hardware suite passed (870 tests). Scoped Ruff and diff checks passed in
  both repositories. No hardware or physical authority was used.

- M4 SO-ARM broker completion-contract follow-up (2026-10-30): tightened the
  provider so an evidence string cannot turn an accepted, incomplete, or failed
  broker response into Puppeteer `completed`; completion now requires an explicit
  successful status or `completed=true` without a contradictory status. Added
  regressions for text-only evidence, `completed=false`, and failed status paired
  with `completed=true`. The SO-ARM provider, actual MCP stdio, and packaged
  fake-broker launcher suites passed (16 tests); the full Puppeteer suite passed
  (112 tests); Ruff on changed Python files and `git diff --check` passed. No
  broker or robot hardware was accessed. Production SO-ARM broker/profile
  compatibility and attended physical qualification remain open.

- M2 SO-ARM full-suite timing recovery (2026-10-30): after the previous complete
  non-hardware run reported 869 passed and one simulated CLI Sleep failure from
  missing its 50 ms command deadline by 62 ms under load, regular CLI
  `--simulation` was switched to the deterministic non-real-time simulator. This
  matches the SDK's documented `SimulationBackend` behavior; explicit
  `sim-demo --realtime` retains the unchanged 50 ms deadline guard. The complete
  SDK suite then completed successfully: 870 tests collected and passed;
  `ruff check .` and `git diff --check` passed. Scoped mypy on the CLI module
  still reports 73 existing diagnostics outside the changed simulation selection.
  This is software-only recovery evidence, not physical motion or braking
  evidence. No hardware, physical arming, commits or pushes were used.

- M2 SO-ARM restart reconciliation follow-up (2026-10-30): the simulation-only
  broker now durably journals each recording/sequence replay identity before
  execution, flushes replay intent records to disk, and restores a fail-closed
  quarantine after restart. A quarantined broker rejects new replay until its
  own simulated SDK STOP returns both `completed=true` and `holding=true`; the
  confirmed STOP resolution is also journaled. Malformed journals remain
  quarantined. Restart regression coverage and focused replay tests passed (35
  tests), as did Ruff and `git diff --check`. This is software-only evidence;
  replay remains simulation-only and no hardware or physical motion was used.

- M4/M6 operator-configured launcher acceptance (2026-10-30): reviewed the
  in-progress broker launcher and added negative tests proving missing, empty,
  near-match, and `none` authority declarations are rejected before a broker
  session can start. Focused provider/launcher stdio tests passed (23 tests),
  `ruff check .` passed, and the offline clean-wheel smoke passed: it built a
  wheel from a staging copy, installed the wheel plus MCP dependencies in a
  fresh Python 3.12 environment, and exercised fake StackChan, Reachy, and
  SO-ARM stdio flows including broker pose and stop delegation. No hardware
  was connected. Reachy remains catalog-only in the packaged launcher because
  its current SDK adapter has no independently confirmed stop contract and no
  launcher-owned daemon connection lifecycle. StackChan real-device launch is
  not pinned because the compatible external-control facade is unpublished;
  adding a guessed VCS dependency would not establish a reproducible install.
  SO-ARM production still depends on the operator's separately installed and
  qualified broker/profile, which remains the authority boundary. M4/M6 and
  physical acceptance remain in progress.

- M4/M6 SO-ARM broker deployment (2026-10-30): added an operator-selected
  `soarm101_broker` packaged launcher backend. It requires an explicit physical
  authority declaration, starts only the configured broker via MCP stdio with
  no shell, intersects the operator allowlist with currently visible broker
  tools on every request, and closes the broker session with server lifecycle
  cleanup. A packaged launcher stdio integration test executes discovery, an
  allowed fake pose, and stop against a separate fake broker process. This does
  not grant or verify real authority and no hardware was connected. No SDK pin
  was added because Puppeteer delegates to the broker MCP contract; the
  separately deployed broker and its profile must be pinned by the operator.
  Attended physical qualification, production broker/profile compatibility,
  and downstream integration remain open. The reusable
  `scripts/stdio-package-smoke.py` now exercises discovery, an allowed fake pose,
  terminal action lookup, and stop through `forge-puppeteer-mcp` installed from
  a freshly built wheel, with a separate fake broker subprocess. The wrapper
  `scripts/offline-package-smoke.sh` runs this after installing MCP and all wheel
  dependencies offline from the staged cache or supplied wheelhouse; it does not
  touch the network. Initial wheel run found that immediate action lookup can
  legitimately report `running`; the smoke now polls with a five-second bound
  for a terminal outcome. Offline wheel smoke passed after this fix. Physical
  authority is simulated only; real broker/profile compatibility and attended
  qualification remain open.

- M5 behavior-pack follow-up (2026-10-30): generalized the operator-authored,
  revision-pinned manifest compiler from StackChan to typed Reachy and SO-ARM
  operations. Each operation is validated against the MCP tool's existing
  parameter model and performer allowlist; Reachy `play_move` requires an exact
  asset ID/catalog revision, so execution preflight checks the staged catalog.
  Added examples for Reachy's exact staged recording playback and SO-ARM's
  broker-owned saved `sleep` pose. Reachy example intentionally contains a
  revision marker that must be replaced with the live catalog revision. Actual
  fake stdio executions passed for both packs (and for existing StackChan
  coverage); the full repository suite passed (104 tests), `ruff check .` and
  `git diff --check` passed. `mypy` is not installed in this environment. This is
  compile and fake-server evidence only: no physical hardware was used. The
  fixture set does not provide general Reachy coordinate sequences or SO-ARM joint/Cartesian
  motion programs; runtime readiness, authority, resource, calibration, and
  provider checks remain required.

- M5 preflight follow-up (2026-10-30): deterministic scripted MCP preflight now
  validates every cue's arguments against the corresponding server-advertised
  JSON Schema, including required fields and nested typed parameters, before
  acquiring performer ownership or dispatching any cue. Invalid arguments fail
  with a bounded cue/tool error. A real stdio regression with an otherwise-valid
  first cue and malformed later cue verifies that neither ownership nor action
  dispatch occurs. Focused scripted-client suite passed (19 tests); the full
  Puppeteer suite passed (101 tests), `ruff check .` passed, and `git diff
  --check` passed. The MCP extra now declares JSON Schema validation directly
  (already present in the repository lock). This closes pre-dispatch tool-schema
  validation for configured MCP tools; runtime/device state can still change
  after preflight, so provider validation at dispatch remains required.
  Files changed for this follow-up: `pyproject.toml`,
  `src/forge_puppeteer/mcp/client.py`, `tests/robots/test_mcp_client.py`, and
  `tests/robots/test_reachy_mcp.py`; this plan records the evidence.
  Read-only GitHub review found local `main` matching remote for Puppeteer
  (`f629a154`) and StackChan (`f92499bf`), with no open PRs in either. SO-ARM
  remote `main` is `cf97dfc`; Reachy remote `main` is `d711629` with one open
  draft PR; Studios remote `main` is `a75601a` with two open draft PRs. Those
  repositories contain local changes and were not modified. Organization-level
  `development.md` and `architecture.md` were not found locally.

- M5 shared ownership follow-up (2026-10-30): added operator-configured SQLite
  arbitration (`ownership_db` in trusted MCP server config, or
  `FORGE_PUPPETEER_OWNERSHIP_DB` for direct runtime deployments). The claim is
  acquired transactionally and includes a process identity; competing server
  processes cannot treat matching caller/session strings as the same owner.
  Motion admission uses a transactional durable `performer_activity` row, so
  only one runtime process can have an admitted action for a performer at a
  time; claim acquisition is denied while any such row exists. Completed and
  pre-dispatch failed actions release their rows. Unknown outcomes and process
  crashes leave rows in place, failing closed. This arbitration requires all
  writers to use the same configured database; action journal coordination is
  still process-local. SQLite storage must be a reliable local shared file;
  network filesystems and multi-host deployment are not claimed. Recovery from
  a stranded row requires the operator to use the established device-safe
  procedure, confirm the performer stopped, then remove that performer's rows
  from both `performer_activity` and `performer_owners`; no automatic expiry is
  used. Cross-process stop cannot fence a remote task between its final software
  check and provider dispatch, so such stop requests fail with
  `cross_process_stop_unsupported` while a remote activity row exists. Ambient
  schedules are process-local and are disabled when shared ownership is
  configured (`ambient_shared_process_unsupported`). Single-process stop and
  ambient paths remain available. These are safe restrictions, not full
  cross-process stop coordination. Multiprocess tests cover competing motion
  admission, claim-vs-motion arbitration, rejection of cross-process stop, and
  crash-persistent quarantine. Validation: full repository suite passed (100),
  `ruff check .` passed, and `git diff --check` passed. No hardware was used.

- M5 Reachy preflight follow-up (2026-10-30): corrected resource metadata
  resolution for MCP tools whose provider control IDs are namespaced (for
  example `reachy.head_pose`). Preflight now accepts exact or unambiguous
  namespace-suffixed control IDs and rejects ambiguous metadata. Actual MCP
  stdio tests verify an authorized Reachy `head_pose` plan passes preflight and
  same-time cues claiming the same resource are denied. The focused Reachy and
  scripted-client suites passed (21 tests), and Ruff passed on the changed
  client/test files. This closes this specific software preflight gap only;
  resource modeling for all providers, broader M5 behavior packs, deployment
  integration and physical qualification remain open.
- M5 ownership follow-up (2026-10-30): scripted execution now gives each run a
  unique ownership session ID even when an authored `take_id` is reused. A real
  stdio fake-server test confirms a later device's ownership denial releases an
  earlier claim before any cue dispatch; MCP cleanup exception groups preserve
  the actionable ownership error. The focused MCP client suite passed (18 tests)
  and Ruff passed on the changed client/test files. This covers acquisition and
  rollback within one configured multi-device take; cross-process/shared-device
  arbitration and broader M5 behavior packs remain open.
- Dependency/lint follow-up (2026-10-30): refreshed `uv.lock` from the
  repository's exact `reachy-mini==1.10.0rc5` extra; lock validation succeeds
  both with the default interpreter selection and explicitly on CPython 3.14.
  The current PyPI metadata for that exact release declares
  `Requires-Python >=3.11` with no upper bound, and uv resolves all declared
  extras on CPython 3.14. The previously reported Python 3.14 resolution failure
  could not be reproduced against the current index. No speculative SDK
  compatibility cutoff was added; the only SDK version claimed is the exact
  pinned release, whose declared Python range is recorded here. A full dry-run
  sync of `dev`, `mcp`, and `reachy` on CPython 3.14 resolved 94 installs from
  the refreshed lock. All repository tests passed (94), and `ruff check .`
  passed. These are software and resolver checks only; no Reachy runtime or
  hardware compatibility is established.

- M0: in progress. Versioned, JSON-safe device/action metadata, fixture control
  schemas, offline examples and reconciliation notes are present. Runtime
  integration and actual MCP wire/dependency compatibility acceptance remain
  outstanding; dependency resolution alone is not compatibility evidence.
- M1 StackChan: in progress. The SDK has USB event routing, typed discovery/state
  and controls, stop fencing, validated asset playback, bounded NeoHEX output,
  schemas, CLI coverage, and descriptors with the authored schedule span. The
  complete software suite passes: 110 tests,
  `ruff check .`, and `mypy`. A built wheel installed in a fresh Python 3.12
  environment and the offline `stackchan assets` CLI smoke passed. The installed
  Forge+SDK wheel PTY smoke covers MCP discovery, SDK serial command/event
  routing, face/head completion, firmware rejection handling, and stop against
  fake firmware. This does not establish hardware behavior. Physical
  qualification remains separate, including device/firmware compatibility,
  real USB completion and stop behavior, watchdog timing, and attended motion
  acceptance. The full prescribed SDK suite and checks were rerun for the
  catalog metadata change; the earlier shared-venv limitation is superseded by
  the verification entry above.
- M2 SO-ARM: in progress. Ordered, operator-pinned preflight resolves and pins
  supported pose/trajectory dependencies, checks calibration and supported
  parameters, bounds loops and known duration, and captures required entry-state
  checks. Simulation-only recording/sequence execution now reaches the broker
  from Forge MCP with an exact SHA-256 asset revision and terminal status
  reconciliation. Unsupported step types and unknown durations are rejected;
  the broker revalidates catalog revisions, entry state, profile grant, STOP
  epoch and watchdog at execution. Physical replay remains disabled and requires
  separate attended qualification of calibration, entry motion, stop paths,
  watchdog behavior and bounded execution.
- M3 Reachy: in progress. The staged catalog and manifest checks are supplemented
  by provider playback and native-control fake tests. A prior Puppeteer suite
  passed 54 tests with optional MCP and a locally built StackChan SDK installed;
  the new fake coverage demonstrates software paths only. Reachy's SDK stop call
  has no cancellation acknowledgment or confirmed stopped state, so stop remains
  unconfirmed. Physical qualification requires a reliable independently verified
  stop, plus attended validation of the pinned SDK, assets, audio and playback
  lifecycle.
- M4 Puppeteer/MCP: in progress. Shared execution, MCP server/client paths,
  provider fakes and packaged SO-ARM launcher-to-SDK broker simulation stdio
  replay are present. SDK exact-revision replay reaches Forge's shared runtime,
  and action lookup plus broker STOP are covered across real subprocesses.
  SQLite journal restart/recovery coverage passed 27 tests; MCP client
  failure-path coverage passed 75 tests. The offline wheelhouse CLI smoke
  passed. The packaged launcher also accepts an explicit action journal, and
  real stdio verifies completed action lookup after restart. Production
  deployment, broader concurrent/cancellation acceptance, full adapter-path
  crash-interrupted recovery and physical robot acceptance remain open.
- M5: in progress. The deterministic MCP client has real stdio subprocess
  acceptance for a two-device mock take, including all-device preflight denial
  before dispatch, independent stop fan-out after a mid-take failure, and
  revision-pinned Reachy/SO-ARM behavior-pack runs against fake providers.
  Local typed speech cues now share the take timeline with stdio devices,
  preserve authored sample-duration windows, serialize access to the shared
  voice provider, apply late policies after resource waits, and clean up voice
  and devices on speech failure or cancellation; focused coverage passed (26
  tests, including real stdio and deterministic fake-clock/provider cases).
  Behavior packs can author typed local speech cues; a regression verifies
  local routing and mixed voice/device execution. The full Forge suite now
  passes (131 tests) with `ruff check .` and
  `git diff --check`; the
  full SDK suite passes (110 tests) with Ruff, mypy, and `git diff --check`.
  Cross-server ownership, packaging acceptance, downstream integration and
  physical qualification remain open. Speech cues require an injected local
  provider and cannot independently verify acoustic output timing or hardware
  playback. The SO-ARM replay behavior-pack fixture uses a replacement asset ID
  and revision; a live operator must substitute a catalog-approved pin. The
  installed-wheel acceptance covers generated recording and sequence packs
  against SDK simulation.
- M4/M6 packaging update (2026-10-30): the wheel now installs a
  `forge-puppeteer-mcp` entry point with explicit operator-selected fake profiles
  for StackChan, Reachy, and SO-ARM tool schemas. A fresh temporary environment
  installed the wheel plus MCP 2.3.0 and completed catalog discovery and one
  deterministic stdio action for each profile. This is fake-only software
  evidence. No real-device launcher or SDK extras are published: the StackChan
  facade is unpublished, Reachy still requires the separately staged pinned SDK
  and assets, and SO-ARM remains an external broker dependency. M4/M6 remain in
  progress pending those deployment integrations and downstream documentation.
- M4/M6 Reachy packaging verification (2026-10-30): added the exact
  `reachy-mini==1.10.0rc5` extra and verified the installed wheel with MCP 2.3.0
  and all 89 resolved packages in a fresh Python 3.12 environment, installed
  offline from a temporary wheelhouse. The wheel was built locally; PyCairo and
  PyGObject had to be built into that wheelhouse against host Cairo/GObject dev
  libraries because no compatible binary wheels were available. The staged
  revision-locked dataset loaded through the installed launcher over stdio and
  returned 85 unique assets across paginated reads; device state remained
  disconnected/not-ready and no playback tool was exposed. This is catalog
  software evidence only; runtime hardware, playback and stop qualification
  remain open.
- M6 Studios/forge-reachy integration examples (2026-10-30): added a
  downstream handoff fixture that keeps the Studios shot/request boundary,
  an explicit performer/behavior-pack mapping, and exact behavior-pack and
  fake-catalog SHA-256 pins. Added a packaged-MCP offline smoke that launches
  the installed `forge-puppeteer-mcp` entry point, preflights and executes the
  Reachy-shaped fake cue, verifies completed action lookup, and rejects a stale
  pack revision. The fake profile has an explicit bounded tool allowlist and
  reports `physical_authorization=none`; it opens no SDK, daemon, network or
  hardware. Studios has an opt-in `dispatch_behavior_pack` route that compiles
  and executes a pinned pack through `MCPClient`; it is not an automatic
  replacement for the legacy request/execute route. Studios still requires an
  explicit caller to select the opt-in route and
  supply operator-owned server/pack configuration. forge-reachy's stock
  Conversation App remains the owner of realtime conversation and motion unless
  an operator explicitly hands off the writer. `bash scripts/offline-package-smoke.sh` passed in a fresh Python
  3.12 venv, including installed-wheel stdio MCP execution of this handoff and
  the existing StackChan, Reachy fake-SDK and SO-ARM broker smokes. Added a
  fixture regression asserting fake-only configuration and matching exact pins.
  This does not demonstrate invocation of the Studios route from its normal
  shot flow, upstream Conversation App integration, Reachy simulation, or
  physical playback/stop. The Studios helper route has fake-MCP and
  installed-wheel stdio tests. Existing unrelated Studios edits were preserved.
  No hardware, commits or pushes were used.
- M3/M4/M6 Reachy launcher implementation (2026-10-30): added an opt-in
  `reachy_sdk` launcher backend around the pinned `ReachyPlaybackProvider`.
  The trusted config must declare `I_AUTHORIZE_REACHY_CONTROL`, exact SDK
  `1.10.0rc5`, staged assets, an explicit host/port, and a per-tool allowlist.
  The launcher rejects `auto` connection discovery, daemon spawning is fixed
  off, and the SDK client is created only inside server lifespan and closed via
  its SDK context manager after runtime cancellation. Tests use a fake SDK
  module and exercise the actual MCP stdio path, exact tool filtering, playback
  request and shutdown cleanup. SDK inspection confirms playback completion is
  only coroutine return; `cancel_move()` is asynchronous in effect and has no
  acknowledgment, while native `goto_target` has no independent stop API.
  Stop therefore reports unconfirmed and the provider quarantines itself from
  further motion for the process lifetime. This is conservative software
  containment, not verified stopping or physical safety. Hardware/daemon was
  not contacted. Physical qualification and independently verified safe-stop
  remain open; M3/M4/M6 are still in progress.
- M5–M7: in progress/planned; no completion claim. End-to-end deployment,
  orchestration and physical acceptance remain open. Physical qualification is a
  distinct gate for each device and must follow completed software milestones,
  attended acceptance, explicit authority, and verified safe-stop behavior.

- Reachy launcher verification follow-up (2026-10-30): inspected the installed
  `reachy-mini==1.10.0rc5` package in the pinned Forge Reachy environment without
  constructing `ReachyMini`. The observed constructor accepts the launcher's
  explicit `host`, `port`, `connection_mode`, `spawn_daemon`, `use_sim`, and
  `media_backend` arguments. `async_play_move(move, ..., sound=True)` accepts
  the adapter's explicit `sound=False`; `goto_target(head, antennas, duration,
  ..., body_yaw)` accepts its native-control mapping; `cancel_move()` is
  fire-and-forget; and `__exit__` closes media and disconnects the client. The
  launcher uses `no_media`, disables daemon spawn/simulation, and calls
  `__exit__` during lifespan cleanup. A lifecycle gap was fixed so SDK cleanup
  also runs if local adapter initialization raises after SDK construction.
  Added a no-connection API signature smoke and a fresh-installed-wheel MCP
  stdio smoke that injects a fake SDK module, filters tools, performs one fake
  recorded move, and asserts lifecycle closure. Stop remains unconfirmed and
  quarantines motion for process lifetime; the native `goto_target` path has
  no SDK cancellation acknowledgment. Full hardware qualification remains
  explicitly open.
- Reachy launcher verification results (2026-10-30): the API signature smoke
  passed against the installed pinned SDK. The new fake-SDK stdio lifecycle
  also passed from a freshly built wheel installed into a clean Python 3.12
  environment; the wheelhouse was staged first, then install ran with
  `--offline --no-index`. The complete offline package script passed including
  CLI, all fake MCP profiles, SO-ARM fake broker and StackChan PTY checks. The
  full test suite passed (121 tests), `ruff check .` and `git diff --check`
  passed. These checks exercised no daemon or robot hardware. The local
  dependency cache alone lacked the runtime wheels, so the script staged them
  from the configured package index before running the offline install phase.

## Outcome and agreed boundaries

Studio performance scripts drive Puppeteer's deterministic executor, largely
through device MCP servers built and packaged by Puppeteer. Conversational agents
can use the same servers, discovered controls and behavior catalogs. No LLM is
required to execute an authored performance.

Puppeteer owns behavior composition, scheduling, voice coordination, performer
adapters, MCP packaging and action provenance. Device SDKs own native controls,
connections, device validation, playback primitives and actual stop semantics.
The existing SO-ARM broker remains authoritative for agent permission and physical
authority. MCP never creates that authority.

Reachy Mini depends on its SDK and pinned motion assets. The Conversation App is
an upstream design/code reference, not a required runtime dependency. Borrow its
queueing, idle and speech-coordination ideas; copy code only with license/notices
and explicit provenance. Do not modify forge-reachy's installed upstream checkout
or replace its existing conversational launch path.

Use explicit typed adapters, not arbitrary Python reflection or model-provided
method names. Discover catalog contents rather than hardcoding emotion names.
SDK updates must not automatically expose new physical capabilities.

## Current baseline inspected

- `forge-puppeteer` at `f629a15`: action/capability models, coarse resource locks,
  cue timeline, events, voice abstraction, mock CLI execution and a narrow SO-ARM
  embodiment. The SO-ARM embodiment receives a gesture-to-waypoint dictionary;
  there are no packaged device MCP providers or SDK-discovered catalogs yet.
- `stackchan-control-sdk` at `5b09c2a`: stock packet-based async StackChan API,
  separate synchronous USB serial request client, firmware command/completion
  protocol, probe CLI and NeoHEX support. Native animation remains gated.
- `soarm101-motion-sdk` at `cf97dfc`: explicit capability registry, pose,
  trajectory and sequence libraries, guarded SDK replay, CLI and broker/MCP.
  Documented constrained MCP tools do not include recorded trajectory or sequence
  replay. Experimental physical broker mode remains narrow and unqualified for
  general physical control.
- `forge-reachy` pins Conversation App
  `5ab905d018de8d88f07444ca57677b2ffbe762ca` and SDK `1.10.0rc5`.
  SDK RecordedMoves supplies list/get/play primitives for recorded assets.
- `forge-studios` already has a Puppeteer request boundary. Its current checkout
  has unrelated edits; preserve them and avoid implementation there until the
  downstream execution contract is stable.

Before implementation, reread each repo's AGENTS.md and required docs, obtain
organization development.md/architecture.md (not found in the local searches),
verify current GitHub main and open PRs, and inventory all local changes. The
attempt to inspect remote PR/main status was interrupted; it is not verified.
Do not assume these recorded heads remain current. Preserve the existing
untracked SO-ARM agent-run directory and unrelated Studios edits.

## Repository layout and ownership

Paths below are proposed additions, to be reconciled with existing modules.

### forge-puppeteer

- `robot_contracts.py`: public control, asset, action and provider protocols.
- `devices/stackchan.py`, `devices/reachy.py`, `devices/soarm101.py`: typed
  discovery and execution adapters. Optional SDK imports stay within providers.
- `catalogs.py`: discovered native assets plus authored behavior-pack manifests.
- `execution.py`: action registry, resource arbitration, ownership and lifecycle;
  integrate existing runtime/resources/events rather than creating parallel ones.
- `mcp/server.py`: common tool lifecycle and per-provider typed tool registration.
- `mcp/client.py`: deterministic MCP consumer used by script execution.
- `deployment.py`: dependency/asset lock validation and reproducible staging.
- Extend existing actions/performance/CLI with native actions, exact asset cues,
  compile/preflight, serve and deterministic run operations.
- `examples/robots/`: mock configurations, performance scripts and custom packs.
- `tests/robots/`: contracts, provider fakes, lifecycle and real stdio roundtrips.

Offer separately installable MCP/device extras and entry points. An operator can
run one robot server per process or explicitly configure several independent
devices. Never implicitly connect hardware on import or catalog inspection.

### stackchan-control-sdk

- Add an external-control facade over USB with typed controls, discovery/state,
  event routing and playback support; keep stock protocol compatibility explicit.
- Add a native capability/schema registry shared by SDK and CLI.
- Add validated native pose/sequence asset metadata and load/list interfaces.
- Add a general `stackchan` CLI; retain `stackchan-probe` for diagnostics.
- Keep SDK independent of Puppeteer and MCP dependencies.

### soarm101-motion-sdk

- Extend its existing capability registry and asset libraries with consistent
  catalog descriptors and replay lifecycle where needed.
- Add narrowly bounded discovery/replay broker capabilities with explicit profile
  grants. Reuse guarded playback, provenance and cancellation implementations.
- Keep the existing `soarm101-mcp` interoperable; Puppeteer's server delegates
  through the same broker rather than creating a physical controller.
- Update SDK documentation, TESTING, CHANGELOG and examples when contracts change.

### forge-reachy and forge-studios

No new Reachy SDK fork. Add integration documentation/example config to
forge-reachy only after its existing boundary is respected. Its stock conversation
path remains usable; running it alongside Puppeteer requires an explicit handoff,
not simultaneous independent writers.

Studios remains the authored-shot caller. Later wire explicit resolved cue/script
references into its existing Puppeteer dispatch and preserve take lineage. Do not
introduce a second episode format or interpret prose with an LLM in Studios.

## Shared contracts

Version contracts explicitly; keep serialization stable and JSON-safe.

### Device and control discovery

Device descriptor: provider/instance identity, SDK/firmware versions, connection
and readiness, supported operations, control descriptors, resource relationships,
feedback kind, effective limits and authority summary when applicable.

Each control descriptor includes stable ID, input schema, units, limits, effect
(read/visual/light/audio/motion/stop), resource claims and concurrent-operation
constraints. Keep native controls typed: head pose, antenna targets, joints,
gripper, face features, and lighting are different schemas.

Reported command/model state must be distinguished from encoder feedback and
independently measured settling. Never claim unsupported feedback is measured.
Import-time inspection is metadata only. Live discovery uses explicit connection
and current broker/device responses; cached metadata never authorizes execution.

### Asset discovery

Asset descriptor: namespaced ID, kind (pose/recording/sequence/behavior), source,
immutable revision/hash, description, duration if known, resources, parameter
schema, calibration/device compatibility and optional audio association.

- Reachy: call SDK `RecordedMoves.list_moves()` and get clip metadata. Preserve
  original IDs and include optional audio sidecars. No emotion enum in Puppeteer.
- StackChan: list actual configured native asset packs; do not pretend firmware
  exposes an emotion library. Keep firmware-gated playback unavailable.
- SO-ARM: enumerate PoseLibrary, TrajectoryLibrary and SequenceLibrary through
  approved discovery paths. Listing does not imply playback is permitted.
- Custom packs add descriptors and recipes; unknown IDs fail explicitly. Semantic
  aliases are optional authored pack data, never implicit random fallback.

### Action lifecycle

Action records include ID, caller/session, cue/take lineage, exact resolved
operation/asset revision, validated parameters, claimed resources, timestamps,
result/error, completion evidence and cancellation outcome.

States: accepted, queued, running, completed, failed, cancelled. Define which
states are supported by each backend. Never equate an SDK queue acknowledgment
with physical completion. Block duplicate execution with request IDs and payload
hashes for a documented retention period; conflicting reuse is an error. A timed
out/disconnected caller has an unknown result until reconciled, not permission to
retry physical movement automatically. Persist enough action identity to explain
restart behavior and explicitly fail/mark unknown unreconciled prior actions.

Validate before submission and again at execution when authority/state can change.
Cancellation remains pending until the device outcome is known; stop failure must
not be reported as cancelled/safe. Keep independent stop available while ordinary
execution is busy. All failures produce structured errors with bounded detail.

### Resource and control ownership

Discover resources such as head.pose, antennas.left/right, arm.motion, gripper,
face.eyes, face.mouth, lights.builtin/neohex and audio.output. Shared resource names
are scheduling concepts, not universal motor schemas.

Adapter constraints may serialize more broadly than resource separation suggests.
Initially reject conflicting work or queue it under an explicit policy. Add motion
blending only for known supported combinations. Idle behavior is opt-in and yields
to primary actions; scripted takes disable ambient behavior unless authored.

Acquire explicit performer control for a scripted take. Competing conversational
requests fail with busy/ownership detail or follow an explicit operator handoff.
Ownership acquisition never arms hardware or extends a device authority lease.
MCP stdio alone is not authentication; document trusted-local deployment and do
not add unauthenticated network exposure.

## MCP and deterministic script execution

Stable tools: device/state/control discovery, asset listing, action lookup,
asset playback, cancellation, device stop, and typed native operations.
Descriptions/schemas come from explicit adapter contracts and SDK metadata where
available. Tool availability is intersected with current broker grants and device
support. Refresh permission checks at execution, including after startup discovery.

Do not generate a separate tool for every motion. A playback tool accepts an asset
ID from the catalog. Paginate/filter catalogs and include revision identifiers so
an agent or script can detect stale references. Reject arbitrary filesystem paths,
Python imports, shell commands and generic method dispatch in caller payloads.

MCP calls submit complete bounded actions; local execution owns playback frames.
Use the supported MCP SDK version compatible with the selected dependencies and
test the actual wire protocol. Current SO-ARM MCP uses MCPServer from MCP v2;
do not assume older FastMCP examples are compatible without verification.

Compile all cues before motion: resolve performers, typed parameters, asset
revisions, resource conflicts, readiness and permissions; retain an immutable
compiled plan and catalog/dependency manifest. Explicit script choices remain
unchanged. Runtime validation still applies if state/authority changes afterward.

Schedule against a monotonic timeline. Define late-cue policies (fail, skip, or
explicit delay) and dependencies/barriers for completion-sensitive cues. Do not
assume a general stdio/network MCP call provides hard real-time start alignment.
Use prepare/start-at/local batches only where actually supported. Record requested
and observed timing and fail stated timing requirements that cannot be met.

The primary acceptance path uses MCP ClientSession against real stdio server
processes with fake devices, not solely direct Python calls. Continue testing the
existing timeline and semantic request contracts; preserve their behavior.

## Implementation milestones and acceptance

### M0 — contracts and clean baseline (Puppeteer)

Write schemas/protocols and example configs/scripts, reconcile the proposed model
with existing PerformanceAction, PerformanceStage and event models. Establish
software baseline checks and dependency compatibility. Make no physical changes.

Accept: fixtures represent every device's native controls and assets without
universal motor assumptions; catalog inspection opens no hardware.

### M1 — complete StackChan external-control SDK and CLI

Implement a single serial reader/event router so concurrent action observation
does not discard another request's events. Serialize writes and ordinary motion
as required; keep system.stop responsive. Explicitly handle connection closure,
malformed messages, firmware rejection and timeout. Avoid hidden reconnect/retry.

Expose native head/face/light controls with host validation and firmware capability
checks, state and result semantics. Bridge or clearly separate the stock packet
API from USB so callers cannot accidentally use the wrong transport protocol.
Add CLI discovery/state/native updates/stop and structured errors. Add validated
asset listing/loading and a conservative playback primitive; enable native
animation only after its separate validation gate. NeoHEX's refresh timeout must
be honored by any sustained lighting action, with explicit cancellation cleanup.

Accept: fake-serial tests cover event interleaving, rejection, stop during motion,
timeout/unknown outcome and reconnect policy; face-only controls cannot accidentally
move servos; existing probe/protocol tests pass. No firmware flashing required.

### M2 — SO-ARM asset discovery and bounded replay

Add read-only catalog APIs with source/provenance, deterministic ordering and
compatibility status. Extend broker profile schema, allowlisted routes, clients
and tests for replay only after defining pinned asset identity, allowed recordings,
calibration binding, speed ceilings, full sequence validation and start-state
requirements. Caller IDs resolve only within operator-approved libraries.

Reuse SDK play_trajectory/SequenceRunner, rate/effort/fault/workspace checks and
both arm/gripper stop paths. Ensure lease expiry/STOP invalidates queued work,
disallows late success and never re-arms/resumes. Include trajectory entry motion
and every sequence step in authorization, not just the named top-level asset.

Accept: simulation/fakes verify allowed and denied discovery/replay, mismatched
calibration, asset mutation, sequence limits, lease expiry and stop races. Existing
restricted physical modes remain restricted. Any physical replay enablement is a
separate attended qualification, not an automatic deployment consequence.

### M3 — Reachy provider and reproducible motion packs (Puppeteer)

Use the supported pinned SDK API to load/list/get/play moves and native controls.
Inspect its actual completion, cancellation, audio/wobble and daemon concurrency
semantics before promising common lifecycle guarantees. If a guarantee is missing,
expose that limitation or implement a bounded SDK-backed adapter; do not invent
completion from a queue acknowledgment or elapsed clip time alone.

Build a motion snapshot manifest with dataset revision, file hashes, SDK version,
source URL and license/notices. Verify local loading and audio paths; do not rely
on internal cache relocation. Add offline build/run checks. Retain upstream asset
IDs and add separate custom packs. Keep sound playback explicit per action.

Accept: two different fixture catalogs appear automatically without code changes;
unknown assets fail; locked/missing/mutated files fail reproducibly; no Conversation
App import, process or sibling checkout is required.

### M4 — shared runtime and packaged MCP servers (Puppeteer)

Implement lifecycle/action registry, resource arbitration, ownership and thin
provider adapters using the existing runtime foundations. Package a common server
factory plus device launch options. SO-ARM delegates through its existing broker;
preserve its camera image verification and native guidance if forwarding those
tools. Never expose a direct-arm bypass as a fallback.

Accept: CLI, Python and MCP exercise the same provider code. Real stdio roundtrips
test discovery, native controls, playback, status, errors, cancellation, independent
stop, denied authority and concurrent callers using fake hardware. Missing optional
dependencies give actionable errors without breaking unrelated providers.

### M5 — deterministic scripted MCP client and behaviors (Puppeteer)

Implement compile/preflight and execution against configured MCP subprocesses or
explicitly supported transports. Preserve exact cue/asset IDs, timeline, authority,
late policy, action results and take lineage. Own subprocess cleanup without shell
interpolation; restrict server configuration to operator-authored configuration.

Add initial data-driven StackChan head/face/light example packs, SO-ARM recording
fixtures and Reachy upstream packs. Add opt-in blink/idle and speech timing through
bounded local behavior execution. Reuse the voice abstraction; model aliases,
audio sample timing and device output stay explicit. No paid voice generation in
automated tests. Broader generative conversation applications remain separate.

Accept: one mock multi-device script runs through actual MCP servers with no LLM;
captures resolved assets and timing; rejects unsupported/conflicting/stale cues
before movement; handles mid-take failure and stop; conversational control is
denied while the scripted owner holds the performer. Existing mock CLI still works.

### M6 — packaging and downstream documentation

Build wheels/extras from clean environments using package dependencies rather
than relative filesystem imports or PYTHONPATH. If SDK versions are not published,
use explicit pinned Git commit dependencies for deployment, then promote released
versions deliberately. Avoid depending on a working checkout's uncommitted code.

Document install/build/asset staging, server/client configurations, supported and
unsupported operations, offline execution, authority setup and result semantics.
Record clean-install smoke evidence. Add Studios/forge-reachy integration examples
without changing their existing ownership or unrelated local work.

Accept: a fresh environment starts each selected mock-backed server, lists its
catalog and executes a deterministic example. Reachy assets load offline from
the staged deployment. SDK-only installs remain usable without Puppeteer/MCP.

### M7 — separately authorized physical acceptance and firmware distribution

After software checks, plan attended trials for each device: read-only discovery,
one bounded action, completion evidence, cancellation, failure and control handoff.
Do not claim physical acceptance from mock/simulation tests.

StackChan touchscreen exit is explicitly deferred until requested: source already
has a home indicator closing the app; investigate visibility/touch layering with
the enlarged face. Validate exit and existing service shutdown during idle/control.
Keep this separate from host SDK/MCP work.

Package prebuilt StackChan firmware and reproducible build inputs with hardware
compatibility, hashes, attribution, flash/restore instructions and evidence limits.
Host-loss/timeout qualification and gated native animation are explicit firmware
follow-ups; do not silently label them implemented by the server work.

## Validation and delivery

Use repository-prescribed checks. StackChan: pytest, ruff and mypy. Puppeteer:
existing tests plus new provider/runtime/MCP integration tests and package smoke.
SO-ARM: focused catalog/broker/profile/replay/STOP tests, simulation checks, then
required full software suite/coverage/lint. Exclude active hardware tests.

Deliver dependency-ordered changes: contracts; StackChan facade; SO-ARM catalog;
SO-ARM bounded replay; Reachy assets/provider; Puppeteer runtime/MCP; scripted
client; packaging/docs. Split large safety-sensitive broker work from generic
catalog work. Commit/push/PR actions follow user instruction and repository policy;
none are performed by this planning task.

Track each milestone as planned / implemented / software-verified /
physically-verified. Before handoff, record exact changed files, dependency pins,
test commands/results, known capability gaps and remaining physical gates.

## Upstream references

- Reachy SDK: https://github.com/pollen-robotics/reachy_mini
- Motion assets and license metadata:
  https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library
- Conversation App reference baseline:
  https://github.com/pollen-robotics/reachy_mini_conversation_app/tree/5ab905d018de8d88f07444ca57677b2ffbe762ca
- Community MCP reference:
  https://github.com/ArturSkowronski/reachy-mini-mcp

Verify licenses and API compatibility at the exact revisions selected during
implementation; current upstream main is not the pinned deployment contract.
