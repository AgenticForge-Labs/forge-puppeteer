"""Inspect the installed pinned Reachy SDK API without creating a client."""

from __future__ import annotations

import inspect
from importlib.metadata import version

EXPECTED = {
    "ReachyMini": {"host", "port", "connection_mode", "spawn_daemon", "use_sim", "media_backend"},
    "async_play_move": {"move", "sound"},
    "cancel_move": set(),
    "goto_target": {"head", "antennas", "duration", "body_yaw"},
    "__exit__": {"exc_type", "exc_value", "traceback"},
}


def main() -> None:
    installed = version("reachy-mini")
    assert installed == "1.10.0rc5", f"expected reachy-mini 1.10.0rc5, got {installed}"
    from reachy_mini import ReachyMini

    actual = {
        "ReachyMini": inspect.signature(ReachyMini),
        "async_play_move": inspect.signature(ReachyMini.async_play_move),
        "cancel_move": inspect.signature(ReachyMini.cancel_move),
        "goto_target": inspect.signature(ReachyMini.goto_target),
        "__exit__": inspect.signature(ReachyMini.__exit__),
    }
    for name, required in EXPECTED.items():
        parameters = set(actual[name].parameters)
        if name != "ReachyMini":
            parameters.discard("self")
        missing = required - parameters
        assert not missing, f"{name} missing expected parameters {sorted(missing)}: {actual[name]}"
    assert actual["ReachyMini"].parameters["spawn_daemon"].default is False
    assert actual["ReachyMini"].parameters["use_sim"].default is False
    assert actual["ReachyMini"].parameters["media_backend"].default == "default"
    assert actual["async_play_move"].parameters["sound"].default is True
    print(f"reachy-mini {installed} API signatures verified (no client constructed)")
    for name, signature in actual.items():
        print(f"{name}{signature}")


if __name__ == "__main__":
    main()
