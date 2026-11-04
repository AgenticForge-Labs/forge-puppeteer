# Development plan

These are development and acceptance priorities, not claims that the work is complete. The device SDKs and brokers remain responsible for calibration, physical limits, leases and STOP. Operator authorization and readiness are required for physical execution.

## Hardware and ensemble acceptance

1. Preserve standalone and simulated operation while qualifying individual device connections and capabilities.
2. For Reachy, review physical clearances and complete attended startup, gesture, sensed neutral/settled-state, interruption and room-close validation. Preserve unknown-outcome quarantine and no automatic retry or rearm. [PR #20](https://github.com/AgenticForge-Labs/forge-puppeteer/pull/20) remains unmerged pending its acceptance conditions.
3. Qualify the other performers and their authorities before validating the three-robot ensemble. Review StackChan display behavior on actual hardware and validate the OBSBOT camera independently of mock CI.
4. Complete the relevant installed-wheel, SDK-simulation, OpenShell and hardware acceptance described in the [robot integration plan](docs/ROBOT_SDK_MCP_CONSOLIDATION.md).

## Expression and integration

- Develop speech-reactive embodiment through the ordered stages in [speech-driven embodiment](docs/SPEECH_DRIVEN_EMBODIMENT.md), including reviewed mappings, transport, timing and separate physical acceptance. Preview signals are not motor commands.
- Continue improving shared semantic behaviors and authored performance across devices, without bypassing the existing runtime or device authority. See the [conversational embodiment roadmap](docs/CONVERSATIONAL_EMBODIMENT_ROADMAP.md).
- Keep Puppeteer independently usable. Conversationalist owns voice and personality; Platform owns business events and approvals. A work event does not automatically authorize a motion command.
- RoboConduit is a deferred remote session/transport direction; it does not replace Puppeteer's performance layer or device safety. RC-M3 remains an optional expressive-audio experiment.

For dated history and detailed links, see [STATUS.md](STATUS.md). The linked subsystem documents and code define specific requirements.
