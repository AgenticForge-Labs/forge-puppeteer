from __future__ import annotations
from pathlib import Path
from .contracts import PhysicalShotRequest, PhysicalTake
from .stage import Stage
from .performers import Performer

class Puppeteer:
    """Coordinates semantic performer behavior with a separately owned physical Stage."""
    def __init__(self, stage: Stage, performers: list[Performer]):
        self.stage=stage; self.performers=performers
    def execute(self, request: PhysicalShotRequest, output_dir: str|Path) -> PhysicalTake:
        out=Path(output_dir)
        self.stage.prepare(request)
        self.stage.start_capture(request)
        try:
            for performer in self.performers:
                performer.perform(request)
        finally:
            media=self.stage.stop_capture(request,out)
        return PhysicalTake(production_id=request.production_id,episode_id=request.episode_id,shot_id=request.shot_id,uri=str(media),stage_executor=self.stage.name,performer_executors=[p.name for p in self.performers],metadata={'camera':request.camera,'performance_intent':request.performance_intent,'stage':request.stage})
