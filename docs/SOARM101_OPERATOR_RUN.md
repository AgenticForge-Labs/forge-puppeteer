# SO-ARM101 operator run and broker boundary

## Current startup

Puppeteer's three-performer room owns one persistent SO-ARM101 SDK session and
the existing SDK broker watchdog in the same process as `ExecutionRuntime`.
The room sends only authored, SHA-pinned semantic choreography to that
session. It does not use the HTTP agent broker as a second controller.

Validate an operator configuration without starting SDKs, opening serial
ports, or checking motion authority:

```bash
forge-puppeteer-ensemble --config /absolute/path/to/operator-approved.json --check-config
```

The JSON result says which config schema and performers were validated. It
explicitly reports that device connections and live authority were not
checked. Start the room only after reviewing the file:

```bash
forge-puppeteer-ensemble --config /absolute/path/to/operator-approved.json
```

The v1 three-performer config defaults SO-ARM101 to simulation. Its autonomous
agent physical mode requires the explicit `attended_physical_trial` setting,
selected port, calibration identity, operator authorization marker, and active
human lease. The operator must already own torque HOLD. Startup does not arm,
enable torque, or make a physical readiness claim. The schema-v4 room can
instead own an SDK physical daemon configured with
`operator_choreography_only: true`; this mode does not require or manage an
agent lease, denies agent state changes, and admits only exact pinned
choreography through its authenticated trusted-host route. Existing-broker
discovery remains read-only and does not grant that route or authorize motion.
See [Three robot ensemble](THREE_ROBOT_ENSEMBLE.md) for the config and room
lifecycle.

## Session ownership

Legacy rooms use an in-process SDK executor. V4 may instead own one SDK
physical daemon and use its authenticated trusted-host choreography route.
Do not start a direct broker or another room against the same arm concurrently.
A separately selected direct broker profile retains the SDK's agent permissions
and lease rules; operator-only choreography mode denies agent state changes.
V4 physical choreography needs an exact `startup_target_revision` bound to the
catalog's `wake_to_neutral` target, with `physical: true` on the room. The room first
checks the arm is at its sleep pose. The SDK plans the pinned target from measured
current positions through the same watchdog and STOP authority.

The physical daemon also supports an explicit `operator_choreography_only: true`
setting in its private physical-daemon config. This mode exposes only health,
capabilities, state, and STOP to agent clients; the SDK executor independently
rejects every state-changing agent action, even if a broker profile is misconfigured.
Exact pinned choreography remains available only through the separate
authenticated trusted-host route. This mode creates no agent lease. Its watchdog
checks the active SDK session and bound robot/calibration identity, while the
daemon still requires the same read-only identity and stationary torque-HOLD
preflight. Omit the setting or set it to `false` to retain the existing
human-lease-gated autonomous agent mode.

This guide and `--check-config` are software-only. They do not qualify physical
clearance, sensed neutral, settling, torque HOLD, or STOP behavior.

## Local dependency and profile handoff

Build matching wheels from the selected source checkouts, then install both in
one environment with Puppeteer's MCP extra. Check actual exported symbols as
well as version strings, without opening a broker:

```bash
python -m pip wheel --no-deps -w /tmp/soarm-wheels ../soarm101-motion-sdk .
python -m venv /tmp/soarm-runtime
/tmp/soarm-runtime/bin/pip install /tmp/soarm-wheels/soarm101_motion_sdk-*.whl '/tmp/soarm-wheels/forge_puppeteer-0.1.0-py3-none-any.whl[mcp]'
PATH=/tmp/soarm-runtime/bin:$PATH /tmp/soarm-runtime/bin/python scripts/soarm101-dependency-preflight.py
```

For a separately owned broker with an operator-reviewed direct profile, the
launcher is `/tmp/soarm-runtime/bin/forge-puppeteer-mcp --config
/absolute/path/to/direct-profile.json`. Stop and reconcile the room-owned daemon
before selecting that profile; broker grants, leases and live capabilities still
apply. The room launcher is `/tmp/soarm-runtime/bin/forge-puppeteer-ensemble
--config /absolute/path/to/reviewed-room.json`. Do not launch it until its catalog,
physical daemon identity, neutral prerequisite, grants and clearance have been
reviewed. See `SOARM_POSE_GESTURES.md` for producing review candidates.

## Completed local software handoff

The matching Python 3.12 wheel runtime is installed at
`~/.local/share/forge-puppeteer/soarm-demo-runtime`; its wheels are retained in
`~/.local/share/forge-puppeteer/soarm-demo-wheels`.
The separate `~/.config/forge-puppeteer/soarm101/demo-simulation-final/`
configuration uses the SDK simulator, a pinned wake target and authored
idle/speaking/yes programs. It opens no physical robot. Run its offline
Conversationalist-client check with:

```bash
~/.config/forge-puppeteer/soarm101/demo-simulation-final/run-ensemble-check
```

That check verifies new journal entries from the current invocation and
confirmed room shutdown. To use the existing voice application with the
simulated arm, run `run-voice-simulation` in the same directory. It selects
the simulation room through process environment overrides; existing voice
credentials stay in Conversationalist's private environment. It does not
change the active `.env.local` or launch a physical daemon. Voice service,
microphone and playback acceptance are separate from the offline check.

