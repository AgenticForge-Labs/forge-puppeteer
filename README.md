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

## Real integrations

When a real robot, camera, or capture stack is added, keep the hardware-specific implementation behind the existing semantic/capture interfaces and test the mapping independently. Real integrations should expose readiness and supported capabilities, preserve calibration/range validation, reject unsupported requests clearly, and provide a safe stop/failure path.

## Legacy mapping

This repository consolidates the durable physical-production behavior from `robo-puppeteer` and the Stage/camera/capture portions of `robo-studio`. Stage remains a first-class concept inside this repository rather than a separate repository. See `docs/MIGRATION.md` for the detailed transfer matrix and external-adapter boundaries.
