"""音色请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel

class ClonedVoiceResponse(BaseModel):
    id: int
    name: str
    reference_path: str
    created_at: datetime

    model_config = {"from_attributes": True}