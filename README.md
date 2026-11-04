![Ink illustration of a puppeteer guiding Reachy Mini, an SO-ARM101 arm, and StackChan](docs/images/forge-puppeteer-banner.webp)

# Forge Puppeteer

**Give AI agents and creative performers a physical presence.**

Forge Puppeteer is a robot-performance system for coordinating expressive behavior across physical devices. A conversational agent can ask a robot to listen or acknowledge; a director can choreograph gestures and cues for a scene. Puppeteer translates shared performance intent into device-specific actions using explicit adapters and a deterministic execution runtime.

**One language for expression. Many robots. Live conversations and scripted performances.**

## What you can do

- Give a conversational character a physical presence through listening, speaking, idle, acknowledgement and gesture behaviors.
- Coordinate a shared semantic performance across Reachy Mini, StackChan and SO-ARM101, with each performer using its supported capabilities.
- Author repeatable behavior packs and scripted performances with validation, timing and action records.
- Connect an AI agent or other client through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), or use Puppeteer through Python and the CLI.
- Experiment without robots using mock stages and fake providers.

The ensemble launcher supports configurations for Reachy alone, Reachy with StackChan, or all three performers. Actual capabilities depend on the configured providers, operator grants and device readiness. See [Native conversational performers](docs/THREE_ROBOT_ENSEMBLE.md).

## How it works

A conversation system, working agent or scripted director requests a semantic behavior. Puppeteer coordinates the performers, validates and journals actions, and delegates execution through device adapters. Each robot's SDK or broker retains authority over calibration, motion limits, leases and stopping. Puppeteer can work independently of the broader AgenticForge platform.

Puppeteer is a performance layer: the conversation application owns its voice and personality, and a robot's controller remains responsible for physical operation. Acknowledging an instruction does not mean a task is complete.

## Try it without hardware

Requires Python 3.11 or newer. From a local checkout:

```bash
git clone https://github.com/AgenticForge-Labs/forge-puppeteer.git
cd forge-puppeteer
python -m pip install -e '.[mcp,dev]'

# Validate and execute a mock physical shot
forge-puppeteer validate --request examples/robots/package-smoke-request.json
forge-puppeteer execute \
  --request examples/robots/package-smoke-request.json \
  --stage mock --out /tmp/forge-puppeteer-take.json

# Run a fake Reachy behavior handoff
python scripts/downstream-integration-smoke.py
```

To connect an MCP client to a fake StackChan server:

```bash
forge-puppeteer-mcp --config examples/robots/mock-mcp-server.json
```

The server communicates over **stdio**; it must be launched by an MCP client rather than used as an interactive shell. These examples use mock or fake providers.

## Working with real devices

Real hardware requires operator-reviewed configuration, device-specific permissions and appropriate safety checks. Software tests and simulations do not qualify physical motion or establish that a device has safely stopped. Start with the [robot deployment and safety guide](docs/ROBOT_DEPLOYMENT.md) and the relevant [ensemble guide](docs/THREE_ROBOT_ENSEMBLE.md).

## Explore

- [Current status](STATUS.md) and [development plan](PLAN.md)
- [Robot contracts and capability models](docs/ROBOT_CONTRACTS.md)
- [Native conversational behaviors](docs/NATIVE_CONVERSATION_BEHAVIORS.md)
- [StackChan MCP integration](docs/STACKCHAN_NATIVE_MCP.md)
- [Forge Studios and Reachy handoff examples](docs/DOWNSTREAM_INTEGRATIONS.md)
- Example behavior packs: [Reachy](examples/robots/reachy/), [StackChan](examples/robots/stackchan/), [SO-ARM101](examples/robots/soarm101/)

## Project status and license

Forge Puppeteer is in active development. Hardware qualification and integration work are ongoing; see [STATUS.md](STATUS.md) for details.

This public repository does not currently include a license file. Public visibility alone does not grant general reuse or redistribution rights.
