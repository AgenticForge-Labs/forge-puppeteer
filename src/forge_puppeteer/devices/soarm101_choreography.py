"""Operator-pinned SO-ARM101 choreography provider over the native SDK.

This provider never opens a servo port or creates a second motion controller.
The trusted host passes its already-connected SDK executor and independent
watchdog. Executing the complete approved motion remains the SDK's job.
"""

from __future__ import annotations

from collections.abc import Mapping

import anyio

from ..execution import ActionRequest, Completion, StopResult

_OPERATION = "play_choreography"
_RESOURCE = ("soarm101.motion",)
_MIN_TIMING_SCALE = 1.0
_MAX_TIMING_SCALE = 2.0


class SOARMChoreographyProvider:
    # An existing finite SDK trajectory may complete before a NORMAL presence
    # change; a user interruption or watchdog expiry always stops immediately.
    # Longer movements exceed this grace and still take terminal SDK STOP.
    finish_before_interrupt_s = 3.5
    defer_normal_transition = True

    def __init__(self, executor, watchdog, policy, programs: Mapping[str, object],
                 startup_target: object | None = None):
        from soarm101_motion.choreography import ChoreographyPolicy, JointChoreography

        if not isinstance(policy, ChoreographyPolicy):
            raise TypeError("a trusted SDK ChoreographyPolicy is required")
        if not programs or not all(isinstance(x, JointChoreography) for x in programs.values()):
            raise ValueError("nonempty SDK JointChoreography catalog required")
        for name, program in programs.items():
            if name != program.name:
                raise ValueError("catalog keys must equal choreography names")
            policy.require(program)
        if executor.robot_id != policy.robot_id:
            raise PermissionError("SO-ARM SDK robot identity differs from operator policy")
        self.executor = executor
        self.watchdog = watchdog
        self.policy = policy
        self.programs = dict(programs)
        neutral_program = self.programs.get("idle")
        if neutral_program is None:
            raise ValueError("configured idle neutral choreography is required")
        neutral = neutral_program.frames[0].joints_rad
        for name in ("yes", "breathe"):
            program = self.programs.get(name)
            if program is not None and (
                program.frames[0].joints_rad != neutral
                or program.frames[-1].joints_rad != neutral
            ):
                raise ValueError(f"{name} must enter and return to the configured idle neutral")
        if startup_target is not None:
            from soarm101_motion.choreography import JointTarget
            if (not isinstance(startup_target, JointTarget)
                    or startup_target.name != "wake_to_neutral"):
                raise ValueError("startup target must be the pinned wake_to_neutral asset")
            if startup_target.revision not in policy.allowed_revisions:
                raise PermissionError("startup target is not in the exact operator revision allowlist")
            if startup_target.joints_rad != neutral:
                raise ValueError("startup target must match the configured idle neutral")
        self.startup_target = startup_target

    def _program(self, request: ActionRequest):
        if request.operation == "wake_to_neutral":
            target = self.startup_target
            if (target is None or request.parameters
                    or request.asset_id != "soarm101/target/wake_to_neutral"
                    or request.asset_revision != target.revision):
                raise ValueError("unknown or stale operator-approved wake target")
            if (target.robot_id != self.policy.robot_id
                    or target.calibration_id != self.policy.calibration_id
                    or target.content_revision() != target.revision):
                raise PermissionError("wake target identity or revision differs from trusted policy")
            return target, 1.0
        if request.operation != _OPERATION:
            raise ValueError("only an exact pinned choreography operation is allowed")
        if set(request.parameters) - {"timing_scale"}:
            raise ValueError("unsupported choreography parameters")
        timing_scale = request.parameters.get("timing_scale", 1.0)
        if (isinstance(timing_scale, bool) or not isinstance(timing_scale, (int, float))
                or not _MIN_TIMING_SCALE <= timing_scale <= _MAX_TIMING_SCALE):
            raise ValueError("timing_scale must be within [1.0, 2.0]")
        if not request.asset_id or not request.asset_revision:
            raise ValueError("choreography requires asset ID and exact revision")
        if not request.asset_id.startswith("soarm101/choreography/"):
            raise ValueError("invalid SDK choreography asset ID")
        name = request.asset_id.removeprefix("soarm101/choreography/")
        program = self.programs.get(name)
        if program is None or request.asset_revision != program.revision:
            raise ValueError("unknown or stale operator-approved choreography")
        self.policy.require(program)
        return program, float(timing_scale)

    async def validate(self, request: ActionRequest) -> None:
        from soarm101_motion.choreography import preflight_choreography

        program, timing_scale = self._program(request)

        def check() -> None:
            # Preflight must not move or energize the arm. It is repeated in
            # the native executor directly before the actual SDK move.
            self.executor._require_active()
            if self.executor._session is None:
                raise PermissionError("existing SDK session must be connected")
            self.executor._assert_session_identity(self.executor._session)
            self.watchdog.check()
            if request.operation == "wake_to_neutral":
                from soarm101_motion.choreography import preflight_joint_target
                preflight_joint_target(self.executor._session, program, self.policy)
                return
            preflight_choreography(self.executor._session, program, self.policy,
                                   speed_scale=1.0 / timing_scale)

        await anyio.to_thread.run_sync(check)

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        self._program(request)
        return _RESOURCE

    def execution_timeout_for(self, request: ActionRequest) -> float:
        program, scale = self._program(request)
        if request.operation == "wake_to_neutral":
            return float(self.policy.max_duration_s) + 5.0
        return float(program.frames[-1].at_s) * scale + 5.0

    async def execute(self, request: ActionRequest) -> Completion:
        program, timing_scale = self._program(request)
        # A hardware motion worker can outlive host task cancellation. The
        # runtime must then call stop() via the independent SDK supervisor.
        # Never interpret coroutine cancellation as physical STOP.
        evidence = await anyio.to_thread.run_sync(
            lambda: self._execute_sdk(request, program, timing_scale),
            abandon_on_cancel=True,
        )
        if evidence.get("revision") != program.revision:
            raise RuntimeError("SDK completion did not match the approved revision")
        return Completion(
            "native SDK completed approved choreography; "
            "physical settling remains independently unverified"
        )

    def _execute_sdk(self, request: ActionRequest, program: object, timing_scale: float):
        if request.operation == "wake_to_neutral":
            from soarm101_motion.choreography import execute_trusted_joint_target
            return execute_trusted_joint_target(
                self.executor, self.watchdog, self.policy, program,
            )
        from soarm101_motion.choreography import execute_trusted_choreography
        return execute_trusted_choreography(
            self.executor, self.watchdog, self.policy, program,
            speed_scale=1.0 / timing_scale,
        )

    async def stop(self) -> StopResult:
        from soarm101_motion.simulated_replay import _stop_after_failure

        try:
            outcome = await anyio.to_thread.run_sync(
                _stop_after_failure, self.executor, self.watchdog,
                abandon_on_cancel=False,
            )
        except Exception:  # noqa: BLE001 - STOP evidence cannot be inferred
            return StopResult(False, "native SDK STOP result unknown")
        return StopResult(
            outcome.get("outcome") == "confirmed",
            "SDK supervisor STOP/HOLD confirmed"
            if outcome.get("outcome") == "confirmed"
            else "SDK supervisor STOP/HOLD remains unconfirmed",
        )
