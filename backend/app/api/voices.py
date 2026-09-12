"""音色管理 API"""
import re
import shutil
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.voice import ClonedVoice
from app.schemas.voice import ClonedVoiceResponse
from app.config import VOICES_DIR, MAX_REFERENCE_AUDIO_BYTES
from app.services.presets import SCENE_PRESETS
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
ALLOWED_UPLOAD_SUFFIXES = {".wav", ".mp3"}


def _sanitize_upload_name(name: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", (name or "").strip())[:64]
    return cleaned or f"clone_{Path().stem}"


@router.get("/builtin")
def list_builtin_voices():
    return {"voices": BUILTIN_VOICES}


@router.get("/meta")
def list_tts_meta():
    """前端单一数据源：风格/语速/标签/模型"""
    return {
        "styles": list(STYLE_PRESETS.keys()),
        "speeds": list(SPEED_PRESETS.keys()),
        "audio_tags": list(AUDIO_TAG_STYLE_PRESETS.keys()),
        "inline_tags": list(AUDIO_TAG_INLINE_PRESETS.keys()),
        "model_types": [
            {"id": "builtin", "name": "内置音色"},
            {"id": "design", "name": "声音设计"},
            {"id": "clone", "name": "克隆音色"},
        ],
        "model_ids": MODEL_IDS,
    }


@router.get("/presets")
def list_scene_presets():
    return {"presets": SCENE_PRESETS}


@router.post("/clone", response_model=ClonedVoiceResponse)
async def clone_voice(name: str, audio: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 wav / mp3 文件")

    safe_name = _sanitize_upload_name(name)
    # 文件名来自客户端，强制消毒，防止路径穿越
    original_stem = Path(audio.filename or "ref").stem
    safe_stem = SAFE_NAME_RE.sub("_", original_stem)[:64] or "ref"
    file_path = (VOICES_DIR / f"{safe_name}_{safe_stem}{suffix}").resolve()

    if not str(file_path).startswith(str(VOICES_DIR.resolve())):
        raise HTTPException(status_code=400, detail="非法文件路径")

    size = 0
    with open(file_path, "wb") as f:
        while chunk := await audio.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_REFERENCE_AUDIO_BYTES:
                f.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="音频文件不能超过 10MB")
            f.write(chunk)

    voice = ClonedVoice(name=safe_name, reference_path=str(file_path))
    db.add(voice)
    db.commit()
    db.refresh(voice)
    logger.info("克隆音色已保存: %s", file_path)
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
    if not path.is_file() or not str(path.resolve()).startswith(str(VOICES_DIR.resolve())):
        raise HTTPException(status_code=404, detail="参考音频文件不存在")
    media = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(path, media_type=media)


@router.delete("/cloned/{voice_id}")
def delete_cloned_voice(voice_id: int, db: Session = Depends(get_db)):
    voice = db.query(ClonedVoice).filter(ClonedVoice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="音色不存在")
    try:
        path = Path(voice.reference_path)
        if path.is_file() and str(path.resolve()).startswith(str(VOICES_DIR.resolve())):
            path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("删除克隆音频失败: %s", e)
    db.delete(voice)
    db.commit()
    return {"message": "删除成功"}
