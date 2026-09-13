"""音色管理 API"""
import re
import uuid
import shutil
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.voice import ClonedVoice
from app.schemas.voice import ClonedVoiceResponse
from app.config import VOICES_DIR, MAX_REFERENCE_AUDIO_BYTES, runtime_config
from app.providers import get_provider
from app.services.presets import SCENE_PRESETS
from app.services.edge_tts_engine import EDGE_VOICES, DEFAULT_EDGE_VOICE, DEFAULT_EDGE_VOICE_B
from app.services.tts_service import (
    STYLE_PRESETS,
    SPEED_PRESETS,
    AUDIO_TAG_STYLE_PRESETS,
    AUDIO_TAG_INLINE_PRESETS,
    MODEL_IDS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voices", tags=["voices"])

BUILTIN_VOICES = [
    {"id": "mimo_default", "name": "MiMo-默认", "lang": "自动", "gender": "-"},
    {"id": "冰糖", "name": "冰糖", "lang": "中文", "gender": "女"},
    {"id": "茉莉", "name": "茉莉", "lang": "中文", "gender": "女"},
    {"id": "苏打", "name": "苏打", "lang": "中文", "gender": "男"},
    {"id": "白桦", "name": "白桦", "lang": "中文", "gender": "男"},
    {"id": "Mia", "name": "Mia", "lang": "英文", "gender": "女"},
    {"id": "Chloe", "name": "Chloe", "lang": "英文", "gender": "女"},
    {"id": "Milo", "name": "Milo", "lang": "英文", "gender": "男"},
    {"id": "Dean", "name": "Dean", "lang": "英文", "gender": "男"},
]

SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]+")
# 显示名允许中文/空格，只去掉控制字符
_DISPLAY_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")
ALLOWED_UPLOAD_SUFFIXES = {".wav", ".mp3"}


def _sanitize_display_name(name: str) -> str:
    cleaned = _DISPLAY_UNSAFE_RE.sub("", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:64]


def _safe_file_stem(raw: str) -> str:
    return (SAFE_NAME_RE.sub("_", (raw or "").strip())[:48].strip("_")) or "ref"


def _voice_under_voices_dir(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(VOICES_DIR.resolve())
    except (OSError, ValueError):
        return False


def _active_tts_spec():
    return get_provider("tts", runtime_config.tts_provider or "mimo")


@router.get("/builtin")
def list_builtin_voices():
    spec = _active_tts_spec()
    if (spec and spec.id == "edge-tts") or runtime_config.tts_provider == "edge-tts":
        return {
            "voices": EDGE_VOICES,
            "provider": "edge-tts",
            "defaults": {"A": DEFAULT_EDGE_VOICE, "B": DEFAULT_EDGE_VOICE_B},
        }
    return {
        "voices": BUILTIN_VOICES,
        "provider": runtime_config.tts_provider or "mimo",
        "defaults": {"A": "冰糖", "B": "白桦"},
    }


@router.get("/meta")
def list_tts_meta():
    """前端单一数据源：风格/语速/标签/模型/能力"""
    spec = _active_tts_spec()
    supported = list(spec.supported_model_types) if spec else ["builtin", "design", "clone"]
    all_types = [
        {"id": "builtin", "name": "内置音色"},
        {"id": "design", "name": "声音设计"},
        {"id": "clone", "name": "克隆音色"},
    ]
    return {
        "styles": list(STYLE_PRESETS.keys()),
        "speeds": list(SPEED_PRESETS.keys()),
        "audio_tags": list(AUDIO_TAG_STYLE_PRESETS.keys()),
        "inline_tags": list(AUDIO_TAG_INLINE_PRESETS.keys()),
        "model_types": [t for t in all_types if t["id"] in supported],
        "model_ids": MODEL_IDS if "clone" in supported or "design" in supported else {},
        "tts_provider": runtime_config.tts_provider or "mimo",
        "capabilities": {
            "builtin": "builtin" in supported,
            "design": "design" in supported,
            "clone": "clone" in supported,
        },
    }


@router.get("/presets")
def list_scene_presets():
    return {"presets": SCENE_PRESETS}


@router.post("/clone", response_model=ClonedVoiceResponse)
async def clone_voice(name: str = "克隆音色", audio: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 wav / mp3 文件")

    display_name = _sanitize_display_name(name) or "克隆音色"
    # 磁盘文件名与显示名解耦：允许中文显示名，落盘用短 slug + 随机后缀防撞
    original_stem = Path(audio.filename or "ref").stem
    safe_stem = _safe_file_stem(original_stem)
    file_path = (VOICES_DIR / f"cv_{uuid.uuid4().hex[:10]}_{safe_stem}{suffix}").resolve()

    if not _voice_under_voices_dir(file_path):
        raise HTTPException(status_code=400, detail="非法文件路径")

    max_bytes = MAX_REFERENCE_AUDIO_BYTES
    max_label = f"{max_bytes / 1024 / 1024:.1f}MB"
    size = 0
    with open(file_path, "wb") as f:
        while chunk := await audio.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                f.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"参考音频不能超过 {max_label}（MiMo Base64 上限 10MB 对应的原始体积）",
                )
            f.write(chunk)

    voice = ClonedVoice(name=display_name, reference_path=str(file_path))
    db.add(voice)
    db.commit()
    db.refresh(voice)
    logger.info("克隆音色已保存: name=%s path=%s", display_name, file_path)
    return voice


@router.get("/cloned", response_model=list[ClonedVoiceResponse])
def list_cloned_voices(db: Session = Depends(get_db)):
    return db.query(ClonedVoice).order_by(ClonedVoice.id.desc()).all()


@router.get("/cloned/{voice_id}/audio")
def get_cloned_voice_audio(voice_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    voice = db.query(ClonedVoice).filter(ClonedVoice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="音色不存在")
    path = Path(voice.reference_path)
    if not path.is_file() or not _voice_under_voices_dir(path):
        raise HTTPException(status_code=404, detail="参考音频文件不存在")
    media = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(path, media_type=media)


class ClonedVoiceRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.patch("/cloned/{voice_id}", response_model=ClonedVoiceResponse)
def rename_cloned_voice(
    voice_id: int,
    data: ClonedVoiceRenameRequest,
    db: Session = Depends(get_db),
):
    """重命名克隆素材（只改显示名，不挪动磁盘文件，避免破坏已有 reference_path）"""
    voice = db.query(ClonedVoice).filter(ClonedVoice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="音色不存在")
    new_name = _sanitize_display_name(data.name)
    if not new_name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    voice.name = new_name
    db.commit()
    db.refresh(voice)
    logger.info("克隆音色已重命名: id=%s name=%s", voice_id, new_name)
    return voice


@router.delete("/cloned/{voice_id}")
def delete_cloned_voice(voice_id: int, db: Session = Depends(get_db)):
    voice = db.query(ClonedVoice).filter(ClonedVoice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="音色不存在")
    try:
        path = Path(voice.reference_path)
        if path.is_file() and _voice_under_voices_dir(path):
            path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("删除克隆音频失败: %s", e)
    db.delete(voice)
    db.commit()
    return {"message": "删除成功", "id": voice_id}
