"""单集请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ModelType(str, Enum):
    builtin = "builtin"
    design = "design"
    clone = "clone"


class HostConfig(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_type: ModelType = ModelType.builtin
    voice_id: Optional[str] = None
    reference_audio: Optional[str] = None
    voice_description: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    emotion: Optional[str] = None
    audio_tag_style: Optional[str] = None
    speaker_names: list[str] = []


class EpisodeCreate(BaseModel):
    project_id: int
    script: str
    name: Optional[str] = None
    host_a_config: HostConfig
    host_b_config: Optional[HostConfig] = None
    global_instruction: Optional[str] = None
    intro_text: Optional[str] = None
    outro_text: Optional[str] = None


class EpisodeUpdate(BaseModel):
    name: Optional[str] = None
    script: Optional[str] = None
    host_a_config: Optional[HostConfig] = None
    host_b_config: Optional[HostConfig] = None
    global_instruction: Optional[str] = None
    intro_text: Optional[str] = None
    outro_text: Optional[str] = None


class EpisodeResponse(BaseModel):
    id: int
    project_id: int
    name: Optional[str]
    script: str
    host_a_config: Optional[str]
    host_b_config: Optional[str]
    global_instruction: Optional[str]
    audio_path: Optional[str]
    status: str
    progress_current: Optional[int] = 0
    progress_total: Optional[int] = 0
    error_message: Optional[str] = None
    segments_json: Optional[str] = None
    intro_text: Optional[str] = None
    outro_text: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SynthesizeRequest(BaseModel):
    episode_id: int


class PreviewSentenceRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    text: str = Field(min_length=1, max_length=2000)
    model_type: ModelType = ModelType.builtin
    voice_id: Optional[str] = None
    reference_audio: Optional[str] = None
    voice_description: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    emotion: Optional[str] = None
    global_instruction: Optional[str] = None
    audio_tag_style: Optional[str] = None
