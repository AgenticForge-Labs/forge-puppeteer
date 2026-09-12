from __future__ import annotations
from pathlib import Path
from .contracts import PhysicalShotRequest, PhysicalTake

def load_request(path: str|Path) -> PhysicalShotRequest:
    return PhysicalShotRequest.model_validate_json(Path(path).read_text())
def save_take(path: str|Path, take: PhysicalTake) -> Path:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(take.model_dump_json(indent=2)+'\n'); return p
