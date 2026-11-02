"""Pure authoring validation for StackChan conversational poses.

Firmware live bounds and operational grants are still checked by the SDK
adapter. These are *additional* conservative design-time envelope limits.
"""

from __future__ import annotations

_MAX_YAW_DELTA = 150
_MAX_PITCH_DELTA = 100


def validate_stackchan_center(
    yaw: int, pitch: int, yaw_delta: int, pitch_delta: int, speed: int,
) -> None:
    if any(type(item) is not int for item in (yaw, pitch, yaw_delta, pitch_delta, speed)):
        raise ValueError("StackChan pose parameters must be integer tenths of a degree")
    if not 0 <= yaw_delta <= _MAX_YAW_DELTA:
        raise ValueError("StackChan yaw amplitude outside conservative authoring envelope")
    if not 0 <= pitch_delta <= _MAX_PITCH_DELTA:
        raise ValueError("StackChan pitch amplitude outside conservative authoring envelope")
    if not -1280 <= yaw - yaw_delta <= yaw + yaw_delta <= 1280:
        raise ValueError("StackChan yaw range exceeds protocol limits")
    if not 30 <= pitch - pitch_delta <= pitch + pitch_delta <= 870:
        raise ValueError("StackChan pitch range exceeds protocol limits")
    if not 1 <= speed <= 500:
        raise ValueError("StackChan speech/head speed outside authoring envelope")
