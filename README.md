# Forge Puppeteer

Robot performance and physical-stage execution for AgenticForge productions.

Forge Puppeteer is intentionally useful on its own. It consumes a semantic shot request, coordinates performers, and works with a **Stage** abstraction that owns cameras/capture/physical environment. It does not need Forge Worlds or Forge Studios to run.

The conceptual split remains explicit:

- **Puppeteer** controls performers: semantic actions, blocking, coordinated behavior.
- **Stage** controls the physical environment: cameras, capture, lighting, rigs, sets, calibration.

## Install and smoke test

```bash
python -m pip install -e '.[dev]'
forge-puppeteer validate --request request.json
forge-puppeteer execute --request request.json --stage mock --out take.json
```

A request uses the stable `forge_puppeteer_request_v1` contract. Forge Studios can generate and dispatch this automatically for shots whose `execution_route` is `puppeteer` or `hybrid`.

## Adding real hardware

Implement `Stage.prepare/start_capture/stop_capture` for the camera/lighting/rig stack and one or more `Performer.perform()` adapters for robots such as SO-101, Reachy Mini, Stack-chan, or later systems. Keep motor/device commands inside adapters; the request contract remains semantic.

## Safety

The semantic request contains a `safety` block for physical constraints. Real adapters should fail closed on missing calibration, unavailable devices, unsafe workspace state, or action limits. Do not make generated narrative text directly control motors without a validated adapter/safety layer.

## Legacy mapping

This repository consolidates the durable concepts from `robo-puppeteer` and `robo-studio`; Stage survives as a first-class package/concept rather than a separate repository.
