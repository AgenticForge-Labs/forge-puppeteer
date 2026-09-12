# Forge Puppeteer migration matrix

Forge Puppeteer consolidates the useful physical execution behavior from `robo-puppeteer` and the Stage/camera/capture portions of `robo-studio`. Hardware SDK repositories remain external adapters/dependencies.

| Legacy source | Destination | Status | Notes |
|---|---|---|---|
| semantic actions/capabilities | `actions.py` | transferred/extended | Gesture, expression, gaze, speech, wait, stop plus move/grip semantics. |
| action resource domains | `resources.py` | transferred | Prevents conflicting simultaneous performer actions. |
| performance/cues | `performance.py` | transferred | Fixed-time semantic cue model. |
| event emitter | `events.py` | transferred/adapted | Inline control hooks + best-effort telemetry queue. |
| character/clock/performance Stage | `runtime.py` | transferred/adapted | Renamed `PerformanceStage` so it cannot be confused with capture/environment Stage. |
| TimelineDirector | `runtime.py::TimelineDirector` | transferred | Concurrent fixed-time cues with cleanup. |
| embodiment protocol | `embodiments.py` | transferred | Semantic boundary maintained. |
| mock embodiment | `embodiments.py::MockEmbodiment` | transferred | Default development/dry-run path. |
| SO-ARM101 embodiment | `embodiments.py::SOARM101Embodiment` | transferred/adapted | Accepts an injected arm from `soarm101-motion-sdk`; SDK not copied. |
| voice provider protocol/mocks | `voice.py` | transferred | Provider-neutral speech boundary and deterministic tests. |
| old plugin registry | Python adapter construction + current protocols | simplified | Dynamic plugins can be re-added only when multiple installed third-party embodiments require discovery. |
| Robo Studio camera protocol/status | `cameras.py` | transferred | Application-owned capture-device abstraction. |
| CameraCollection | `cameras.py` | transferred | Atomic open/cleanup and stable camera IDs. |
| camera native pan/tilt/zoom model | `cameras.py` | transferred | Vendor-neutral normalized coordinate system. |
| camera calibration persistence | `cameras.py` | transferred | Versioned operator-approved calibration/presets. |
| OBSBOT/vendor native helpers | future concrete CaptureDevice/CameraMotionController adapters | external adapter | Vendor-specific code should live behind generic camera protocols; do not contaminate contracts. |
| Robo Studio switching/media-pipeline abstractions | Stage backend adapters | intentionally deferred | Useful only once a concrete OBS/NDI/shared-memory production sink is selected; generic contracts are preserved conceptually by Stage prepare/capture boundary. |
| legacy Studio/Director cross-imports | Forge Studios semantic request contract | retired | Forge Puppeteer receives `forge_puppeteer_request_v1`, not internal Director objects. |

## Two distinct Stage concepts

- **Capture/environment Stage** (`stage.py`) prepares the physical scene and captures media.
- **PerformanceStage** (`runtime.py`) coordinates semantic performer cues/resources.

They may be used together by `Puppeteer`, but are intentionally separate abstractions.

## Hardware safety acceptance

Before a concrete adapter is marked production-ready it must expose readiness/capabilities, validate calibration/ranges, stop safely, reject unsupported semantic actions, and have a mock or hardware-independent test of its mapping. Physical execution is never enabled simply because a config file names a device.

## External repositories retained

`soarm101-motion-sdk`, `stackchan-control-sdk`, Reachy/other hardware SDKs remain independent libraries. Forge Puppeteer is an orchestrator/semantic adapter layer, not a copy of every device SDK.
