# Native conversational performers — operator-selected deployment

**Current software state:** Reachy-only (schema v2), Reachy + StackChan duo
(schema v3), and the original three-performer configuration (schema v1) all
use the same `forge-puppeteer-ensemble` executable, `ConversationEnsemble`,
`ExecutionRuntime`, journal, and SDK controllers. The v3 duo opens only
Reachy and StackChan; it does not import/construct a simulated or physical
SO-ARM101 SDK and does not require an SO-ARM neutral/calibration/program file.
Hardware qualification remains a separate attended requirement.

**Historical context:** The PR 4/5 architecture and its original v1
three-performer instructions below describe the initial implementation.

### Reachy + StackChan duo (schema v3)

Create a **review-only** operator candidate from existing pinned Reachy
recordings and a reviewed Reachy neutral head file:

```bash
python scripts/author_three_robot_ensemble.py \
  --reachy-stackchan-only \
  --reachy-manifest examples/robots/reachy/emotions.manifest.json \
  --reachy-recordings /absolute/path/to/pinned-reachy-assets \
  --reachy-neutral /absolute/path/to/reviewed-reachy-head.json \
  --stackchan-port /dev/serial/by-id/OPERATOR_SELECTED_STACKCHAN_PORT \
  --action-journal /absolute/path/to/ensemble-actions.sqlite3 \
  --ownership-db /absolute/path/to/ensemble-owners.sqlite3 \
  --output /absolute/path/to/reachy-stackchan-review.json
```

The script does not connect hardware, flash StackChan, start the Reachy
daemon, or grant motion. Its generated `motion_authority`, per-robot
`physical_authority`, Reachy daemon launcher/identity and other
`REVIEW_REQUIRED` values must be reviewed and replaced by the operator.
V3 requires the complete Reachy SDK controls, an actual StackChan USB port,
and the native StackChan SDK version explicitly installed in the worker.
The candidate uses SDK 0.4.0; firmware 0.3.0 still works for existing
expressions but cannot accept optional `display.face_scale` operations.

```bash
forge-puppeteer-ensemble --config /absolute/path/to/operator-approved-duo.json
```

Or let a signed Conversationalist LiveKit room start this **same** executable
by setting `PUPPETEER_ENSEMBLE_CONFIG` in its trusted worker environment.
Do not run the standalone launcher at the same time as the worker's child:
only one process may own each SDK/USB device. The Pollen Conversation App
must also remain stopped while Puppeteer owns Reachy.

The duo supports the same named presence, gestures, optional generated-PCM
preview, and operator-granted `ensemble_presentation` StackChan
`normal`/`enlarged` presets as v1. The optional display presets require
an installed StackChan SDK 0.4.0, actual compatible firmware, and explicit
`face_scale_update` grant; they never activate automatically. No actual
speech-reactive hardware actuation, head/antenna settling, or shutdown sleep
is qualified by software tests.

The original v1 mode is unchanged and retains its seven SHA-pinned SO-ARM
programs, watchdog, human lease rules and simulation default.


### Operator-selected motion profiles (PR #40 software)

All schemas v1 (Reachy, StackChan, SO-ARM), v2 (Reachy only) and v3
(Reachy + StackChan) accept optional `motion_profiles`, with independently
selected `idle` and `speaking` values of `legacy` (default),
`native_v1` or `smooth_v2`. They may select only **existing reviewed**
authoring packs; no arbitrary actuator targets, new SDK connection or
auto-fallback is allowed.

A **review-only example** for the v3 duo, to be merged into an otherwise
complete operator-reviewed config:

```json
{
  "reachy_neutral_antennas_rad": [-0.17, 0.17],
  "speech_driven_performers": ["reachy", "stackchan"],
  "motion_profiles": {
    "reachy": {"idle": "native_v1", "speaking": "smooth_v2"},
    "stackchan": {"idle": "legacy", "speaking": "smooth_v2"}
  }
}
```

