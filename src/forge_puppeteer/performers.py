from __future__ import annotations
from typing import Protocol
from .contracts import PhysicalShotRequest

class Performer(Protocol):
    name: str
    def perform(self, request: PhysicalShotRequest) -> None: ...

class MockPerformer:
    name='mock-performer'
    def perform(self, request): pass
