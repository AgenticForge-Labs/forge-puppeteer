# Forge Puppeteer

Robot-performance and physical-stage execution for AgenticForge productions.

Forge Puppeteer is intentionally useful on its own. It consumes a semantic shot request, coordinates performers, and works with a **Stage** abstraction for cameras/capture and the physical production environment. It does not need Forge Worlds or Forge Studios to run.

The conceptual split remains explicit:

- **Puppeteer** handles performer semantics, timing, coordination, and performance state.
- **Stage** handles capture/environment concerns such as cameras, sets, lighting interfaces, and calibration state.

## Install and smoke test

```bash
python -m pip install -e '.[dev]'
forge-puppeteer validate --request request.json
forge-puppeteer execute --request request.json --stage mock --out take.json
```

## Explicit configured provider actions

The legacy `validate` and `execute` commands retain their shot/Stage mock
behavior. To submit one typed device action through the same configured
provider adapter and `ExecutionRuntime` used by MCP, opt in with
`forge-puppeteer provider-action --provider-config operator.json --action
action.json`. The action JSON is an `ActionRequest` with operator-matching
performer ID, caller/session IDs, an allowlisted operation, and typed parameters.
The Python equivalent is `execute_configured_action(config_path, request)` in
`forge_puppeteer.operator`. The configured backend lifecycle, runtime
validation, ownership, and stop/quarantine rules remain in force. This path
does not translate legacy shot requests into device actions.

The `mcp` extra installs the MCP v2 server dependency and
`forge-puppeteer-mcp` stdio entrypoint (`pip install '.[mcp]'`). The launcher
requires trusted operator JSON selecting a mock backend and one device's fixed
tool allowlist; see [`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md).
The default install and `.[dev]` do not install robot SDKs or open hardware.
There are intentionally no StackChan or Reachy extras: the StackChan SDK wheel
is installed separately by the operator, and Reachy deployment must pin its SDK
together with a staged motion snapshot. Avoid installing an SDK from a mutable
checkout as a deployment dependency. See
[`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md) for current boundaries,
asset staging and offline package smoke instructions.

To build fresh Puppeteer and StackChan SDK wheels, stage runtime dependencies,
and install them offline in a temporary clean environment, run:

```bash
bash scripts/offline-package-smoke.sh
```

The staging phase downloads dependencies unless an offline wheelhouse is passed
as the script's first argument. Installation from the staged wheelhouse is
always offline. It validates and executes the mock request, exercises all three
mock-backed device profiles over MCP stdio, and tests the installed StackChan
SDK launcher through a fake serial PTY plus MCP stdio. The PTY protocol emulator
is software evidence only: it does not validate firmware, USB enumeration,
physical movement, or settling. No real hardware is opened.

A request uses the stable `forge_puppeteer_request_v1` contract. Forge Studios can dispatch this contract for shots whose `execution_route` is `puppeteer` or `hybrid`.

The built-in command-line execution path intentionally uses mock implementations. Device-specific integrations remain explicit adapters rather than being activated implicitly.

## Manual and agentic use

Manual testing and agentic orchestration use the same semantic action/performance primitives. The expected development progression is:

```text
manual semantic action/cue
-> reusable tested adapter primitive
-> timed performance
-> Stage/Puppeteer orchestration
-> optional Director/agent dispatch under explicit policy
```

Do not create a second autonomous implementation beside the manual one. See `AGENTS.md` for the repository's manual, assisted, agentic, and physical-safety operating rules.

## Consolidated runtime

The migration now includes the durable concepts from the old Puppeteer/Studio repositories:

- semantic action and capability models;
- resource-domain coordination for overlapping actions;
- timed performance/cue models;
- event hooks and telemetry surfaces;
- `PerformanceStage` for performer cue execution, kept distinct from capture/environment `Stage`;
- embodiment protocols plus deterministic mock support;
- an SO-ARM101 embodiment boundary that accepts the external motion SDK rather than copying it into this repository;
- provider-neutral voice abstractions;
- camera abstractions, camera collections, normalized pan/tilt/zoom concepts, and calibration/preset persistence.

Concrete vendor/device SDKs remain external dependencies behind these interfaces.

## OBSBOT pan/tilt/zoom camera control

The proven OBSBOT Tiny 2 / Tiny 2 Lite SDK control path from `robo-studio` has been migrated into Forge Puppeteer's Stage/camera layer.

Install the native helper on Ubuntu:

```bash
cd ~/code/forge-puppeteer
bash scripts/install-obsbot-sdk-helper.sh
```

The installer downloads the OBSBOT Linux SDK headers and `libdev.so` used by the existing working controller into:

```text
~/.local/share/forge-puppeteer/obsbot-sdk/v1.0.2/
```

and builds:

```text
~/.local/bin/forge-puppeteer-obsbot-helper
```

The Python adapter is `forge_puppeteer.obsbot.ObsbotSdkPTZController`. It implements the generic `CameraMotionController` shape with normalized pan/tilt in `-1..1` and zoom in `0..1`. Absolute pan/tilt/zoom is supported. Relative motion remains intentionally disabled until hardware position readback is normalized and validated.

Example:

```python
from forge_puppeteer.cameras import CameraMotionPosition
from forge_puppeteer.obsbot import ObsbotSdkPTZController

camera = ObsbotSdkPTZController(device="/dev/video0")
camera.connect()
camera.move_absolute(CameraMotionPosition(pan=0.25, tilt=-0.1, zoom=0.2))
camera.disconnect()
```

Set `FORGE_PUPPETEER_OBSBOT_HELPER` if the helper executable is installed somewhere else. The adapter is explicit and never activates real camera motion merely by importing Forge Puppeteer.

## Real integrations

When a real robot, camera, or capture stack is added, keep the hardware-specific implementation behind the existing semantic/capture interfaces and test the mapping independently. Real integrations should expose readiness and supported capabilities, preserve calibration/range validation, reject unsupported requests clearly, and provide a safe stop/failure path.

## Legacy mapping

This repository consolidates the durable physical-production behavior from `robo-puppeteer` and the Stage/camera/capture portions of `robo-studio`. Stage remains a first-class concept inside this repository rather than a separate repository. See `docs/MIGRATION.md` for the detailed transfer matrix and external-adapter boundaries.
#
