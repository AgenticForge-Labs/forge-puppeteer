"""Discover recorded Reachy motions through an explicitly supplied SDK library."""

from __future__ import annotations

from typing import Protocol

from .robot_contracts import AssetDescriptor


class RecordedMove(Protocol):
    @property
    def duration(self) -> float: ...


class RecordedMoveLibrary(Protocol):
    def list_moves(self) -> list[str]: ...
    def get(self, move_name: str) -> RecordedMove: ...


def discover_recordings(
    library: RecordedMoveLibrary, *, source: str, revision: str
) -> tuple[AssetDescriptor, ...]:
    """Build a catalog without opening a robot or interpreting motion names."""
    names = library.list_moves()
    if len(names) != len(set(names)) or any(not name or "/" in name for name in names):
        raise ValueError("recorded move library contains invalid or duplicate names")
    return tuple(
        AssetDescriptor(
            id=f"reachy/{name}",
            kind="recording",
            source=source,
            revision=revision,
            duration_s=library.get(name).duration,
            resources=("head.pose", "antennas.left", "antennas.right", "body.yaw"),
        )
        for name in sorted(names)
    )
