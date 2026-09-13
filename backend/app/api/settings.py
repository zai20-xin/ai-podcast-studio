"""系统设置 API"""
import os
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException

from app.schemas.settings import SettingsUpdate, SettingsResponse
from app.config import runtime_config, DEFAULT_BASE_URL, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
from app.providers import catalog_payload, get_provider

router = APIRouter(prefix="/api/settings", tags=["settings"])


def mask_key(key: str) -> str:
    if not key or len(key) < 10:
        return ""
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _require_token(x_studio_token: str | None) -> None:
    """若配置了 APP_TOKEN，则修改设置必须携带该头（本机无 token 时默认放行）"""
    expected = os.environ.get("APP_TOKEN", "").strip()
    if not expected:
        return
    if not x_studio_token or x_studio_token != expected:
        raise HTTPException(status_code=401, detail="缺少或错误的访问令牌")


def _valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _validate_provider(kind: str, provider_id: str | None) -> None:
    if not provider_id:
        return
    if not get_provider(kind, provider_id):  # type: ignore[arg-type]
        raise HTTPException(status_code=400, detail=f"未知的 {kind} 供应商: {provider_id}")


@router.get("", response_model=SettingsResponse)
def get_settings():
    return SettingsResponse(
        api_key_set=bool(runtime_config.api_key),
        base_url=runtime_config.base_url or DEFAULT_BASE_URL,
        masked_key=mask_key(runtime_config.api_key),
        llm_api_key_set=bool(runtime_config.llm_api_key),
        llm_base_url=runtime_config.llm_base_url or DEFAULT_LLM_BASE_URL,
        llm_model=runtime_config.llm_model or DEFAULT_LLM_MODEL,
        masked_llm_key=mask_key(runtime_config.llm_api_key),
        tts_provider=runtime_config.tts_provider,
        llm_provider=runtime_config.llm_provider,
        providers=catalog_payload(),
    )


@router.put("")
def update_settings(
    data: SettingsUpdate,
    x_studio_token: str | None = Header(default=None),
):
    _require_token(x_studio_token)
    if data.base_url and not _valid_http_url(data.base_url):
        raise HTTPException(status_code=400, detail="Base URL 必须是 http(s) 地址")
    if data.llm_base_url and not _valid_http_url(data.llm_base_url):
        raise HTTPException(status_code=400, detail="LLM Base URL 必须是 http(s) 地址")
    _validate_provider("tts", data.tts_provider)
    _validate_provider("llm", data.llm_provider)
    runtime_config.update(
        api_key=data.api_key,
        base_url=data.base_url,
        llm_api_key=data.llm_api_key,
        llm_base_url=data.llm_base_url,
        llm_model=data.llm_model,
        tts_provider=data.tts_provider,
        llm_provider=data.llm_provider,
    )
    return {"message": "设置已更新"}
