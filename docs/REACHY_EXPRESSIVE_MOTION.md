# Reachy Mini expressive motion — hardware qualification plan

**Status:** Software implementation merged into `main` via PR #16,
commit `c83efcbbf470f9f72052d2090398f1092e6670dd`.
This document is **not** evidence of physical qualification or safe motion;
operator-reviewed hardware validation remains outstanding.

## Scope and architecture

Puppeteer authors a `forge.behavior.v1` style for native Reachy Mini:
`classic` remains the default, preserving the original small single-axis
head cues and recorded yes/no/acknowledge gestures. `expressive` is opt-in
for both the Reachy-only (`schema_version: 2`) and three-performer
(`schema_version: 1`) operator-reviewed launcher configurations.

The expressive style uses the **existing** Reachy Mini SDK 1.10.0rc5
`goto_target(head=..., antennas=..., duration=..., body_yaw=None)` method
for atomic head/antenna interpolation, not a copied Conversation App runtime
or a second motor scheduler. The Puppeteer `ExecutionRuntime` remains
responsible for action identity, serialization, resource arbitration and STOP
escalation; the native SDK validates and executes each finite target.

The reusable adapter operation `pose_and_antennas` accepts only:
- an orthonormal, right-handed, bounded 4x4 head pose;
- two bounded antenna angles in radians (right, left);
- an SDK-governed duration from 0.05–5 s.

`ReachySDKProvider.validate` now enforces the operator's explicit grants
before forwarding native motions to the existing adapter. The new operation
is not exposed as an arbitrary public MCP tool or an LLM-granted raw motion
command. Recorded gestures remain exact SHA-verified catalog assets with
associated audio disabled; yes/no are unchanged.

## Operator settings

### Adapter-owned daemon and startup wake-up

Local Reachy SDK deployments may configure the generic Puppeteer daemon owner
with an explicit argument vector, working directory, readiness timeout and
optional log file. It reuses an available endpoint, starts the configured
process only when absent, checks endpoint and SDK-reported hardware identity,
and keeps a child daemon running after a normal room shutdown by default. It
does not kill an existing process, restart a failed process or infer hardware
calibration. Remote daemons remain supervised at their device host.

The optional `reachy_startup_behavior: "wake_up_if_sleeping"` adds one
Puppeteer-owned startup action to the behavior pack. It runs on the first
presence event in the room, not when the daemon starts, and requires an
operator-granted `wake_up` operation. The adapter uses the pinned SDK's own
sleep-pose check and `robot.wake_up()` sequence. Select `"wake_up"` when the
operator wants the same SDK sequence to run even when the current pose is
outside the SDK sleep-pose tolerance. Both modes use the SDK-defined startup
pose, not a pose inferred at connection time. With Puppeteer's
`media_backend="no_media"`, the SDK wake-up sound is unavailable; head and
antenna movement are still handled by the SDK sequence. When the sequence
returns, Puppeteer samples Reachy's present head transform and antenna joint
positions. That per-session sample is the neutral target; it is based on where
the robot ended startup, not a guessed or connection-time pose. Sampling does
not independently prove physical settling. The pinned SDK startup sequence
also targets body yaw 0.0 rad; Puppeteer retains that target because recorded
expressions can change body yaw and `None` would leave it there.

`reachy_return_to_neutral_after_expression: true` authors a `neutral_behavior`
in the performer's behavior pack and requires
`reachy_startup_behavior: "wake_up"` plus explicit `wake_up` and
`pose_and_antennas` grants in the reviewed Reachy tool allowlist. The combined
target grant remains a configuration capability for the internal authored
neutral behavior; it does not register a raw-target MCP function.
Puppeteer's shared conversation
state machine runs each finite expression, runs that performer's neutral
behavior, then resumes the latest semantic presence state. Motion-bearing
presence changes and confirmed interruptions also pass through that authored
neutral. A manual expression first returns active motion to neutral and then
uses the same expression/neutral/resume sequence. The neutral step has its own
journal action and action ID under the same exclusive performer
owner. Reachy's adapter maps the fixed `return_to_neutral` operation to the
head and antenna feedback sampled after startup plus the SDK startup body-yaw
target; callers cannot provide raw targets. Other performers can author their
own neutral behavior and use the same state-machine transition. SDK completion
remains distinct from measured physical settling. With this option, zero-breath
classic idle is a hold.

