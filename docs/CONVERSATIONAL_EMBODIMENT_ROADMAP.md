# Conversational Embodiment Roadmap

> **Goal:** Give an existing AI agent a natural physical presence through standard, discoverable MCP capabilities. Use the same performance primitives for live interaction and authored Forge Studios scripts.

**Status:** The talking/idle subset has a software-only implementation on a stacked draft PR (not main). A physical StackChan/Reachy/SO-ARM behavior mapping, listening, emotions and actual playback integration remain separate work. See [Talking/idle MVP](TALKING_IDLE_MVP.md).

## Scope and boundaries

- Puppeteer supplies physical expression, attention, timing, scripts, device discovery, and execution feedback. It does **not** supply the conversation model, speech recognition, memory, task planner, or general manipulation intelligence.
- A working agent can ask for conversational gestures while its separate manipulation controller owns the work. Puppeteer must not seize a shared joint, interrupt a task, or override hardware safety.
- Studios and conversation agents are different directors of the **same** performer behaviors: one supplies authored cues, the other live events. Both converge on existing typed action/behavior-pack and execution-runtime boundaries.
- The initial public interface should be self-describing MCP tools plus capabilities/resources. Keep tool descriptions sufficient for ordinary use; optional prompts or SKILL.md guidance can follow once we have proven workflows.
- Cross-application policy, complex shared/shifting autonomy, and fleet orchestration may eventually live in AgenticForge Engine/Platform. Keep the necessary public interoperability contracts small and do not promise an Engine dependency.

## 1. Minimal conversational vocabulary

Start with three **semantic states**, independent of device-specific motion commands:

| State | Meaning | Candidate expression |
| --- | --- | --- |
| `listening` | A person or agent is speaking and the robot is attending | Orient toward speaker when permitted; reduce competing motion |
| `speaking` | The robot/agent is delivering a response | Coordinate gaze, expression and optional gestures with speech; do not assume Puppeteer generates the voice |
| `idle` | No active conversational turn | Bounded, low-priority breathing/blinking/settling, or no movement |

Two **one-shot intents** can be added without expanding the persistent state machine:

- `acknowledge`: indicate an instruction was heard/understood, **never** imply successful task completion.
- `orient_to`: request attention toward a named target, subject to device capabilities and current authority.

Do not create three different conversation frameworks, personality engines, or unbounded motion generation. Prefer small, composable semantics. Exact names, schemas, and event sources must be reviewed before adding public MCP tools.

## 2. Map the same semantics onto three performers

| Performer | `listening` | `speaking` | `idle` |
| --- | --- | --- | --- |
| **Reachy Mini** | Attention pose/head orientation if allowed | Head/antenna gesture from approved motion catalog | Conservative idle head/antenna behavior |
| **StackChan** | Face/head attention via supported SDK commands | Facial expression, head and lights where available | Face animation or bounded idle state |
| **SO-ARM101** | Prefer external display/camera/other expressive device; arm movement only if explicitly cleared | A small approved gesture only when manipulation resources are available | No motion by default while working |

Implement capability lookup and per-robot graceful degradation: a performer can report that it cannot safely express a semantic cue. Do not fake a successful gesture or infer missing movement hardware.

## 3. One performance, three robots

Create one **semantic performance** (for example: listen, acknowledge, speak, settle) and resolve it into per-device approved cues for Reachy Mini, StackChan and SO-ARM101.

- Run all three from one authored performance/take ID, retaining individual performer IDs and action results.
- Preflight every mapped cue and asset revision before any device action begins.
- Coordinate starts with a shared time reference and a start barrier where supported; record requested and observed start/finish times per performer.
- Treat MCP scheduling as **best effort**, not hard real-time synchronization. Measure start offsets; do not claim simultaneous physical movement without measured evidence.
- A denied, busy, unavailable, or unconfirmed-stop device must follow the documented policy (skip, fail, or stop the group) rather than silently proceeding.
- Keep shared work/expression motion under the existing authority, resource, broker and device safety controls. No physical actions are implied by a mock demonstration.

First acceptance target: a fully offline, three-performer MCP stdio integration test with fake providers demonstrating the **same semantic sequence** and producing a per-performer take report. Follow with carefully authorized, separately qualified attended hardware tests.

## 4. Two directors, one performance vocabulary

**Conversation integration:** a thin adapter turns supported external events such as `speech_started`, `speech_finished`, `user_speaking` and `turn_idle` into the minimal semantics. The existing conversation application retains STT/TTS, turns, models and user context.

**Studios integration:** authored shots map to those same semantic behaviors and optional scripted timing. Preserve the existing opt-in pinned behavior-pack handoff; do not imply normal episode execution automatically invokes a physical robot.

Both use common behaviors but may differ in how cues are selected, delayed, interrupted, or resumed. Retain the legacy paths until they are deliberately unified; do not create a parallel device-control runtime.

## 5. Sequence for the next working session

1. Inspect existing `Performance`, `Cue`, behavior packs, MCP tools and provider mappings; identify what already covers the three states.
2. Agree on a compact v1 semantic state/event schema and device capability mapping for all three performers.
3. Build mock behavior examples for `listening`, `speaking`, `idle`, and a short `acknowledge` gesture without touching hardware.
4. Execute the same short performance concurrently through three fake-backed MCP servers. Collect per-device timing, success/skip/error and stop evidence.
5. Decide the smallest conversational adapter and Studios integration changes based on the demo. Defer physical qualification, advanced shared autonomy, complex agent skills, and any new conversation engine.

## Implementation and safety notes

- The current package already has typed MCP device tools, behavior packs, timeline/cue scheduling, ownership, action records and mock-backed integrations. These are foundations, **not** an implemented universal conversational state machine or a verified three-robot synchronized performance.
- Do not confuse SDK completion with measured pose settling, especially for Reachy Mini. Do not bypass SO-ARM broker authority; StackChan SDK and physical USB tests have separate qualification requirements.
- Keep real-device movement opt-in, operator-configured and independently validated. E-stop/interlock/workspace limits remain with hardware and device controllers, not an LLM or a descriptive skill.

Related: [README](../README.md) · [Robot MCP implementation plan](ROBOT_MCP_IMPLEMENTATION_PLAN.md) · [Deployment](ROBOT_DEPLOYMENT.md) · [Studios/Reachy handoffs](DOWNSTREAM_INTEGRATIONS.md)
