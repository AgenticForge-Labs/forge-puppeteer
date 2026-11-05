# Reachy Mini + StackChan motion continuity implementation

Status: **software implementation in PR #40; not hardware-qualified**. This document records the requirements and deliberate limitations; it does not authorize motion. See [PR #40 acceptance](PR40_MOTION_ENGINE_COMPLETION.md) for code locations and tests.

## Current verified baseline

- Puppeteer main before PR #40 already had Reachy `classic` and opt-in `expressive` styles, authored Reachy and StackChan idle/speaking packs, and optional audio-driven speaking.
- Reachy's upstream SDK implements `motion/speech_tapper.py:SwayRollRT` and `motion/head_wobbler.py:HeadWobbler`. It analyzes 20 ms windows, emits 50 ms hops, preserves oscillator phase, schedules offsets using PCM playback time, skips stale deadlines, and invalidates scheduled callbacks on stop/reset.
- The upstream Conversation App layers primary moves (breathing, emotions, goto poses) with speech-reactive offsets. It also suppresses idle motion during local microphone speech detection.
- Puppeteer's current speech feature schema carries **generated PCM timing**, explicitly **not measured speaker playout**. Do not label it acoustically synchronized.
- Puppeteer's existing `ExecutionRuntime` and native SDK adapters remain the only actuator authority. Never start a second upstream motion manager or parallel `HeadWobbler` callback with actuator access.

## Required profiles

Preserve both robots' **actual pre-change authored** idle and speaking patterns. Before refactoring, capture exact serialized behavior packs, revisions, defaults, cadence, parameters, and tests from the starting commit. Keep legacy output available as a selectable configuration, not only in Git history.

The operator-selected profiles are independent for `idle` and `speaking`:
- `legacy`: exact existing authored behavior and timing; the default until attended qualification.
- `native_v1`: opt-in Reachy expressive authored pack or StackChan native mouth-weight speaking pack. These are upstream-inspired authored alternatives, **not exact PCM sample parity**.
- `smooth_v2`: the same source-validated alternative pack plus 50 ms envelope smoothing for explicitly speech-driven speaking performers, opt-in, preserving the same physical grants. Idle uses authored targets; no extra motor scheduler is created.

Profile changes are **operator configuration**, never an automatic response to device failure, UNKNOWN outcome, stale audio, or failed STOP. An unsupported profile must fail closed.

## Implementation order

1. **Preserve baseline and regression fixtures (software implemented).** Record deterministic serialized Reachy and StackChan packs and the original audio-driven configuration. Assert that existing defaults and recorded yes/no playback remain unchanged. Add tests for independent idle/speaking profile selection and invalid names before enabling new profiles.
2. **Upstream comparison (documented approximation).** Reachy Mini SDK v1.10.0rc5 commit `221b3c3ce1aadae3c0eed97a8712d83bd205e13e` uses raw PCM and six-axis rad/mm, while Puppeteer receives content-free generated-PCM envelopes. Tests cover matching frequencies, silence, attack/release, reset, seeded phase and unitless bounds. Full identical-PCM parity is **not applicable** to this input contract and is not claimed.
3. **Timing and ownership (software implemented; physical audio calibration pending).** Retain the existing room/performer execution runtime. Establish a monotonic mapping from generated audio to *measured* playout where supported; otherwise retain an explicit uncalibrated timing label. Skip late targets rather than replaying backlogs. Preserve SDK cancellation, resource locks, action journal, STOP and UNKNOWN quarantine.
4. **Motion arbitration.** SDK-validated base/voice targets and finite gestures use a single serialized ExecutionRuntime. A finite gesture takes precedence over ongoing presence; after known completion, the current presence resumes. Normal idle/listening/speaking are mutually exclusive; there is **no simultaneous additive base+idle+speech waveform engine** and no claim of seamless measured transition. The opt-in shaper retains seeded phases across new utterances. Do not introduce a second hardware scheduler.
5. **StackChan renderer (software implemented with SDK preflight).** Respect integer tenths-degree servo semantics, nonzero valid pitch, actual firmware limits and native speaking modifier. Apply smooth target transitions and mouth/eye timing at device-appropriate rates; keep original speaking and idle as legacy.
6. **Conversationalist integration (dependent PR 2, not merged by this PR).** Maintain independent per-performer envelope delivery, bounded queues, interruption priority and honest playout timing. Do not leak raw audio or transcripts into Puppeteer.
7. **Automated integration first, attended physical test last.** Run unit, contract, simulated two-performer and failure tests. Only then perform one operator-attended legacy-vs-new comparison with prerecorded speech and live multi-turn conversation, including startup, speaking, listening, idle, interruption, STOP and shutdown. Record commanded targets, measured pose where available, dropped cues, audio timing, and video. Do not make `smooth_v2` the hardware default without physical acceptance.

## Acceptance gates

- Both legacy speaking and idle patterns remain selectable for both robots, unchanged in their serialized behavior.
- No motion generator bypasses Puppeteer's execution authority or native SDK.
- Speech oscillator outputs and time base are deterministic and documented; the native parity test states whether it uses PCM or only envelope features.
- No queued motion burst after latency, no discontinuous transition, no cross-performer starvation.
- UNKNOWN/uncertain STOP never triggers automatic fallback motion, retry, neutral, or rearm.
- Final hardware results are required before asserting physical smoothness, safe settling, or acoustic synchronization.

## Git workflow

This branch starts from Puppeteer `main`. Open Puppeteer PR #20 concerns authored neutral and remains a **separate hardware-gated change**; do not merge it into this work or assume it is present. Complete Puppeteer-owned contracts first, then update Conversationalist and StackChan only if necessary on dependent branches. Keep hardware validation until the end as requested, without relaxing software safety gates.