For the pinned Reachy SDK 1.10.0rc5, `ReachyMini.goto_target()` waits for the
daemon's `TaskProgress.finished` event. The daemon emits that event after its
time-based `GotoMove` loop returns; that loop submits interpolated targets on
each tick and does not wait for measured joints to settle at the endpoint.
Therefore an action record marked `completed` proves the SDK task loop ended,
not that the head, antennas or body yaw reached the neutral target. A physical
neutral claim requires a separate measured-pose convergence check with a
reviewed tolerance and a clear interruption outcome. The adapter currently
records this distinction and does not independently qualify settling.

Keep daemon startup wake disabled (`--no-wake-up-on-start`) so only the
Puppeteer adapter owns the startup movement. A missing or incompatible daemon
fails readiness checks before the performer is exposed. Do not enable the
startup option until its motion authority and SDK sleep-state behavior have
been reviewed for the connected robot.

Merge these fields into an existing, reviewed native ensemble JSON
configuration (do **not** replace its identity, authorization or SDK fields):

```json
{
  "reachy_expression_style": "expressive",
  "reachy_neutral_antennas_rad": [-0.17, 0.17],
  "reachy_expressive_amplitude_rad": 0.09,
  "reachy_antenna_sway_rad": 0.18,
  "reachy_breath_amplitude_m": 0.0
}
```

The example antenna values are **illustrative only**, not a universal
calibration and not an instruction to send these values to any robot.
The operator must read the currently configured Reachy state, approve the
antenna neutral and assess collision-free travel before enabling physical
movement. The Reachy device's `allowed_tools` must explicitly include
`pose_and_antennas` in addition to the existing
`head_pose`, `play_move` and `device_stop` grants.
Otherwise expressive startup is rejected.

By default, vertical breathing displacement stays **zero**, even in
expressive mode. The operator may authorize a value from 0–0.005 m only
after independently qualifying the neutral head transform, physical Z axis
and clearance. Larger amplitudes are not equivalent to safe amplitudes:
SDK and mechanism limits are authoritative over Puppeteer bounds.

The suggested expressive defaults use up to 0.09 radians (approximately
5.2 degrees) in the lead head axis and 0.18 radians antenna sway around an explicitly reviewed
neutral. Coordinated minor yaw and roll offsets produce more visible,
less repetitive talking movement. Patterns interpolate at 0.6 s steps;
they are **not** connected to outgoing audio volume, phonemes or actual
speaker playout time. The current Conversationalist presence callback is
approximate. Those audio-driven signals require a separate change in the
LiveKit-owned media boundary.

Listening continues to support `listening_mode: "hold"`, which dispatches
no new motion. Both profiles retain exact recorded yes/no when the verified
catalog contains the pinned revisions; normal transitions allow a bounded
finite segment to complete, while interruption and disconnect invoke the
existing uncertain STOP/quarantine path. Quarantine is not automatically
cleared or treated as a confirmed motor stop.

## Offline CI and local Codex acceptance

From the branch's exact committed SHA:

```bash
python -m pip install -e '.[dev,mcp,reachy]'
python -m pip install 'soarm101-motion-sdk @ git+https://github.com/AgenticForge-Labs/soarm101-motion-sdk.git@60589ef56e02084ff53e432771d0cc7b7d257e1c'
python -m pip install 'stackchan-control[serial] @ git+https://github.com/AgenticForge-Labs/stackchan-control-sdk.git@391ccb9b5ff777fbd88df6a97e2477161a34c7ec'
pytest -q tests/robots/test_reachy_expressive_motion.py tests/robots/test_conversation_native.py tests/robots/test_ensemble_launcher_config.py tests/robots/test_conversation_native_integration.py
pytest -q
ruff check src tests
```

Hardware work should be attended, incremental and reversible:

