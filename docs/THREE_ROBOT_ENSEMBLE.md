# Three native performers — PR 4 of 5

**Status:** Software integration on the PR 4 branch; verify the pull request merge status before treating this as current main behavior. Hardware validation is outstanding.

## Architecture

```text
Conversationalist (PR 5; one 4G LLM persona and tool grants)
  -> single Puppeteer ensemble MCP room
     -> ConversationEnsemble -> existing ExecutionRuntime
        -> Reachy Mini native SDK (pinned recording library, head poses)
        -> StackChan native SDK (face, head, lights)
        -> SO-ARM101 native SDK executor (approved joint-only choreography)
             -> independent host BrokerWatchdog and canonical STOP/HOLD
```

Puppeteer owns named behavior definitions and all coordination; SDKs own
movement validation and device safety. 4G is a conversation personality,
**not** a robot SDK personality. All three embodiments have exactly one
shared semantic conversation, and Puppeteer remains fully standalone.

The new `forge-puppeteer-ensemble` executable exposes only
`ensemble_state` (read-only), `ensemble_presence` (ordered
idle/listening/speaking/disconnected), and `ensemble_expression` (finite
yes/no/acknowledge/neutral). There are no exposed raw servo controls,
calibration settings, arbitrary operation names, or model-defined grants.
Events fan out as best-effort concurrent requests, not measured
sample-synchronous physical movements.

## Operator approval and configuration

The launcher loads an operator-owned JSON file, with exact SDK versions,
host/port settings, SHA-pinned SO-ARM101 programs, reviewed Reachy neutral
head matrix, and separate SQLite action journal and ownership database.
The room itself requires the explicit
`I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION` authorization marker.

Use the review-only generator to create candidate simulation configuration:

```bash
python scripts/author_three_robot_ensemble.py \
  --reachy-manifest examples/robots/reachy/emotions.manifest.json \
  --reachy-recordings /absolute/path/to/pinned-reachy-assets \
  --reachy-neutral /absolute/path/to/reviewed-reachy-head.json \
  --soarm-neutral /absolute/path/to/overhead-five-joint-radians.json \
  --stackchan-port /dev/ttyACM0 \
  --action-journal /absolute/path/to/ensemble-actions.sqlite3 \
  --ownership-db /absolute/path/to/ensemble-owners.sqlite3 \
  --output /absolute/path/to/ensemble-review.json
```

The generator never contacts hardware or authorizes motion. It
deliberately writes `REVIEW_REQUIRED` placeholders that prevent
launching before operator approval. Every SO-ARM program's five-joint
target, relative timestamp, robot identity, calibration and SHA-256
revision must match its separate approved list. The SDK rechecks the
full trajectory against measured current position and workspace
before execution. No arbitrary agent requests can turn candidate
programs into approved motions.

Exact SDK source refs for this PR:
- SOARM SDK `60589ef56e02084ff53e432771d0cc7b7d257e1c`
- StackChan SDK `391ccb9b5ff777fbd88df6a97e2477161a34c7ec`
- Reachy SDK `1.10.0rc5`, using the checked-in official Pollen
  recording manifest and the explicit SHA-verified importer from PR 2.

Install standalone dependencies:

```bash
python -m pip install -e '.[mcp,reachy]'
python -m pip install 'soarm101-motion-sdk @ git+https://github.com/AgenticForge-Labs/soarm101-motion-sdk.git@60589ef56e02084ff53e432771d0cc7b7d257e1c'
python -m pip install 'stackchan-control[serial] @ git+https://github.com/AgenticForge-Labs/stackchan-control-sdk.git@391ccb9b5ff777fbd88df6a97e2477161a34c7ec'
forge-puppeteer-ensemble --config /absolute/path/to/operator-reviewed-ensemble.json
```

No automatic Pollen Conversation App is launched. It must not
control Reachy's motion concurrently with Puppeteer.

### Reachy-only attended deployment

For a session with only Reachy, use the same launcher with `schema_version: 2`
and omit `stackchan` and `soarm101`. Supply the same `motion_authority`,
`reachy`, `reachy_neutral_head`, `action_journal` and `ownership_db` fields as
the three-performer configuration. The Reachy SDK identity, explicit motion
grant, reviewed neutral transform and pinned recording catalog are still
required. No other robot SDK session is started. This does not authorize or
qualify physical motion; the operator must review each behavior separately.
Optional `speaking_axis` (`yaw` or `tilt`), `speaking_amplitude_rad` and
`listening_tilt_rad` select bounded motion; each amplitude is at most 0.07
radians. Defaults preserve the existing behavior. Choosing a larger value or
axis requires separate attended physical review; configuration is not proof of
physical qualification.

## SO-ARM101 supervised physical trial

The default SO-ARM launch mode is SDK simulation. A physical trial
must explicitly change to `attended_physical_trial`, provide the
real port, real calibration identity, trusted host human-lease file,
`I_AUTHORIZE_ATTENDED_PHYSICAL_CHOREOGRAPHY` marker, already-enabled
human torque HOLD and the reviewed program hash list. Native SDK
`SDKAgentExecutor` owns its one physical connection, independent
host `BrokerWatchdog`, joint-only execution and STOP/HOLD. The
untrusted agent ±2° jog envelope is unaffected and no separate
agent-facing choreography route is created.

The operator-taught Overhead pose is not a universal home. Candidate
Puppeteer routines include a zero-default 0.1 Hz wrist-flex sine,
listening lift/wrist-roll, talking wrist wag, wrist-flex nod and
wrist-roll shake. **Amplitude defaults are zero** pending physical
validation. They are not guaranteed world-vertical movements.

Canceling an in-flight SO-ARM command can invoke a terminal SDK STOP:
that performer may become quarantined until the operator reconciles
the device. The other two performers continue independently.
Puppeteer must not auto-restart a quarantined arm or treat a software
STOP as certified braking; earlier hardware traces documented
~18.4° post-STOP elbow travel in one trial.

## Test and completion status

CI covers Python 3.11 and 3.12 with SDK-backed simulated SO-ARM
execution and mocked-native Reachy and StackChan, operator config
and SHA denial tests, and Ruff on changed implementation. It
does **not** validate the physical robots, measured speech timing,
actual gesture smoothness or voice/LLM tool use.

PR 5 adds the Conversationalist 4G presence and expression bridge
to this exact MCP room. Then a single local Codex handoff validates
real robot behavior, safety, audio and final system operation.
