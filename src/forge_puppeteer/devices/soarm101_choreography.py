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


class SOARMChoreographyProvider:
    def __init__(self, executor, watchdog, policy, programs: Mapping[str, object]):
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

    def _program(self, request: ActionRequest):
        if request.operation != _OPERATION or request.parameters:
            raise ValueError("only an exact pinned choreography operation is allowed")
        if not request.asset_id or not request.asset_revision:
            raise ValueError("choreography requires asset ID and exact revision")
        if not request.asset_id.startswith("soarm101/choreography/"):
            raise ValueError("invalid SDK choreography asset ID")
        name = request.asset_id.removeprefix("soarm101/choreography/")
        program = self.programs.get(name)
        if program is None or request.asset_revision != program.revision:
            raise ValueError("unknown or stale operator-approved choreography")
        self.policy.require(program)
        return program

    async def validate(self, request: ActionRequest) -> None:
        from soarm101_motion.choreography import preflight_choreography

        program = self._program(request)

        def check() -> None:
            # Preflight must not move or energize the arm. It is repeated in
            # the native executor directly before the actual SDK move.
            self.executor._require_active()
            if self.executor._session is None:
                raise PermissionError("existing SDK session must be connected")
            self.executor._assert_session_identity(self.executor._session)
            self.watchdog.check()
            preflight_choreography(self.executor._session, program, self.policy)

        await anyio.to_thread.run_sync(check)

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        self._program(request)
        return _RESOURCE

    async def execute(self, request: ActionRequest) -> Completion:
        from soarm101_motion.choreography import execute_trusted_choreography

        program = self._program(request)
        # A hardware motion worker can outlive host task cancellation. The
        # runtime must then call stop() via the independent SDK supervisor.
        # Never interpret coroutine cancellation as physical STOP.
        evidence = await anyio.to_thread.run_sync(
            lambda: execute_trusted_choreography(
                self.executor, self.watchdog, self.policy, program
            ),
            abandon_on_cancel=True,
        )
        if evidence.get("revision") != program.revision:
            raise RuntimeError("SDK completion did not match the approved revision")
        return Completion(
            "native SDK completed approved choreography; "
            "physical settling remains independently unverified"
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
