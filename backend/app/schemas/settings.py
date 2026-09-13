"""系统设置模型"""
from pydantic import BaseModel
from typing import Optional


class SettingsUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    tts_provider: Optional[str] = None
    llm_provider: Optional[str] = None


class SettingsResponse(BaseModel):
    api_key_set: bool
    base_url: str
    masked_key: str = ""
    llm_api_key_set: bool = False
    llm_base_url: str = ""
    llm_model: str = ""
    masked_llm_key: str = ""
    tts_provider: str = "mimo"
    llm_provider: str = "freellmapi"
    # 设置页用的供应商目录（含 supported / experimental 标注）
    providers: dict = {}