The physical configuration candidate is separately prepared in
`~/.config/forge-puppeteer/soarm101/demo-physical-final-review/`, including
workstation-derived serial/identity configuration and a pinned neutral
startup target. It remains a review candidate. Its separately authored
breathing cycle takes 10 seconds with 4-second entry and return (18 seconds
total); the nod takes 20 seconds. These preserve the taught endpoints and
the SDK's 8 degrees/second policy. They do not replace or approve the
original 19-second routine or its five-second breathing cycles. The
idle and listening aliases use the breathing candidate. Speaking has a
separate source-authored 2.4-second wrist-roll cycle, one degree around the
taught neutral. This separate candidate also requires physical review.

Conversationalist starts Puppeteer's ensemble MCP service, which may own the
configured SDK daemon. This application path does not require the separate
interactive `soarm101` MCP server to be attached to the coding session.
Launching the configured service is supported application startup, not direct
shell-driven motor control. Device admission and motion still pass through
the existing SDK/broker. Do not bypass a rejected SDK readiness or motion check.

The operator subsequently authorized the physical demo and its pinned startup.
The physical configuration check passed and the application-owned launcher
attempted the hardware connection. Its SDK preflight refused startup because
healthy stationary torque HOLD was not established. No semantic motion request
was dispatched. The operator must establish readiness through the normal
hardware controls before a new explicitly requested session can proceed;
the default startup does not enable torque, create an agent lease, or retry a failed motion.

An operator can instead explicitly select `establish_hold_on_startup: true`
in the private SDK physical-daemon file with `operator_choreography_only: true`.
The SDK then checks the bound identity, calibration and stationary health,
starts watchdog supervision, latches current measured motor positions using
its existing `enable` primitive, and verifies bounded encoder stability before
advertising readiness. Existing HOLD is verified without another torque latch.
The pinned wake-to-neutral remains a separate subsequent action. This startup
grant is not an agent lease or agent-accessible motor operation. An uncertain
latch faults the session and writes `startup-hold-failure.json`; subsequent
startup refuses to reconnect until explicit operator reconciliation.
The attended startup test established torque HOLD with 0.0 degrees of sampled
joint drift. Its subsequent wake request exposed two operator-mode integration
bugs: the trusted execution helper still requested an agent lease and broker
STOP incorrectly used the read-only-preview admission gate. Both are corrected
and covered by the SDK daemon test using an injected simulation backend.
The failed physical wake remains unknown in its journal; no automatic retake
or rearm is permitted. Observed reconciliation is separate from the initial
startup authorization. A private `operator-reconciliation-required.json`
marker also blocks daemon startup after an unresolved physical outcome.

After the operator observed and reconciled that exact unknown action, the
corrected attended run verified existing HOLD without relatching, and
wake-to-neutral completed on hardware. Idle admission rejected a measured
elbow entry error of approximately 1.23 degrees against the unchanged
0.02-radian (approximately 1.15-degree) contract. SDK STOP subsequently
confirmed HOLD with approximately 0.44 degrees maximum sampled drift.
The physical daemon now tightens ordinary motion completion tolerance to the
policy's entry tolerance; the target operation also verifies that contract
before reporting completion. Admission limits are not widened and no motion
is automatically retried. A stopped coordinator releases idle ownership only
when its runtime's confirmed STOP has cleared all reserved activity, while
that coordinator's blocked performer remains blocked.

Installed-package failure-boundary acceptance can be rerun from Puppeteer:

```bash
SOARM_TEST_PYTHON="$HOME/.local/share/forge-puppeteer/soarm-demo-runtime/bin/python" \
  /tmp/soarm-demo-wheel-venv/bin/python -m pytest \
  tests/robots/test_soarm_installed_acceptance.py -q
```

The ten cases cover startup, normal transition coalescing, queued gesture,
interrupt/disconnect, heartbeat expiry, foreign ownership, lost STOP receipt,
and admitted broad control versus operator-only denial. The SDK simulator
advances virtual time, so long in-flight checks inject a delayed completion
receipt; the lost-STOP case discards the receipt after real simulator STOP.
Those are transport/lifecycle tests, not measured physical stopping evidence.

The operator-selected medium profile is 50 degrees/s and 500 degrees/s².
Set both the physical daemon's `joint_speed_deg_s` / `joint_acceleration_deg_s2`
and the catalog policy ceilings; choreography still needs complete SDK path
validation. `target_timing: native` uses the SDK planner at those rates for
pinned targets, while the duration policy remains a deadline. Named taught
targets can be exposed with `pose_targets`; they are finite requested actions,
never automatic presence loops. The attended sleep2 parking run completed
through this exact pinned-target MCP path with the gripper unchanged.

Conversationalist selects 4G with `CONVERSATIONALIST_PERSONALITY=4g` in its own
environment. The current operator mapping uses taught `confused_pup` for the
finite `curious` gesture. Idle and speaking remain room-owned presence
behaviors, and yes/no/curious run only when requested.
