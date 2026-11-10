# SO-ARM101 wake to neutral

The legacy in-process ensemble supports an opt-in, one-shot SO-ARM101 startup transition to an
operator-pinned five-joint neutral target. The default is `startup_behavior:
none`; no target is moved at startup unless the trusted ensemble config selects
`wake_to_neutral` and pins its exact SHA-256 revision. (Legacy in-process note: the
v4 room needs only `physical: true`; the old startup marker no longer exists there.)
This does not enable torque or create a broker, MCP, CLI, or generic agent
capability.

Schema v4 does not expose this operation: it requires an explicitly operator-established
neutral for owned physical choreography. Neither that declaration nor SDK
completion establishes sensed settling.

## Why the existing SDK actions are insufficient

The persistent SDK executor's `go_pose` action does plan from measured current
joint positions through the SDK controller. It accepts a saved pose **name**,
however, rather than an immutable content revision. Its capability validation
and replay each read the mutable `PoseLibrary` independently, so checking the
pose and then replaying it does not bind the motion to the content that was
reviewed. Also, saved-pose replay includes the gripper by default. The
`joint_only` option suppresses gripper movement and preserves existing physical
torque HOLD, but does not solve the mutable-content race.

The SDK's pinned `JointChoreography` path does bind exact authored content to
robot and calibration identity, uses whole-path SDK planner/workspace
validation, and commands only arm joints. Its preflight requires measured
current position to match the first pinned frame within the reviewed start
tolerance. It cannot safely express a target-only, measured-start transition
when the arm starts elsewhere.

Puppeteer must not bridge this gap by invoking the arm's motion controller
directly, generating an unpinned path from a live measurement, or issuing a
generic `go_pose` action. Those approaches would create a second execution
authority or weaken exact operator approval.

## SDK trusted-host capability

The SDK operation accepts an immutable, calibration-bound
five-joint target asset and its exact content revision, then under the existing
dispatch lock and authority/watchdog checks:

1. verifies the revision and robot/calibration identity;
2. reads measured current arm joints;
3. plans and validates the complete measured-start-to-pinned-target path using
   the existing SDK planner and workspace checks;
4. executes with the existing STOP/HOLD and terminal fault/quarantine behavior;
5. never enables physical torque or commands the gripper.

The operation must keep target content immutable between validation and
execution, and require the pre-existing session and applicable operator grant;
the agent lease applies to lease-gated modes, not operator-choreography-only.
The SDK now provides `JointTarget`, `preflight_joint_target`, and
`execute_trusted_joint_target`. The target hash binds robot ID, calibration ID,
and exactly five joint values. The SDK reads measured start and builds/preflights
the full path while holding its existing dispatch lock, then dispatches through
the ordinary SDK trajectory facade. Simulation may enable simulated torque;
physical execution requires existing authority, lease, watchdog, and operator
torque HOLD and never enables torque. Gripper commands are excluded.

The legacy in-process Puppeteer backend binds this target as its fixed `wake_to_neutral` startup
operation, scheduled by its existing `ExecutionRuntime` and performer ownership
slot. Interruption, unknown STOP outcome, and quarantine semantics are unchanged.
The behavior remains opt-in and does not auto-retry or rearm.

The new target API is currently source-available in the local
`soarm101-motion-sdk` checkout (`0.1.0.dev8`); that checkout is four commits
behind its remote and has not published a release containing this API. The
startup option therefore requires a future SDK release (or an installation of
this updated source). Existing startup-disabled choreography configurations do
not import the new target API.

## Validation boundary

Software simulation may validate the operation's authority admission,
planner path, no-gripper behavior, interruption/STOP handling, and fault
quarantine. It cannot qualify physical clearance, measured settling, STOP
performance, or safe startup behavior.
