# Reachy original idle profile

The optional `original_v1` Reachy idle selection reproduces the pinned
Conversation App `BreathingMove` curve using a single runtime-owned SDK
action. The provider sends combined head, antenna and body targets at a
target rate of 60 Hz. The curve starts from the SDK's current head and
antenna measurements, interpolates toward the original app's fixed identity
head reference for one second after its 0.3 second inactivity delay, then uses the app's 5 mm vertical breathing
and 15 degree opposing antenna sway. The pinned source changes antenna
targets at the one-second transition; the app's wider motion composition
and listening blend may also affect what is observed in the original app.

Select it with `"idle": "original_v1"` in the Reachy robot file's `options`
(see `ENSEMBLE.md`). It requires the `original_idle` tool grant, and the
operator-reviewed neutral head file must contain the identity transform used by
the original curve. These checks validate configuration; they do not establish
clearance, calibration, physical neutral or safe motion.

The pinned daemon accepts commands through multiple transports and can compose
persistent speech offsets and tracking with targets. It does not provide an
exclusive motion lease. Check the active controllers and daemon state before an
attended test; a status snapshot cannot prevent another client from issuing
commands afterward.

The action is bounded to at most one hour and remains subject to the room
presence watchdog and the existing runtime ownership and STOP path. Its
continuous action releases the shared admission gate after submission so it
does not block other performers. If the SDK command loop cannot return, STOP
remains unconfirmed and the runtime fences the performer. A returned loop is
software evidence, not proof that the physical robot settled or slept.

It does not clone the original app's listening blend, audio timing, gesture
composition, or tracking, and it has only been exercised in one attended idle
trial. (An `original_v1` *speaking* profile that composed speech offsets over
this idle was removed; speech-driven motion is `speaking: smooth_v2`.)

For the separately selected `smooth_v2` speaking profile, an operator may set
`"legacy_speech_fallback": true` with classic Reachy expression style and
speech-driven Reachy enabled. When no fresh speech features are available,
the existing runtime plays the reviewed head-only legacy speaking rhythm. It
does not replay stale features and does not activate after an unconfirmed motor
outcome. Fresh features resume the reactive path; interruption still uses the
same STOP and quarantine behavior. This fallback is rhythmic, not synchronized
to speech, and has not been physically validated in this configuration.
