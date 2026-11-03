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

After this branch is physically qualified and merged, add fine-grained
outgoing audio/playout timing at the LiveKit boundary, use the pinned
Reachy `speech_tapper` algorithm or compatible SDK primitive, and compose
audio-driven offsets under the **same** motion owner. Do not run a second
controller or publish a raw-target LLM tool to accomplish lip-sync.
