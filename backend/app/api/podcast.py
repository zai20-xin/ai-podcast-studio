"""播客合成 API"""
import re
import json
import os
import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.episode import Episode
from app.schemas.episode import (
    EpisodeCreate,
    EpisodeUpdate,
    EpisodeResponse,
    SynthesizeRequest,
    PreviewSentenceRequest,
    HostConfig,
)
from app.services.tts_service import TTSService
from app.services.audio import AudioService
from app.services.llm import friendly_error
from app.config import AUDIO_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/podcast", tags=["podcast"])

MAX_DIALOGUE_LINES = 200
PROCESSING_STALE_MINUTES = 45

# 每集一把进程内锁，防止并发双开合成
_episode_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _episode_lock(episode_id: int) -> threading.Lock:
    with _locks_guard:
        if episode_id not in _episode_locks:
            _episode_locks[episode_id] = threading.Lock()
        return _episode_locks[episode_id]


def _ensure_under_dir(path: str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        p = Path(path).resolve()
        return p.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False



def parse_dialogue_lines(script: str) -> list[dict]:
    lines = [l.strip() for l in script.strip().split("\n") if l.strip()]
    dialogue = []
    for line in lines:
        if re.match(r"^【[^】]+】$", line):
            continue
        if line.startswith("---"):
            continue
        match = re.match(r"^(.+?)[：:]\s*(.+)$", line)
        if match:
            speaker = match.group(1).strip()
            content = match.group(2).strip()
            speaker = re.sub(r"^【[^】]+】", "", speaker).strip()
            if not content:
                continue
            dialogue.append({"speaker": speaker, "text": content})
        else:
            if not line.startswith("【"):
                dialogue.append({"speaker": "", "text": line})
    return dialogue[:MAX_DIALOGUE_LINES]


def extract_speakers(dialogue: list[dict]) -> list[str]:
    speakers = []
    seen = set()
    for line in dialogue:
        speaker = line["speaker"]
        if speaker and speaker not in seen:
            speakers.append(speaker)
            seen.add(speaker)
    return speakers


def _pick_host_config(
    speaker: str,
    host_a: HostConfig,
    host_b: HostConfig | None,
) -> HostConfig:
    """按角色名映射到主播配置"""
    if host_a.speaker_names and speaker in host_a.speaker_names:
        return host_a
    if host_b and host_b.speaker_names and speaker in host_b.speaker_names:
        return host_b
    # 无映射：A 负责第一位/空 speaker，其余给 B
    if not host_a.speaker_names and not (host_b and host_b.speaker_names):
        return host_a
    if host_b is None:
        return host_a
    return host_b if speaker else host_a


def _load_segments(episode: Episode) -> list[dict]:
    if not episode.segments_json:
        return []
    try:
        data = json.loads(episode.segments_json)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_segments(db: Session, episode: Episode, segments: list[dict]) -> None:
    episode.segments_json = json.dumps(segments, ensure_ascii=False)
    db.commit()


def _segment_dir(episode_id: int) -> Path:
    d = AUDIO_DIR / f"ep_{episode_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _model_type_str(config: HostConfig) -> str:
    mt = config.model_type
    return mt.value if hasattr(mt, "value") else str(mt)


async def _run_synthesis(episode_id: int) -> None:
    """后台合成：分句落盘，失败可续跑，全部成功后再合并"""
    lock = _episode_lock(episode_id)
    if not lock.acquire(blocking=False):
        logger.warning("单集 %s 已有合成任务在跑", episode_id)
        return
    db = SessionLocal()
    audio = AudioService()
    try:
        episode = db.query(Episode).filter(Episode.id == episode_id).first()
        if not episode:
            return

        def _heartbeat() -> None:
            episode.processing_heartbeat = datetime.utcnow()
            db.commit()

        host_a = HostConfig(**json.loads(episode.host_a_config))
        host_b = HostConfig(**json.loads(episode.host_b_config)) if episode.host_b_config else None

        dialogue_lines = parse_dialogue_lines(episode.script)
        if not dialogue_lines:
            raise ValueError("脚本为空或无法解析出有效台词")

        seg_dir = _segment_dir(episode_id)
        existing = {
            s.get("index"): s
            for s in _load_segments(episode)
            if isinstance(s.get("index"), int)
        }

        segments: list[dict] = []
        for i, line in enumerate(dialogue_lines):
            prev = existing.get(i)
            # 脚本变了则作废旧句
            same_text = prev and prev.get("text") == line["text"] and prev.get("speaker") == line["speaker"]
            audio_path = prev.get("audio_path") if same_text else None
            if audio_path and not Path(audio_path).exists():
                audio_path = None
            status = "done" if audio_path else "pending"
            segments.append({
                "index": i,
                "speaker": line["speaker"],
                "text": line["text"],
                "audio_path": audio_path,
                "status": status,
                "error": None,
            })

        intro_text = (episode.intro_text or "").strip()
        outro_text = (episode.outro_text or "").strip()
        has_intro = bool(intro_text)
        has_outro = bool(outro_text)

        # 片头/片尾也计入进度
        episode.status = "processing"
        episode.progress_total = len(segments) + (1 if has_intro else 0) + (1 if has_outro else 0)
        done_lines = sum(1 for s in segments if s["status"] == "done")
        intro_state = {"kind": "intro", "text": intro_text, "status": "pending", "audio_path": None, "error": None}
        outro_state = {"kind": "outro", "text": outro_text, "status": "pending", "audio_path": None, "error": None}
        intro_file = seg_dir / "intro.wav"
        outro_file = seg_dir / "outro.wav"
        if has_intro and intro_file.exists() and intro_file.stat().st_size > 0:
            intro_state["status"] = "done"
            intro_state["audio_path"] = str(intro_file)
        if has_outro and outro_file.exists() and outro_file.stat().st_size > 0:
            outro_state["status"] = "done"
            outro_state["audio_path"] = str(outro_file)

        episode.progress_current = done_lines + (1 if intro_state["status"] == "done" else 0) + (
            1 if outro_state["status"] == "done" else 0
        )
        episode.error_message = None

        def _persist() -> None:
            meta = []
            if has_intro:
                meta.append(intro_state)
            body = [{**s} for s in segments]
            if has_outro:
                meta.append(outro_state)
            episode.segments_json = json.dumps(meta + body, ensure_ascii=False)
            episode.processing_heartbeat = datetime.utcnow()
            db.commit()

        _persist()

        tts = TTSService()

        async def _synth_io(state: dict, dest: Path) -> str:
            path = await tts.synthesize(
                text=state["text"],
                model_type=_model_type_str(host_a),
                voice_id=host_a.voice_id,
                reference_audio=host_a.reference_audio,
                voice_description=host_a.voice_description,
                style=host_a.style,
                speed=host_a.speed,
                emotion=host_a.emotion,
                global_instruction=episode.global_instruction,
                dest_dir=seg_dir,
            )
            if Path(path) != dest:
                Path(path).replace(dest)
            return str(dest)

        if has_intro and intro_state["status"] != "done":
            try:
                intro_state["audio_path"] = await _synth_io(intro_state, intro_file)
                intro_state["status"] = "done"
                intro_state["error"] = None
            except Exception as e:
                intro_state["status"] = "error"
                intro_state["error"] = friendly_error(e)
                episode.status = "error"
                episode.error_message = f"片头合成失败：{intro_state['error']}"
                _persist()
                return
            episode.progress_current = done_lines + 1 + (1 if outro_state["status"] == "done" else 0)
            _persist()

        for seg in segments:
            if seg["status"] == "done":
                continue
            config = _pick_host_config(seg["speaker"], host_a, host_b)
            try:
                out_name = f"{seg['index']:03d}.wav"
                audio_path = await tts.synthesize(
                    text=seg["text"],
                    model_type=_model_type_str(config),
                    voice_id=config.voice_id,
                    reference_audio=config.reference_audio,
                    voice_description=config.voice_description,
                    style=config.style,
                    speed=config.speed,
                    emotion=config.emotion,
                    global_instruction=episode.global_instruction,
                    audio_tag_style=config.audio_tag_style,
                    dest_dir=seg_dir,
                )
                final_seg = seg_dir / out_name
                if Path(audio_path) != final_seg:
                    Path(audio_path).replace(final_seg)
                    audio_path = str(final_seg)
                seg["audio_path"] = audio_path
                seg["status"] = "done"
                seg["error"] = None
            except Exception as seg_err:
                logger.warning("分句 %s 失败: %s", seg["index"], seg_err)
                seg["status"] = "error"
                seg["error"] = friendly_error(seg_err)
                episode.status = "error"
                episode.error_message = f"第 {seg['index'] + 1} 句失败：{seg['error']}"
                done_lines = sum(1 for s in segments if s["status"] == "done")
                episode.progress_current = done_lines + (
                    1 if intro_state["status"] == "done" else 0
                ) + (1 if outro_state["status"] == "done" else 0)
                _persist()
                return

            done_lines = sum(1 for s in segments if s["status"] == "done")
            episode.progress_current = done_lines + (
                1 if intro_state["status"] == "done" else 0
            ) + (1 if outro_state["status"] == "done" else 0)
            _persist()

        if has_outro and outro_state["status"] != "done":
            try:
                outro_state["audio_path"] = await _synth_io(outro_state, outro_file)
                outro_state["status"] = "done"
                outro_state["error"] = None
            except Exception as e:
                outro_state["status"] = "error"
                outro_state["error"] = friendly_error(e)
                episode.status = "error"
                episode.error_message = f"片尾合成失败：{outro_state['error']}"
                _persist()
                return
            episode.progress_current = episode.progress_total
            _persist()

        paths = [s["audio_path"] for s in segments]
        if not paths or any(not p for p in paths):
            raise ValueError("存在未完成的分句，无法合并")

        output_name = f"episode_{episode.id}"
        final_path = await asyncio.to_thread(
            audio.merge_audio,
            paths,
            output_name,
            600,
            True,
            intro_state["audio_path"] if has_intro else None,
            outro_state["audio_path"] if has_outro else None,
        )

        episode.audio_path = final_path
        episode.status = "done"
        episode.progress_current = episode.progress_total
        episode.error_message = None
        _persist()
        logger.info("单集 %s 合成完成（%d 句）", episode_id, len(segments))
    except Exception as e:
        logger.exception("单集 %s 合成失败", episode_id)
        episode = db.query(Episode).filter(Episode.id == episode_id).first()
        if episode:
            episode.status = "error"
            episode.error_message = friendly_error(e)
            db.commit()
    finally:
        db.close()
        lock.release()



@router.post("/episodes", response_model=EpisodeResponse)
def create_episode(data: EpisodeCreate, db: Session = Depends(get_db)):
    episode = Episode(
        project_id=data.project_id,
        script=data.script,
        name=data.name,
        host_a_config=json.dumps(data.host_a_config.model_dump(mode="json")),
        host_b_config=json.dumps(data.host_b_config.model_dump(mode="json")) if data.host_b_config else None,
        global_instruction=data.global_instruction,
        intro_text=(data.intro_text or "").strip() or None,
        outro_text=(data.outro_text or "").strip() or None,
    )
    db.add(episode)
    db.commit()
    db.refresh(episode)
    return episode


@router.get("/episodes/project/{project_id}", response_model=list[EpisodeResponse])
def list_episodes(project_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Episode)
        .filter(Episode.project_id == project_id)
        .order_by(Episode.id.desc())
        .all()
    )


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    return episode


