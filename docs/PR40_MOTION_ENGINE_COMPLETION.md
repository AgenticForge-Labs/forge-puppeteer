# PR #40 — Puppeteer native conversational motion software acceptance

Branch: `agent/per-performer-speech-dynamics`. This PR is software-only and does
not authorize or certify hardware motion. PR #20 is independent, unmerged and
hardware-gated. Dependent Conversationalist work begins only after this PR
merges into `main`.

## Software behavior and code locations

| Area | Implementation | Safety / evidence |
| --- | --- | --- |
| Reviewed selection | `mcp/ensemble_launcher.py:PerformerMotionSelection` | v1/v2/v3 support independent `idle` / `speaking` profiles; unknown IDs/values and unsupported SO-ARM streaming rejected before SDK connection |
| Legacy default | `motion_profiles.py:select_motion_patterns` | No selection copies the exact authored baseline and preserves yes/no recording SHA pins; selection never changes grants |
| Reachy alternative | `reachy_conversation_pack(... expression_style="expressive")` | Authored native-inspired target samples, selected only with explicit `pose_and_antennas` grant and operator-reviewed neutral antennas |
| Smoothing | `speech_expression.py:SpeechExpressionShaper` | `legacy` or `smoothed_v1` per performer, deterministic 50 ms generated-PCM features, independent sequence and TTL; smoothed mode retains seeded phases at new stream boundaries |
| Serialized motion | `conversation_ensemble.py` | One room/runtime, shared dispatch lock, native SDK preflight, gesture priority and latest-presence resume; no second hardware scheduler |
| Late hop rejection | `SpeechEnvelopeWindow` and `ConversationEnsemble` | Independent performer expiry, one dispatch per stream sequence, VAD silence gate, skip stale authored loop deadlines; no backlog replay |
| StackChan | `stackchan_conversation_pack` and `stackchan_native_mouth_cue` | Legacy size default, opt-in native mouth weight 0..75 with silence release; integer native tenths-degree head values and SDK firmware validation |
| STOP and quarantine | `ExecutionRuntime` and ensemble stop path | In-flight uncertain actions request SDK STOP, unknown/unconfirmed outcome quarantines; no autonomous rearm, retry, wake, neutral or sleep |
| Diagnostics | action journal, performer snapshots and deterministic tests | Acceptance is software dispatch/completion only; never evidence of physical settling |

`legacy`, `native_v1` and `smooth_v2` are **configuration
labels**, not evidence of exact upstream PCM output: on Reachy the nonlegacy
authored idle/speaking pack is the opt-in `expressive` pattern. The
`smooth_v2` speaking choice additionally uses the
`SpeechEnvelopeWindow(dynamics="smoothed_v1")` when the performer is in
`speech_driven_performers`; it does not instantiate Reachy's
`HeadWobbler`. On StackChan, nonlegacy speaking selects
`native_weight`; `smooth_v2` speaking adds the latest-hop mouth mapper.
SO-ARM101 remains exact approved finite choreography only.

## Upstream comparison — precise scope

Official Reachy Mini SDK **v1.10.0rc5**, signed tag resolving to commit
`221b3c3ce1aadae3c0eed97a8712d83bd205e13e`,
`src/reachy_mini/motion/speech_tapper.py:SwayRollRT`:
- analyzes raw mono float32 PCM in 20 ms windows with 50 ms hops;
- calculates dBFS, hysteretic VAD (-35/-45 dBFS), attack/release, a following
  envelope, per-axis oscillator phases and physical six-axis rad/mm outputs;
- `reset()` keeps seeded phases while resetting VAD, envelope and time.

Puppeteer's versioned `forge.speech_envelope.v1` takes **already reduced,
content-free generated-PCM** 50 ms features. Its matching six oscillator
frequencies are verified by deterministic tests, but its phases, VAD/loudness
mapping and amplitude are not numerically identical to `SwayRollRT`.
It emits unitless channels instead of rad/mm and cannot reconstruct raw PCM
or acoustic output latency. Do **not** claim exact PCM parity, speaker
synchronization or physically smooth motion. Hardware calibration is
deliberately postponed.

## Offline acceptance

Full GitHub CI is required on Python 3.11 and 3.12: `pytest -q`, selected
Ruff checks, and every regression suite. Relevant tests are
`test_motion_profiles.py`, `test_speech_expression.py`,
`test_stackchan_speech.py`, `test_conversation_native.py`,
`test_conversation_native_integration.py`,
`test_conversation_ensemble.py`, `test_reachy_stackchan_duo.py`,
`test_ensemble_launcher_config.py`, and `test_ensemble_mcp_wire.py`.

Fault tests cover: conflicting performer/profile identity, invalid revision,
unknown tool grants, over-limit cue parameters, duplicate/gap/out-of-order
envelope batches, independent stale envelope expiry, VAD silence, stream
reset, interruption, unconfirmed STOP quarantine, bounded transition
resumption, two native SDK fakes and three-performer simulation. An SDK
`completed` result is not measured pose convergence.

## Final local hardware gate (separate from this PR)

One attended Codex session after Puppeteer and Conversationalist software
merge must review physical clearance, real neutral and antennas, actual
commanded-vs-measured motion, StackChan firmware face and head appearance,
Reachy voice synchronization, duplicate controllers, motor settling, STOP
unknown/quarantine, room closure and daemon sleep lifecycle. Never auto-rearm
the previously unknown Reachy STOP. An unverified neutral/sleep state blocks
claims of physical qualification.
