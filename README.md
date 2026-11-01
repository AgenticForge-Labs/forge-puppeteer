# Forge Puppeteer

### Give any AI agent a physical presence.

**One language for expression. Many robots. Live conversations and scripted performances.**

Forge Puppeteer helps AI agents **listen, acknowledge, speak, gesture, and perform** through physical devices. It is a model-agnostic robot-performance layer with a standard [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) interface, explicit hardware adapters, and a deterministic execution runtime.

An agent might acknowledge a person giving a new task, a character might gesture during a conversation, or Forge Studios might choreograph a scene across several performers. **Different directors. The same expressive vocabulary and execution foundation.**

Puppeteer does not replace the conversation app, agent brain, or robotic manipulation controller. It gives those systems a common way to request and coordinate *physical expression*.

## What could this feel like?

- **An approachable working robot:** You describe a new lab task. The robot turns toward you if its motion controller permits it, indicates that it is listening, and acknowledges the instruction without pretending the task is already done.
- **An expressive conversational character:** An existing voice agent drives gaze, gestures, face displays, lights, or sound behaviors using the robot's supported capabilities.
- **One scene, three performers:** Reachy Mini, StackChan, and SO-ARM101 follow the same authored *semantic* performance, each rendering only the motions its hardware and authority permit.
- **A physical Studio production:** Preauthored dialogue and timed cues become validated, traceable actions rather than unstructured device commands.

The first two examples describe the product direction; a universal conversational state interface and coordinated three-performer demo are **next steps**, not shipped features. The underlying typed MCP and scripted execution foundations already exist.

## How it fits together

```text
     Conversation / working agent       Forge Studios / scripted director
             (live events)                    (authored cues)
                     \                          /
                      \                        /
                      Semantic performance intent
                                  |
                       Forge Puppeteer
                 MCP tools · behavior packs · timing
                ownership · action records · recovery
                                  |
                   Explicit robot/provider adapters
                                  |
                 Reachy Mini · StackChan · SO-ARM101
```

**The agent decides what to communicate. Puppeteer translates that into approved behaviors. Device controllers retain authority over what can physically happen.**

### Built today

- Packaged **MCP stdio servers** with typed discovery, action, control, cancellation, and stop tools.
- Explicit profiles for **Reachy Mini**, **StackChan**, and **SO-ARM101**: fake providers for offline development, a staged Reachy catalog and opt-in pinned SDK provider, a StackChan SDK provider, and SO-ARM101 broker delegation.
- **Deterministic scripts and behavior packs** with pinned assets, preflight validation, dependencies, timing policies, action results, and optional local speech cues.
- A shared **ExecutionRuntime** with resource coordination, ownership checks, action journaling, restart reconciliation, and conservative handling of unknown outcomes.
- Mock Stage execution, provider-neutral voice abstractions, camera abstractions, and an **OBSBOT Tiny 2 / Tiny 2 Lite** PTZ adapter.
- An **opt-in Forge Studios behavior-pack handoff** and offline integration examples.

These are software capabilities. Mock, simulated, and fake-SDK test results do **not** establish safe real-hardware operation or hard-real-time synchronization.

### Coming next: conversational embodiment

We are keeping the first vocabulary intentionally small:

| Semantic behavior | Intended meaning |
| --- | --- |
| **Listening** | Attend to a speaker without interfering with current work |
| **Speaking** | Pair an existing conversation or voice stream with appropriate expression |
| **Idle** | Provide bounded, low-priority natural presence when not engaged |
| **Acknowledge** | Show that a message was received or understood, not that work is complete |

Then we'll map these behaviors to each of our three robots and demonstrate **one shared performance across Reachy Mini, StackChan, and SO-ARM101** with per-performer timing and result records. The initial demonstration will use fake-backed MCP servers; hardware tests require separate qualification.

See the [Conversational Embodiment Roadmap](docs/CONVERSATIONAL_EMBODIMENT_ROADMAP.md) for the minimal plan, explicit boundaries, and next working session.

## Robot SDK → MCP consolidation (in progress)

The [ordered robot integration plan](docs/ROBOT_SDK_MCP_CONSOLIDATION.md)
keeps SDKs independently usable while moving all robot-facing MCP
interfaces into **Forge Puppeteer**. SO-ARM101 migration comes first:
Puppeteer now has a development direct-authenticated-broker transport
(`broker_url` + trusted token env), avoiding a nested SDK MCP
subprocess for that configuration. Its camera, native controls and
MCP guidance parity are being assembled in the same PR. The original
`soarm101-mcp`/OpenShell client remains for migration compatibility
until clean installed-wheel and SDK-simulation validation passes.
Nothing here enables physical robot operation or removes SDK safety.