@router.get("/episodes/{episode_id}/status")
def get_episode_status(episode_id: int, db: Session = Depends(get_db)):
    """轻量轮询接口，含分句明细"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    segments = _load_segments(episode)
    body = [s for s in segments if isinstance(s.get("index"), int)]
    return {
        "id": episode.id,
        "status": episode.status,
        "progress_current": episode.progress_current or 0,
        "progress_total": episode.progress_total or 0,
        "error_message": episode.error_message,
        "audio_path": episode.audio_path,
        "segments": segments,
        "failed_count": sum(1 for s in body if s.get("status") == "error"),
        "done_count": sum(1 for s in body if s.get("status") == "done"),
    }


@router.put("/episodes/{episode_id}", response_model=EpisodeResponse)
def update_episode(episode_id: int, data: EpisodeUpdate, db: Session = Depends(get_db)):
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    if "name" in data.model_fields_set:
        episode.name = (data.name or "").strip() or None
    if data.script is not None:
        episode.script = data.script
    if data.host_a_config is not None:
        episode.host_a_config = json.dumps(data.host_a_config.model_dump(mode="json"))
    if data.host_b_config is not None:
        episode.host_b_config = json.dumps(data.host_b_config.model_dump(mode="json"))
    if data.global_instruction is not None:
        episode.global_instruction = data.global_instruction
    if data.intro_text is not None:
        episode.intro_text = (data.intro_text or "").strip() or None
    if data.outro_text is not None:
        episode.outro_text = (data.outro_text or "").strip() or None
    db.commit()
    db.refresh(episode)
    return episode


@router.delete("/episodes/{episode_id}")
def delete_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    if episode.audio_path and _ensure_under_dir(episode.audio_path, AUDIO_DIR):
        p = Path(episode.audio_path)
        for extra in (p, p.with_suffix(".mp3"), p.with_name(p.stem + "_norm.wav")):
            if extra.exists():
                try:
                    extra.unlink()
                except OSError:
                    pass
    seg_dir = _segment_dir(episode.id)
    if seg_dir.exists():
        import shutil
        shutil.rmtree(seg_dir, ignore_errors=True)
    db.delete(episode)
    db.commit()
    return {"message": "删除成功"}


@router.post("/parse-script")
def parse_script(data: dict):
    script = data.get("script", "")
    dialogue = parse_dialogue_lines(script)
    speakers = extract_speakers(dialogue)
    return {
        "speakers": speakers,
        "total_lines": len(dialogue),
        "dialogue": dialogue[:80],
    }


def _reclaim_if_stale(episode: Episode) -> bool:
    """进程被杀导致 processing 卡死时，超时后允许重新合成"""
    if episode.status != "processing":
        return False
    lock = _episode_lock(episode.id)
    if lock.locked():
        return False
    hb = episode.processing_heartbeat
    if not hb:
        return True
    return datetime.utcnow() - hb > timedelta(minutes=PROCESSING_STALE_MINUTES)


def _assert_not_busy(episode: Episode) -> None:
    if episode.status == "processing" and not _reclaim_if_stale(episode):
        raise HTTPException(status_code=409, detail="该单集正在合成中，请稍候")


@router.post("/synthesize")
async def synthesize_episode(
    data: SynthesizeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    episode = db.query(Episode).filter(Episode.id == data.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    _assert_not_busy(episode)

    dialogue_lines = parse_dialogue_lines(episode.script)
    if not dialogue_lines:
        raise HTTPException(status_code=400, detail="脚本为空或无法解析出有效台词")

    extra = (1 if (episode.intro_text or "").strip() else 0) + (
        1 if (episode.outro_text or "").strip() else 0
    )
    episode.status = "processing"
    episode.progress_current = 0
    episode.progress_total = len(dialogue_lines) + extra
    episode.error_message = None
    episode.segments_json = None
    episode.processing_heartbeat = datetime.utcnow()
    db.commit()

    background_tasks.add_task(_run_synthesis, episode.id)
    return {
        "message": "合成已开始",
        "episode_id": episode.id,
        "total_lines": len(dialogue_lines) + extra,
        "estimated_seconds": estimate_duration_seconds(dialogue_lines),
    }


def estimate_duration_seconds(lines: list[dict], chars_per_second: float = 4.2) -> int:
    """粗估成片时长：中文约 4 字/秒 + 每句 0.45s 停顿"""
    total_chars = sum(len(l.get("text") or "") for l in lines)
    return int(total_chars / chars_per_second + len(lines) * 0.45)


@router.post("/episodes/{episode_id}/estimate")
def estimate_synthesis(episode_id: int, db: Session = Depends(get_db)):
    """合成前预估：句数 / 字数 / 时长"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    lines = parse_dialogue_lines(episode.script)
    if not lines:
        raise HTTPException(status_code=400, detail="脚本为空或无法解析出有效台词")
    total_chars = sum(len(l["text"]) for l in lines)
    return {
        "total_lines": len(lines),
        "total_chars": total_chars,
        "estimated_seconds": estimate_duration_seconds(lines),
        "dialogue_preview": lines[:5],
    }


