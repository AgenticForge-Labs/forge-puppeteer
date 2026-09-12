from __future__ import annotations
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

class PhysicalShotRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    contract_version: Literal['forge_puppeteer_request_v1'] = 'forge_puppeteer_request_v1'
    production_id: str
    episode_id: str
    shot_id: str
    duration_seconds: float = Field(gt=0)
    visual: str = ''
    camera: dict[str,Any] = Field(default_factory=dict)
    performance_intent: dict[str,Any] = Field(default_factory=dict)
    edit_intent: dict[str,Any] = Field(default_factory=dict)
    entity_ids: list[str] = Field(default_factory=list)
    stage: dict[str,Any] = Field(default_factory=dict)
    safety: dict[str,Any] = Field(default_factory=dict)

class PhysicalTake(BaseModel):
    contract_version: Literal['forge_puppeteer_take_v1']='forge_puppeteer_take_v1'
    take_id: str = Field(default_factory=lambda: f'take_{uuid4().hex}')
    production_id: str
    episode_id: str
    shot_id: str
    uri: str
    stage_executor: str
    performer_executors: list[str] = Field(default_factory=list)
    status: Literal['candidate','approved','failed']='candidate'
    metadata: dict[str,Any] = Field(default_factory=dict)
