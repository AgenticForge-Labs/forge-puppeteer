# PR #40: Complete native conversational motion (implementation contract)

Status: in progress on `agent/per-performer-speech-dynamics`. This is a single Puppeteer-owned PR, not hardware qualification. The dependent Conversationalist PR must start only after this PR merges.

## Existing work to retain
- Existing legacy Reachy/StackChan idle and speaking pack fixtures and authored revision hashes.
- Merged opt-in pattern selection in `ConversationEnsemble`.
- Merged `SpeechExpressionShaper(dynamics="smoothed_v1")`; this branch adds per-performer opt-in to the preview window.
- Existing SDK validation, runtime leases, STOP/UNKNOWN quarantine and one room-owned action authority.
- Separate hardware-gated PR #20; do not silently adopt it.

## Required implementation in this PR
1. **Configuration:** one operator-reviewed per-performer mapping of independently chosen idle and speaking authored packs, plus envelope dynamics. Unknown performer, unsupported name, mismatched pack revision and unauthorized motion must fail closed. No automatic fallback on failed motion.
2. **Reachy parity:** pin upstream SDK revision and create deterministic fixtures. Distinguish envelope-derived six-axis *unitless* expression from actual PCM/20 ms analysis/50 ms output. Do not claim PCM or acoustic playout parity until implemented and tested against the same PCM. Document what is approximated.
3. **Motion composition:** integrate selected idle, listening, speaking and named gestures into the existing `ConversationEnsemble` dispatch gate and `ExecutionRuntime`. Use one SDK-validated motion authority; avoid overlapping absolute head targets, stale command backlogs and unintended restart after interruption. No second motor scheduler.
4. **StackChan:** retain firmware-native mouth/eye behavior and integer tenths-degree servo targets, validate pitch nonzero and discovered capability limits, and preserve original cadence as a legacy choice. Only add smoothing where SDK-validated.
5. **Lifecycle:** preserve explicit STOP/UNKNOWN quarantine and per-performer isolation. Do not infer neutral, sleep or physical settling from a software acknowledgment. Preserve idle resumption only after known-completed actions.
6. **Tests:** legacy serialized fixtures; opt-in profile matrix; deterministic speech traces including silence/attack/release/interruption/reset; mixed Reachy/StackChan fake-provider end-to-end traces; overlapping gestures; slow and failed provider, stale hops, unknown STOP and shutdown; full CI on Python 3.11/3.12.
7. **Documentation:** operator examples, exact limitations and physical acceptance checklist. Update this document as each item is actually implemented.

## CI issue to resolve before merge
The first run for this branch failed `test_transition_after_speech_never_quarantines_performer[asyncio]` on Python 3.12: `idle quarantined the performer` (352 passed, 1 skipped). Investigate timing/transition ownership and preserve the safety contract. Do not weaken the assertion or automatically rearm. Confirm reproducibility, then fix the authoritative scheduler or test's timing assumptions with regression coverage.

## Definition of done
- The operator can select supported profiles for each performer without changing the legacy default.
- Fake-provider tests demonstrate no conflicting targets, stale bursts, unsafe retries, or cross-performer starvation.
- Native speech-parity claims are supported by executable fixtures; otherwise the approximation is explicitly labeled.
- CI passes on both supported Python versions; all relevant docs updated.
- No real hardware is opened by CI and no physical readiness is claimed.
