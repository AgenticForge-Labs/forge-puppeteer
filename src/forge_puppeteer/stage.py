from __future__ import annotations
from pathlib import Path
from typing import Protocol
from .contracts import PhysicalShotRequest

class Stage(Protocol):
    name: str
    def prepare(self, request: PhysicalShotRequest) -> None: ...
    def start_capture(self, request: PhysicalShotRequest) -> None: ...
    def stop_capture(self, request: PhysicalShotRequest, output_dir: Path) -> Path: ...

class MockStage:
    name='mock-stage'
    def prepare(self, request): pass
    def start_capture(self, request): pass
    def stop_capture(self, request, output_dir):
        output_dir.mkdir(parents=True,exist_ok=True)
        path=output_dir/f'{request.shot_id}-physical.mock.json'
        path.write_text('{"mock": true}\n')
        return path.resolve()
