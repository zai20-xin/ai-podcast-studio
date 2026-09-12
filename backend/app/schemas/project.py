"""项目请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class ProjectCreate(BaseModel):
    name: str
    mode: str = "single"


class ProjectResponse(BaseModel):
    id: int
    name: str
    mode: str
    created_at: datetime
    updated_at: datetime
    episode_count: int = 0
    done_count: int = 0
    error_count: int = 0
    last_status: Optional[str] = None

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int