1. Synchronize exact Puppeteer branch SHA, pinned SDK and immutable Reachy
   recording snapshot. Confirm *only Puppeteer* owns the robot's motion
   daemon; stop any stock Conversation App motion owner.
2. Capture operator-reviewed head neutral and antenna neutral **from the
   actual robot and calibration**, including joint/power status. Do not use
   illustrative values without review.
3. Establish a non-moving baseline. Test `classic` yes/no and verify prior
   behavior and recorded-move IDs before enabling expressive mode.
4. In `expressive`, begin with a reduced amplitude/sway, verify clearance
   through the entire cycle, and only increase toward the proposed values
   if the physical robot behaves smoothly. Do not begin with maximum bounds.
5. Observe speaking, listening, idle, a neutral return and a named recorded
   gesture. Film movement and retain the action journal; note actual vs.
   intended amplitude, acceleration/jerk impression, responsiveness,
   oscillations, drift and whether antenna movements remain synchronized
   with the head.
6. Test ordinary voice-state transitions separately from an intentional
   interruption. Confirm no unexpected motion command after disconnect or
   SDK error; **do not demand a dangerous live fault test**. Use an SDK fake
   to exercise unconfirmed STOP, and treat a real unconfirmed stop as a
   terminal quarantine requiring operator reconciliation.
7. Compare video side-by-side with the stock Conversation App's motion in
   an **exclusive** separate session, never concurrently.
8. Report exact SHA, test commands, logs/video, qualified envelope and
   outstanding safety or timing problems. Correct this same PR before merge.

**Pass criteria:** existing yes/no preserved; approved multi-axis + antenna
gestures are perceptibly stronger, smooth and bounded across transitions;
no unapproved motion, duplicate controller or implicit rearm; evidence
is recorded and physical behavior is confirmed rather than inferred from
SDK completion.

## Next dependent phase

The pinned Reachy SDK 1.10.0rc5 includes `motion/speech_tapper.py` and
`motion/head_wobbler.py`. `SwayRollRT` analyzes mono float32 PCM using a 20 ms
RMS frame every 50 ms, a VAD threshold with hysteresis and attack/release, and
a clipped loudness envelope. Its oscillator offsets are scaled by that
envelope. `HeadWobbler` accepts a monotonic playback-start timestamp, schedules
the offsets on 50 ms hops, invalidates pending callbacks by generation on stop,
and emits zero offsets. The local GStreamer path feeds it from a `sync=True`
appsink at the buffer PTS and timestamps the callback with
`time.monotonic_ns()`; this is local pipeline scheduling, not a measurement of
sound at a browser speaker. The current `ReachySDKProvider` uses
`media_backend="no_media"`, so the SDK audio path is not active.

Do not call `ReachyMini.enable_wobbling()` from Puppeteer: it writes speech
offset commands outside `ExecutionRuntime`, and SDK daemon-side audio may
create a second motion source. Reuse the tapper's bounded analysis only behind
Puppeteer's single performer owner, with SDK targets submitted through the
existing adapter and runtime. Keep a single long-lived, interruptible action
for the envelope stream instead of one journaled action per audio frame.

The installed LiveKit `AudioOutput` exposes `PlaybackStartedEvent(created_at)`,
`PlaybackProgressedEvent(started_at, offset, duration)` and
`PlaybackFinishedEvent(playback_position, interrupted, synchronized_transcript)`.
The Conversationalist currently subscribes to start/finish only. These server
output events describe its track queue and playout accounting; they do not
contain PCM loudness or prove browser acoustic playout. The exact next PR is in
`forge-conversationalist`, titled **“Expose bounded outgoing speech envelope
for Puppeteer”**: add an opt-in, bounded outgoing-PCM loudness envelope
to the existing output integration, with sequence, sample interval and honest
monotonic timing metadata, while preserving the existing one-sink audio path
and interruption semantics. Its follow-up Puppeteer PR should consume only
that authenticated envelope, apply rate and amplitude bounds, shape it through
the reusable speech behavior, and report action evidence. Browser speaker
timing remains estimated until measured against browser output timestamps.

Neither PR should enable audio-reactive motion by default or on hardware before
the speaker-to-motion delay, stop behavior and safe envelope are measured.