The Reachy selection requires the explicit `pose_and_antennas` grant and
physically reviewed antenna neutrals; these numeric examples are **not**
operator permission or recommended physical calibration. StackChan
`native_v1` speaking uses native mouth WEIGHT and `smooth_v2` adds
content-free latest-hop smoothing with a single silent release. Unknown
performers or profile names, unauthorized Reachy expressive motion, duplicate
speech performer IDs, and SO-ARM audio-reactive streaming all fail closed.
`smooth_v2` speaking requires that performer in
`speech_driven_performers`, not merely an LLM expression request.

These selections remain software-only until physical approval. Their motion
samples are upstream-inspired, **not exact Reachy raw PCM output**; the
generated-PCM clock is not a measured speaker clock. See
[PR #40 acceptance](PR40_MOTION_ENGINE_COMPLETION.md) and
[continuity contract](MOTION_CONTINUITY_IMPLEMENTATION.md).

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
`listening_mode: "hold"` selects a no-command listening state instead of
repeating a tilt. After the preceding transition finishes, it sends no motion
commands while listening; it does not independently hold motors, establish
physical settling, or bypass an unconfirmed-stop quarantine. The default is
`"tilt"` for compatibility.
An ordinary presence change allows an already-dispatched finite Reachy SDK
segment up to 1.5 seconds to finish before switching patterns. Explicit
interruption, disconnect, expiry and shutdown still use immediate STOP; the
SDK cannot confirm physical STOP for an in-flight native head target, so an
unconfirmed outcome quarantines Reachy until operator reconciliation.
An admitted finite gesture runs to completion across ordinary voice-state
changes and then resumes the latest presence. A real interruption,
disconnection, expiry or shutdown still preempts it immediately and retains
the existing unknown-outcome quarantine if STOP cannot be confirmed.

## Experimental Reachy expressive profile (merged, hardware pending)

PR #16 (merge `c83efcbbf470f9f72052d2090398f1092e6670dd`) adds
`reachy_expression_style: "expressive"` for both launcher schemas.
It requires a reviewed `reachy_neutral_antennas_rad` pair and an explicit
Reachy `pose_and_antennas` operation grant. Its lead head-motion
amplitude defaults to 0.09 radians (about 5.2 degrees), antenna sway to 0.18 radians, and
vertical breathing displacement remains 0 m until separately qualified.
The existing conservative `classic` style stays the default and the
official pinned recorded yes/no are unchanged.

The compound head + antennas cue enters the same ExecutionRuntime, and
the same pinned SDK `goto_target`, as existing motion. It does not
create a new motor scheduler, grant raw LLM servo commands, or independently
confirm physical STOP. **The code is merged into `main`, but the expressive
motion envelope is not physically qualified.** See
[Reachy expressive motion](REACHY_EXPRESSIVE_MOTION.md).

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


## Per-performer behavior discovery for Conversationalist

The existing read-only `ensemble_state` MCP response now includes the
operator-authored catalog for **each configured performer**:

- `behavior_revision`: SHA-256 content revision of that performer's pack.
- `expressions`: sorted names of finite, one-shot gestures present in the
  pack. Reachy may include `yes`, `no`, `acknowledge`, `neutral` and
  optional `happy`, `curious`, `confused`, `excited` when corresponding
  verified recordings exist. StackChan and SO-ARM have their own mappings.
- `presence_behaviors`: automatic modes available to this performer,
  among `idle`, `listening` and `speaking`.

These fields are **discovery, not permission**. UI clients must use the
actual returned catalog, not assume that every device supports every action;
they must not offer ambient loops as button-triggered expressions. A button
request passes an exact selected performer ID through the existing
`ensemble_expression` tool, subject to room ownership, the native SDK,
operator approval, unavailable/busy/quarantine state and exact asset pins.
An `accepted` response proves dispatch admission, **not physical completion**.

This addition is software-only: the unchanged SDK motion paths, current
conservative amplitudes and incomplete physical qualification still apply.