**StackChan implementation has begun in a separate draft branch**, while
SO-ARM101 SDK cleanup PR #99 awaits local OpenShell acceptance. The work
is independent of the SO-ARM101 motion broker and does not modify its
SDK. StackChan's native SDK remains independently usable; Puppeteer
adds operator-granted MCP access using the *real* StackChan 0.3.0
facade contract, live capability filtering, typed NeoHEX updates,
replay revisions and common action/STOP handling. See the
[StackChan MCP integration guide](docs/STACKCHAN_NATIVE_MCP.md).
It remains unmerged and unqualified for physical hardware.

## Try it without hardware

Requires Python 3.11 or newer. From a local checkout:

```bash
git clone https://github.com/AgenticForge-Labs/forge-puppeteer.git
cd forge-puppeteer
python -m pip install -e '.[mcp,dev]'

# Validate and run a mock physical shot
forge-puppeteer validate --request examples/robots/package-smoke-request.json
forge-puppeteer execute \
  --request examples/robots/package-smoke-request.json \
  --stage mock --out /tmp/forge-puppeteer-take.json

# Run a packaged-MCP-style, fake Reachy behavior handoff
python scripts/downstream-integration-smoke.py
```

Or launch a fake StackChan MCP server for an MCP stdio client:

```bash
forge-puppeteer-mcp --config examples/robots/mock-mcp-server.json
```

The MCP server speaks **stdio**, so an MCP client must start/connect to the process; it is not an interactive shell. All these examples use mock or fake behavior. For clean-wheel integration testing, run `bash scripts/offline-package-smoke.sh` (dependency staging may access the package index unless you provide a complete wheelhouse).

## Build on it

Use the same runtime for a configured single device action from Python or CLI:

```bash
forge-puppeteer provider-action \
  --provider-config operator.json --action action.json
```

The `operator.json` file and typed `ActionRequest` must be operator-authored and valid; this is **not** a way to pass arbitrary agent-generated hardware instructions. See [Robot Deployment](docs/ROBOT_DEPLOYMENT.md) for profiles, authority gates, exact SDK versions, device constraints, and package-smoke instructions.

Explore:

- [Robot deployment and safety boundaries](docs/ROBOT_DEPLOYMENT.md)
- [MCP implementation plan and detailed validation](docs/ROBOT_MCP_IMPLEMENTATION_PLAN.md)
- [Robot contracts and capability models](docs/ROBOT_CONTRACTS.md)
- [Forge Studios and Reachy handoff examples](docs/DOWNSTREAM_INTEGRATIONS.md)
- [Example behavior packs: Reachy](examples/robots/reachy/), [StackChan](examples/robots/stackchan/), [SO-ARM101](examples/robots/soarm101/)
- [Consolidation from older Puppeteer/Studio repositories](docs/MIGRATION.md)

## Scripted performance and physical production

Forge Puppeteer remains useful even without a conversational agent. It accepts a stable `forge_puppeteer_request_v1` semantic shot contract, coordinates performers, and separates performance from **Stage** capture and environment concerns. Forge Studios has an opt-in behavior-pack bridge, but its normal episode flow does not automatically control a physical robot.

An OBSBOT PTZ integration is available at `forge_puppeteer.obsbot.ObsbotSdkPTZController`. On Ubuntu, `bash scripts/install-obsbot-sdk-helper.sh` installs the native helper. Absolute normalized pan/tilt/zoom commands are supported; relative movement remains disabled pending validated position readback. No real camera is opened simply by importing Puppeteer.

## Safe collaboration, not competing robot controllers

Physical expression and physical work often share resources. Puppeteer must respect active manipulation controllers, explicit ownership, resource limits, and a device's stop behavior. It may request an attention gesture; it must not steal a working arm or assume that an SDK acknowledgment means a robot has safely stopped.

Rich **shifting and shared autonomy** across humans, agents, and controllers is a wider ecosystem design goal. Puppeteer should expose portable behaviors and honest capability/authority feedback, while more complex autonomy orchestration can remain in AgenticForge Engine/Platform. Hardware and safety controllers must continue independently enforcing motion and safety constraints.

**Current status:** Active development (`0.1.0` package metadata), with tested software integrations and further physical qualification required. No published package release is assumed by these instructions.

**License:** This public repository does not currently include a license file. Public visibility alone does not grant general reuse or redistribution rights; a license decision is still needed before describing the project as open source.
