# SO-ARM taught-pose gesture authoring

`forge_puppeteer.soarm_pose_assets` reads the operator's explicitly selected
SO-ARM `PoseLibrary` path and imports the nine named `*_pup` poses into a
separate content-revisioned asset document. The source library is read-only to
this flow. Each imported item carries robot and calibration identity plus its
capture provenance; mismatched source or target identity fails closed. Do not
commit the resulting operator data.

`GestureTiming()` records the current authoring defaults: breathe cycles are
5 seconds, rise occupies 60%, and three loops; the operator may choose bounded
timings and 1–10 loops. Entry and return are separate authored transitions.
Yes should be authored as neutral entry, up entry, up→down→up cycles (1 second
for the complete cycle, or 0.5 seconds per half, 2 loops by default), then a
distinct neutral return. Breathe is authored and played once; it is not a loop.
All generated motion is five-joint only; gripper values are deliberately absent.

The installed SDK accepts only exact revision-pinned `JointChoreography`
programs. Therefore arbitrary timing/loop values cannot be accepted as a
runtime gesture parameter. Author each chosen configuration into finite
candidate programs, inspect and pin their revisions in trusted operator
configuration, then let SDK whole-path preflight validate speed, acceleration,
limits, measured start, calibration and authority. Infeasible SDK paths fail;
do not slow them by resampling or split them into chained small commands.
These authoring helpers do not dispatch motion or grant authority. Existing
SO-ARM execution scheduler remains the dispatch path. The legacy in-process
backend and v4 both support an optional pinned wake target. V4's private SDK
catalog carries the exact `startup_target`, and the robot file pins its
`startup_target_revision`; physical startup also requires its explicit
startup authority. Without that target, establish neutral independently.
SDK completion cannot prove settling.

No no/confused/surprised motor behavior is inferred from pose names. Those
poses are imported for operator-authored semantic mappings; a mapping must be
explicitly authored and revision-pinned before it can be scheduled.
# SO-ARM101 authored pose gestures

The pose importer copies operator-taught five-joint poses into a separate
revisioned asset file. Every imported pose must carry its actual source robot
and calibration identities; absent provenance is rejected. The content hash
covers those identities and capture timestamp as well as the joint values.
The importer never edits the source library and refuses an output path that
resolves to that library.

`author_complete_programs` creates finite, joint-only SDK `JointChoreography`
assets. Breathe defaults to a 5-second cycle, three loops, 2-second entry and
return, with a 60% rise. Yes defaults to neutral → up → (down → up) × 2 →
neutral, using one second per full up→down→up cycle (0.5 seconds per half) plus one-second entry and return. Its
timing is independent from breathe. Both use the existing zero-slope eased
authoring and retain exact authored control timestamps; the 256-frame ceiling is
checked before SDK authoring.

`validate_pose_assets` verifies the imported document revision, every pose
revision, and robot/calibration identity before joint values are extracted.
The concrete authoring flow is `scripts/author_soarm_pose_gestures.py`: provide
the source pose library, identity, separate asset output, a full launcher config
template, and launcher output. Optional `--breathe-*` and `--yes-*` arguments
set each gesture's cycle duration, loop count, entry/return duration, and
breathe rise fraction. `--yes-cycle-seconds` is the full up→down→up duration.
Outputs must be distinct from each other and from both input files. The script
writes a separate imported asset document and review-only candidate config; it
never changes the source library or launcher template. The generated config has
empty `approved_revisions` and strips physical authorization markers. Operators
must separately configure trusted pins and physical authorization after review.

SDK simulation preflight remains responsible for measured start, frame count,
duration and full-path speed/acceleration feasibility. Infeasible trajectories
are rejected, not slowed or retried. The wake limitation is unchanged: SDK
completion does not prove sensed settling, and no arbitrary-start wake-to-neutral
trajectory is synthesized; the first frame must match measured start within the
reviewed tolerance.

The existing semantic pack and execution scheduler dispatch only the exact
approved choreography revision. The SDK also requires measured start to be
within the policy tolerance of the program's first frame. Consequently wake
cannot be synthesized for an arbitrary unknown start: it requires a separately
preapproved trajectory whose first frame matches the measured start. If the
SDK cannot authorize such an exact-start asset, wake remains blocked pending
SDK/operator support. No live robot operation or physical qualification is
performed by authoring or simulation tests.

## V4 review export

The authoring script also accepts `--v4-catalog-template`,
`--v4-robot-template`, `--v4-room-template` and `--v4-output-dir`. It writes
`catalog.json`, `robot.json` and `room.json` as private review candidates.
The SDK catalog contains exact pins and `allow_physical_trial: false`; the room
omits motion authority. The breathe asset is explicitly copied into separately
hashed idle, listening and speaking candidates, and yes is a finite candidate.
These aliases do not establish that any of them is physically approved.
The original 19-second breathe was checked with the SDK simulator at its
authored entry: it failed the configured 8-degree/second speed limit (0.2642
versus 0.1396 rad/s), before acceleration could be established. The shortened
4.5-second breathe and 4-second yes candidates also fail that speed limit.
The runtime permits longer pinned programs within the SDK's 30-second policy
ceiling; this does not make these candidates feasible or physically approved.
No timing is silently changed. SDK full-path checks and exact revision pins
remain mandatory.
