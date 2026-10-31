# Reachy SDK 1.10.0rc5 playback semantics

Inspected the installed `reachy-mini==1.10.0rc5` package in
`forge-reachy/.forge-reachy/upstream/reachy_mini_conversation_app/.venv`.
The implementation is in `reachy_mini/reachy_mini.py` and
`reachy_mini/motion/recorded_move.py`.

## Observed API

- `RecordedMove(move, sound_path=None)` reads `description`, `time` and
  `set_target_data`; its `duration` is computed from trajectory length and
  sample interval. It does not itself play or connect to a daemon.
- `RecordedMoves(hf_dataset_name)` loads local cache, then falls back to a
  Hugging Face download. Its `get(name)` returns a `RecordedMove` and
  `list_moves()` returns names. The Puppeteer staged catalog deliberately uses
  `RecordedMove` directly, avoiding this network/cache path.
- `ReachyMini.async_play_move(move, play_frequency=100.0,
  initial_goto_duration=0.0, sound=True)` returns `None`. It optionally sends
  the initial goto, starts associated audio only when `sound=True`, repeatedly
  evaluates and sends head/body/antenna targets, and ends its loop using
  wall-clock duration. It has no measured-pose completion check and does not
  prove the robot physically settled.
- `cancel_move()` sets `_move_cancelled=True`, calls
  `media_manager.stop_playing()`, and returns without waiting for the playback
  loop to observe the flag. The next loop iteration notices cancellation.
  There is no cancellation acknowledgment or confirmed stopped state.
- `enable_wobbling()` enables audio-reactive motion for SDK media playback and
  tells the daemon to enable wobbling; `disable_wobbling()` disables both and
  resets offsets. The provider does not toggle wobbling implicitly.
- Each `ReachyMini` instance owns a single `_move_cancelled` flag and the SDK
  exposes no playback handle or documented concurrent-playback arbitration.
  Puppeteer therefore serializes these actions as sharing all Reachy motion
  resources.

## Adapter contract

`ReachyPlaybackProvider` accepts an already-created SDK client and never opens a
connection. Motion must be explicitly authorized. `sound` defaults to false
for each action. Completion evidence says only that the SDK playback coroutine
returned; cancellation remains unconfirmed because the SDK offers no
acknowledgment. Adapter readiness reflects the supplied client's presence, not
an independent daemon health or hardware-safety check. No physical operation
was performed during this inspection or implementation.
