"""Simulation-only integration of Puppeteer startup target and native SDK."""

from __future__ import annotations

import pytest

pytest.importorskip("soarm101_motion.choreography")

from soarm101_motion.agent_control import AgentAuthorityStore
from soarm101_motion.broker import AgentMotionRates
from soarm101_motion.broker_sdk import SDKAgentExecutor
from soarm101_motion.broker_watchdog import BrokerWatchdog
from soarm101_motion.choreography import ChoreographyPolicy, JointTarget
from soarm101_motion.config import SOARM101Config
from soarm101_motion.constants import ARM_JOINTS

from forge_puppeteer.conversation_soarm import (
    author_soarm_reference_programs,
    soarm_conversation_pack,
)
from forge_puppeteer.devices.soarm101_choreography import SOARMChoreographyProvider
from forge_puppeteer.execution import ActionRequest


@pytest.fixture
def supervised(tmp_path):
    executor = SDKAgentExecutor(
        config=SOARM101Config(robot_id="so101"), rates=AgentMotionRates(),
        simulation=True, simulation_actions=True,
        authority_store=AgentAuthorityStore(tmp_path / "lease.json"),
    )
    executor.execute("state", {})
    watchdog = BrokerWatchdog(
        timeout_s=8.0, validate=executor.validate_supervision,
        trip=lambda _reason: executor.execute("stop", {}),
    )
    executor.motion_guard = watchdog.check
    watchdog.start()
    watchdog.heartbeat()
    try:
        yield executor, watchdog
    finally:
        watchdog.close()
        executor.close()


@pytest.mark.anyio
async def test_fixed_wake_target_runs_only_from_pinned_startup_pack(supervised):
    executor, watchdog = supervised
    arm = executor._session
    neutral = [arm.get_joint_positions().positions[name] for name in ARM_JOINTS]
    target = JointTarget.author("wake_to_neutral", "so101", "simulation", tuple(neutral))
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
        speaking_wrist_roll_amplitude_rad=0.01,
        nod_wrist_flex_amplitude_rad=0.01,
        shake_wrist_roll_amplitude_rad=0.01,
    )
    policy = ChoreographyPolicy(
        "so101", "simulation",
        (*(program.revision for program in programs.values()), target.revision),
        # Joint target execution reserves one second of this hard deadline
        # for normal SDK completion and host scheduling.
        max_duration_s=5,
    )
    provider = SOARMChoreographyProvider(executor, watchdog, policy, programs, target)
    pack = soarm_conversation_pack(
        "soarm101", programs, approved_revisions=policy.allowed_revisions,
        startup_target=target,
    )
    assert pack.startup is not None
    cue = pack.startup.cues[0]
    request = ActionRequest(
        id="wake", performer_id="soarm101", caller_id="startup",
        session_id="simulation", operation=cue.operation,
        parameters=dict(cue.parameters), asset_id=cue.asset_id,
        asset_revision=cue.asset_revision,
    )
    before = len(arm.backend.command_history)
    await provider.validate(request)
    assert len(arm.backend.command_history) == before
    completion = await provider.execute(request)
    assert "completed" in completion.evidence
    assert arm.get_joint_positions().positions["wrist_roll"] == pytest.approx(
        target.joints_rad[ARM_JOINTS.index("wrist_roll")], abs=0.01,
    )


def test_startup_target_must_match_configured_neutral(supervised):
    executor, watchdog = supervised
    neutral = tuple(executor._session.get_joint_positions().positions[name] for name in ARM_JOINTS)
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
    )
    moved = list(neutral)
    moved[0] += 0.01
    target = JointTarget.author("wake_to_neutral", "so101", "simulation", tuple(moved))
    policy = ChoreographyPolicy(
        "so101", "simulation", (*(p.revision for p in programs.values()), target.revision),
    )
    with pytest.raises(ValueError, match="configured idle neutral"):
        SOARMChoreographyProvider(executor, watchdog, policy, programs, target)
    with pytest.raises(ValueError, match="configured idle neutral"):
        soarm_conversation_pack(
            "soarm101", programs, approved_revisions=policy.allowed_revisions,
            startup_target=target,
        )


def test_default_pack_has_no_startup_motion(supervised):
    executor, _watchdog = supervised
    arm = executor._session
    neutral = [arm.get_joint_positions().positions[name] for name in ARM_JOINTS]
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
    )
    pack = soarm_conversation_pack(
        "soarm101", programs,
        approved_revisions=tuple(program.revision for program in programs.values()),
    )
    assert pack.startup is None


def test_authored_yes_and_breathe_must_return_to_idle_neutral(supervised):
    from soarm101_motion.choreography import JointChoreography, JointFrame

    executor, _watchdog = supervised
    neutral = tuple(executor._session.get_joint_positions().positions[name] for name in ARM_JOINTS)
    target = JointTarget.author("wake_to_neutral", "so101", "simulation", neutral)
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
        nod_wrist_flex_amplitude_rad=0.01,
    )
    programs["breathe"] = JointChoreography.author(
        "breathe", "so101", "simulation", programs["idle"].frames,
    )
    approved_revisions = tuple(p.revision for p in programs.values()) + (target.revision,)
    for name in ("yes", "breathe"):
        frames = list(programs[name].frames)
        bad = list(frames[-1].joints_rad)
        bad[0] += 0.01
        frames[-1] = JointFrame(frames[-1].at_s, tuple(bad))
        programs[name] = JointChoreography.author(name, "so101", "simulation", tuple(frames))
        with pytest.raises(ValueError, match="enter and return"):
            soarm_conversation_pack(
                "soarm101", programs,
                approved_revisions=approved_revisions,
                startup_target=target,
            )
        if name == "yes":
            programs[name] = author_soarm_reference_programs(
                robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
                nod_wrist_flex_amplitude_rad=0.01,
            )[name]
        else:
            programs[name] = JointChoreography.author(
                "breathe", "so101", "simulation", programs["idle"].frames,
            )