@router.post("/episodes/{episode_id}/retry")
async def retry_synthesis(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """失败后从断点继续：只重合成未完成/失败句，成功句复用"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    _assert_not_busy(episode)

    segments = _load_segments(episode)
    if not segments:
        raise HTTPException(status_code=400, detail="没有可续跑的分句，请重新合成")

    pending = sum(
        1 for s in segments if s.get("status") != "done" and isinstance(s.get("index"), int)
    )
    episode.status = "processing"
    episode.error_message = None
    episode.processing_heartbeat = datetime.utcnow()
    db.commit()

    background_tasks.add_task(_run_synthesis, episode.id)
    return {
        "message": "已开始续跑",
        "episode_id": episode.id,
        "pending_lines": pending,
    }


@router.post("/preview-sentence")
async def preview_sentence(data: PreviewSentenceRequest):
    try:
        tts = TTSService()
        audio_path = await tts.synthesize(
            text=data.text,
            model_type=data.model_type.value
            if hasattr(data.model_type, "value")
            else data.model_type,
            voice_id=data.voice_id,
            reference_audio=data.reference_audio,
            voice_description=data.voice_description,
            style=data.style,
            speed=data.speed,
            emotion=data.emotion,
            global_instruction=data.global_instruction,
            audio_tag_style=data.audio_tag_style,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=friendly_error(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=friendly_error(e))
    return {"audio_path": audio_path}


@router.get("/segments/{episode_id}/{filename}")
def get_segment_audio(episode_id: int, filename: str):
    """分句试听/断点音频"""
    safe = Path(filename).name
    if not re.match(r"^[\w\-.]+\.wav$", safe):
        raise HTTPException(status_code=400, detail="非法文件名")
    path = _segment_dir(episode_id) / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="分句音频不存在")
    return FileResponse(path, media_type="audio/wav")


@router.get("/{episode_id}/audio")
def get_audio(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode or not episode.audio_path:
        raise HTTPException(status_code=404, detail="音频不存在")
    if not _ensure_under_dir(episode.audio_path, AUDIO_DIR):
        raise HTTPException(status_code=400, detail="音频路径非法")
    return FileResponse(episode.audio_path, media_type="audio/wav")


@router.get("/{episode_id}/download")
def download_audio(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode or not episode.audio_path:
        raise HTTPException(status_code=404, detail="音频不存在")
    if not _ensure_under_dir(episode.audio_path, AUDIO_DIR):
        raise HTTPException(status_code=400, detail="音频路径非法")

    audio = AudioService()
    mp3_path = episode.audio_path.replace(".wav", ".mp3")
    if not (Path(mp3_path).exists() and Path(mp3_path).stat().st_mtime >= Path(episode.audio_path).stat().st_mtime):
        mp3_path = audio.convert_to_mp3(episode.audio_path)

    return FileResponse(
        mp3_path,
        media_type="audio/mpeg",
        filename=f"podcast_{episode_id}.mp3",
    )


@router.get("/{episode_id}/export/srt")
def export_srt(episode_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import PlainTextResponse

    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    segments = _load_segments(episode)
    if not segments:
        raise HTTPException(status_code=400, detail="尚无分句数据，请先合成")
    audio = AudioService()
    srt = audio.build_srt(segments)
    return PlainTextResponse(
        srt,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="podcast_{episode_id}.srt"'
        },
    )


@router.get("/{episode_id}/export/script")
def export_script(episode_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import PlainTextResponse

    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    try:
        ha = json.loads(episode.host_a_config) if episode.host_a_config else {}
        hb = json.loads(episode.host_b_config) if episode.host_b_config else None
    except json.JSONDecodeError:
        ha, hb = {}, None
    hosts = {"主播 A": ha}
    if hb:
        hosts["主播 B"] = hb
    title = episode.name or f"单集 {episode.id}"
    md = AudioService.build_script_markdown(
        title=title,
        script=episode.script,
        mode="dialogue" if hb else "single",
        hosts=hosts,
    )
    return PlainTextResponse(
        md,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="podcast_{episode_id}.md"'
        },
    )
