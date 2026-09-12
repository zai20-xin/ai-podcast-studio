"""脚本生成 / 改写 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.llm import LLMService, friendly_error

router = APIRouter(prefix="/api/script", tags=["script"])


class ScriptGenerateRequest(BaseModel):
    source: str = Field(min_length=10, max_length=20000)
    mode: str = "dialogue"
    topic: str | None = None
    style_hint: str | None = None
    target_minutes: float | None = Field(default=3, ge=0.5, le=30)


class ScriptRewriteRequest(BaseModel):
    script: str = Field(min_length=10, max_length=20000)
    mode: str = "dialogue"
    action: str = "colloquial"  # colloquial | shorten | polish
    ratio: float | None = Field(default=None, ge=0.3, le=0.95)


@router.post("/generate")
def generate_script(data: ScriptGenerateRequest):
    if data.mode not in ("single", "dialogue"):
        raise HTTPException(status_code=400, detail="mode 须为 single 或 dialogue")
    try:
        svc = LLMService()
        script = svc.generate_script(
            source=data.source,
            mode=data.mode,
            topic=data.topic,
            style_hint=data.style_hint,
            target_minutes=data.target_minutes,
        )
        return {"script": script}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=friendly_error(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=friendly_error(e))


@router.post("/rewrite")
def rewrite_script(data: ScriptRewriteRequest):
    if data.mode not in ("single", "dialogue"):
        raise HTTPException(status_code=400, detail="mode 须为 single 或 dialogue")
    if data.action not in ("colloquial", "shorten", "polish"):
        raise HTTPException(status_code=400, detail="action 须为 colloquial / shorten / polish")
    try:
        svc = LLMService()
        script = svc.rewrite_script(
            script=data.script,
            mode=data.mode,
            action=data.action,
            ratio=data.ratio,
        )
        return {"script": script}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=friendly_error(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=friendly_error(e))
