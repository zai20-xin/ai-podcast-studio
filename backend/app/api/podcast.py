"""播客合成 API"""
import re
import json
import os
import time
import hashlib
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
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
from app.services import task_runner
from app.config import (
    AUDIO_DIR,
    SILENCE_GAP_MS,
    SILENCE_GAP_SHORT_MS,
    GAP_LEVEL_TO_MS,
    GAP_NONE,
    GAP_SHORT,
    GAP_PARAGRAPH,
    GAP_SECTION,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/podcast", tags=["podcast"])

MAX_DIALOGUE_LINES = 200
PROCESSING_STALE_MINUTES = 45
# 连续多少句失败即判定为系统性问题（例如密钥失效、服务不可用），中止剩余分句以免空耗额度
FAIL_FAST_THRESHOLD = 3

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
    """把脚本解析成台词列表，并标注每句之后应留出的停顿层级。

    停顿层级完全由脚本结构决定：普通换行 = 句间，空行 = 段落，【章节】= 章节。
    这样合并与字幕才能做出有层次的停顿，而不是从头到尾同一个值
    （单一停顿是「AI 播客听起来平」的主要来源）。

    这里**不做截断**：原实现直接 `dialogue[:MAX_DIALOGUE_LINES]`，超出 200 句的部分
    被静默丢弃，用户会以为整篇都合成了。超限改由 assert_within_line_limit 显式报错。
    """
    raw = script.split("\n")
    total = len(raw)
    entries: list[list] = []  # [line, gap_level]
    for i, raw_line in enumerate(raw):
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^【[^】]+】$", line) or line.startswith("---"):
            # 章节分隔：把它前面那句的停顿升级为章节级
            if entries:
                entries[-1][1] = GAP_SECTION
            continue
        # 向后看一行，判断这句之后是句间、段落还是章节
        gap = GAP_SHORT
        if i + 1 < total:
            nxt = raw[i + 1].strip()
            if not nxt:
                gap = GAP_PARAGRAPH
            elif re.match(r"^【[^】]+】$", nxt) or nxt.startswith("---"):
                gap = GAP_SECTION
        entries.append([line, gap])

    dialogue = []
    for line, gap in entries:
        match = re.match(r"^(.+?)[：:]\s*(.+)$", line)
        if match:
            speaker = re.sub(r"^【[^】]+】", "", match.group(1).strip()).strip()
            content = match.group(2).strip()
            if not content:
                continue
            dialogue.append({"speaker": speaker, "text": content, "gap_after": gap})
        else:
            if not line.startswith("【"):
                dialogue.append({"speaker": "", "text": line, "gap_after": gap})

    # 最后一句之后没有内容了，不需要停顿
    if dialogue:
        dialogue[-1]["gap_after"] = GAP_NONE
    return dialogue


def assert_within_line_limit(dialogue: list[dict]) -> None:
    if len(dialogue) > MAX_DIALOGUE_LINES:
        raise ValueError(
            f"脚本解析出 {len(dialogue)} 句，超过单集上限 {MAX_DIALOGUE_LINES} 句，"
            "请拆成多集后再合成"
        )


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


def _config_fingerprint(
    config: HostConfig,
    global_instruction: str | None,
    text: str,
    speaker: str,
) -> str:
    """把「决定这条音频听感」的全部输入压成一个指纹。

    指纹一致 => 该句音频可以原样复用，跳过 TTS 调用；
    音色 / 风格 / 参考音频 / 全局指令 / 文本 任一变化，指纹即改变，该句重做。
    """
    reference = config.reference_audio
    reference_mtime = None
    if reference:
        try:
            reference_mtime = os.path.getmtime(reference)
        except OSError:
            reference_mtime = None
    payload = {
        "model_type": _model_type_str(config),
        "voice_id": config.voice_id,
        "reference_audio": reference,
        "reference_audio_mtime": reference_mtime,
        "voice_description": config.voice_description,
        "style": config.style,
        "speed": config.speed,
        "emotion": config.emotion,
        "audio_tag_style": config.audio_tag_style,
        "global_instruction": global_instruction,
        "speaker": speaker,
        "text": text,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
        assert_within_line_limit(dialogue_lines)

        seg_dir = _segment_dir(episode_id)
        previous = _load_segments(episode)
        existing = {
            s.get("index"): s
            for s in previous
            if isinstance(s.get("index"), int)
        }
        previous_meta = {
            s.get("kind"): s for s in previous if s.get("kind") in ("intro", "outro")
        }
        # 每句生效的主播配置：构建与合成两个阶段共用，避免重复计算
        seg_hosts = [
            _pick_host_config(line["speaker"], host_a, host_b) for line in dialogue_lines
        ]

        segments: list[dict] = []
        for i, line in enumerate(dialogue_lines):
            prev = existing.get(i)
            fingerprint = _config_fingerprint(
                seg_hosts[i], episode.global_instruction, line["text"], line["speaker"]
            )
            # 指纹一致且文件仍在 => 复用旧音频，这一句不再请求 TTS
            reusable = bool(prev) and prev.get("fingerprint") == fingerprint
            audio_path = prev.get("audio_path") if reusable else None
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
                "fingerprint": fingerprint,
                # 这句之后应留的停顿：由脚本结构（句间 / 段落 / 章节）决定
                "gap_after_ms": GAP_LEVEL_TO_MS.get(
                    int(line.get("gap_after", GAP_SHORT)), SILENCE_GAP_MS
                ),
            })

        intro_text = (episode.intro_text or "").strip()
        outro_text = (episode.outro_text or "").strip()
        has_intro = bool(intro_text)
        has_outro = bool(outro_text)

        # 片头/片尾也计入进度
        episode.status = "processing"
        episode.progress_total = len(segments) + (1 if has_intro else 0) + (1 if has_outro else 0)
        done_lines = sum(1 for s in segments if s["status"] == "done")
        intro_state = {
            "kind": "intro",
            "text": intro_text,
            "status": "pending",
            "audio_path": None,
            "error": None,
            "fingerprint": _config_fingerprint(
                host_a, episode.global_instruction, intro_text, "__intro__"
            ),
        }
        outro_state = {
            "kind": "outro",
            "text": outro_text,
            "status": "pending",
            "audio_path": None,
            "error": None,
            "fingerprint": _config_fingerprint(
                host_a, episode.global_instruction, outro_text, "__outro__"
            ),
        }
        intro_file = seg_dir / "intro.wav"
        outro_file = seg_dir / "outro.wav"

        def _meta_reusable(state: dict, target: Path) -> bool:
            """片头/片尾同样按指纹判断：配置没变且文件在，就不重新合成。"""
            prev = previous_meta.get(state["kind"])
            return (
                bool(prev)
                and prev.get("fingerprint") == state["fingerprint"]
                and target.exists()
                and target.stat().st_size > 0
            )

        if has_intro and _meta_reusable(intro_state, intro_file):
            intro_state["status"] = "done"
            intro_state["audio_path"] = str(intro_file)
        if has_outro and _meta_reusable(outro_state, outro_file):
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
            # 片头/片尾固定沿用主播 A 的音色配置（当前 UI 未提供单独选择项）
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
                audio_tag_style=host_a.audio_tag_style,
                dest_dir=seg_dir,
            )
            if Path(path) != dest:
                Path(path).replace(dest)
            return str(dest)

        if has_intro and intro_state["status"] != "done":
            if task_runner.is_cancelled(episode_id):
                raise asyncio.CancelledError()
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

        intro_done = 1 if intro_state["status"] == "done" else 0
        outro_done = 1 if outro_state["status"] == "done" else 0

        def _update_progress() -> None:
            done_lines = sum(1 for s in segments if s["status"] == "done")
            episode.progress_current = done_lines + intro_done + outro_done

        pending = [s for s in segments if s["status"] != "done"]

        if pending:

            async def _synth_one(seg: dict) -> tuple[dict, str | None, str | None]:
                """合成单句。异常在内部收拢成 (seg, None, error)，便于并发收结果。"""
                config = seg_hosts[seg["index"]]
                try:
                    produced = await tts.synthesize(
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
                    target = seg_dir / f"{seg['index']:03d}.wav"
                    if Path(produced) != target:
                        Path(produced).replace(target)
                    return seg, str(target), None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    return seg, None, friendly_error(e)

            # 并发下发所有待合成句；实际同时在飞的数量由 TTSService 的闸门约束
            tasks = [asyncio.create_task(_synth_one(s)) for s in pending]
            consecutive_failures = 0
            try:
                for finished in asyncio.as_completed(tasks):
                    if task_runner.is_cancelled(episode_id):
                        logger.info("单集 %s 收到取消信号，停止后续分句", episode_id)
                        raise asyncio.CancelledError()
                    seg, path, err = await finished
                    if err:
                        logger.warning("分句 %s 失败: %s", seg["index"], err)
                        seg["status"] = "error"
                        seg["error"] = err
                        consecutive_failures += 1
                    else:
                        seg["audio_path"] = path
                        seg["status"] = "done"
                        seg["error"] = None
                        consecutive_failures = 0
                    # 进度与落库统一在主协程串行处理，避免多个协程并发写同一个 session
                    _update_progress()
                    _persist()
                    if consecutive_failures >= FAIL_FAST_THRESHOLD:
                        logger.warning(
                            "单集 %s 连续 %d 句失败，判定为系统性问题，中止剩余分句",
                            episode_id,
                            consecutive_failures,
                        )
                        break
            finally:
                # 中止或取消时回收尚未完成的请求
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        # 有失败分句则如实上报；已成功的句子保留，可直接续跑
        failed = [s for s in segments if s["status"] == "error"]
        if failed:
            first = failed[0]
            episode.status = "error"
            episode.error_message = (
                f"{len(failed)} 句合成失败（首条：第 {first['index'] + 1} 句 {first['error']}），"
                "可直接续跑，已成功的句子不会重做"
            )
            _update_progress()
            _persist()
            return

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
            SILENCE_GAP_MS,
            True,
            intro_state["audio_path"] if has_intro else None,
            outro_state["audio_path"] if has_outro else None,
            # 逐句停顿：段落与章节处会明显更长，做出呼吸感
            [s.get("gap_after_ms") for s in segments],
        )

        episode.audio_path = final_path
        episode.status = "done"
        episode.progress_current = episode.progress_total
        episode.error_message = None
        _persist()
        logger.info("单集 %s 合成完成（%d 句）", episode_id, len(segments))
    except asyncio.CancelledError:
        # 用户主动停止：已合成的分句均已落盘，稍后可直接续跑
        logger.info("单集 %s 合成被取消", episode_id)
        try:
            episode = db.query(Episode).filter(Episode.id == episode_id).first()
            if episode:
                episode.status = "cancelled"
                episode.error_message = None
                episode.processing_heartbeat = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise
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
        "max_lines": MAX_DIALOGUE_LINES,
        # 让前端在编辑阶段就能提示超限，不必等到合成时才失败
        "over_limit": len(dialogue) > MAX_DIALOGUE_LINES,
        "dialogue": dialogue[:80],
    }


def _reclaim_if_stale(episode: Episode) -> bool:
    """进程被杀导致 processing 卡死时，超时后允许重新合成"""
    if episode.status != "processing":
        return False
    if task_runner.is_running(episode.id) or _episode_lock(episode.id).locked():
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
    db: Session = Depends(get_db),
):
    episode = db.query(Episode).filter(Episode.id == data.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    _assert_not_busy(episode)
    if task_runner.is_running(episode.id):
        raise HTTPException(status_code=409, detail="该单集已有合成任务在运行")

    dialogue_lines = parse_dialogue_lines(episode.script)
    if not dialogue_lines:
        raise HTTPException(status_code=400, detail="脚本为空或无法解析出有效台词")
    try:
        assert_within_line_limit(dialogue_lines)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    extra = (1 if (episode.intro_text or "").strip() else 0) + (
        1 if (episode.outro_text or "").strip() else 0
    )
    episode.status = "processing"
    episode.progress_current = 0
    episode.progress_total = len(dialogue_lines) + extra
    episode.error_message = None
    episode.processing_heartbeat = datetime.utcnow()
    # 刻意保留既有 segments_json：_run_synthesis 会按配置指纹复用未变化的句子，
    # 否则「只改一句话」也会导致整篇重新合成。
    db.commit()

    # is_running 与 start 之间没有 await，不会被并发请求插入，故必然启动成功
    task_runner.start(episode.id, _run_synthesis(episode.id))
    return {
        "message": "合成已开始",
        "episode_id": episode.id,
        "total_lines": len(dialogue_lines) + extra,
        "estimated_seconds": estimate_duration_seconds(dialogue_lines),
    }


def estimate_duration_seconds(lines: list[dict], chars_per_second: float = 4.2) -> int:
    """粗估成片时长：中文约 4 字/秒 + 逐句停顿（分级 gap 优先，否则回退句均值）"""
    total_chars = sum(len(l.get("text") or "") for l in lines)
    gap_ms = 0.0
    for i, line in enumerate(lines):
        if i == len(lines) - 1:
            break  # 末句之后不停顿
        if "gap_after_ms" in line and line["gap_after_ms"] is not None:
            gap_ms += float(line["gap_after_ms"])
        else:
            gap_ms += SILENCE_GAP_SHORT_MS
    return int(total_chars / chars_per_second + gap_ms / 1000.0)


@router.post("/episodes/{episode_id}/estimate")
def estimate_synthesis(episode_id: int, db: Session = Depends(get_db)):
    """合成前预估：句数 / 字数 / 时长"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    lines = parse_dialogue_lines(episode.script)
    if not lines:
        raise HTTPException(status_code=400, detail="脚本为空或无法解析出有效台词")
    try:
        assert_within_line_limit(lines)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    db: Session = Depends(get_db),
):
    """失败/中止后从断点继续：只重合成未完成句，已成功的句子按指纹复用"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")
    _assert_not_busy(episode)
    if task_runner.is_running(episode_id):
        raise HTTPException(status_code=409, detail="该单集已有合成任务在运行")

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

    task_runner.start(episode_id, _run_synthesis(episode_id))
    return {
        "message": "已开始续跑",
        "episode_id": episode.id,
        "pending_lines": pending,
    }


@router.post("/episodes/{episode_id}/cancel")
async def cancel_synthesis(episode_id: int, db: Session = Depends(get_db)):
    """停止正在进行的合成。已完成的分句会保留，之后可用 retry 续跑。"""
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="单集不存在")

    stopped = task_runner.cancel(episode_id)
    if episode.status == "processing":
        episode.status = "cancelled"
        episode.error_message = None
        db.commit()

    return {
        "episode_id": episode_id,
        "cancelled": stopped,
        "message": "已停止合成，已完成的分句会保留" if stopped else "当前没有正在运行的合成任务",
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
    except (ValueError, OSError) as e:
        # 一并捕获 OSError：克隆模式下参考音频丢失时抛的是 FileNotFoundError，
        # 原实现只认 ValueError / RuntimeError，会漏成 500「服务器内部错误」。
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
    # 字幕时间轴要与实际拼接一致：正文之外还要算上片头时长与句间停顿，
    # 否则偏移会随句数累积（第 N 句约偏 0.6×(N-1) 秒）。
    body = [s for s in segments if isinstance(s.get("index"), int)]
    if not body:
        raise HTTPException(status_code=400, detail="尚无分句数据，请先合成")
    intro = next((s for s in segments if s.get("kind") == "intro"), None)
    outro = next((s for s in segments if s.get("kind") == "outro"), None)
    audio = AudioService()
    srt = audio.build_srt(
        body,
        silence_gap_ms=SILENCE_GAP_MS,
        intro_duration_ms=audio.wav_duration_ms(intro.get("audio_path")) if intro else 0,
        has_outro=bool(outro and outro.get("audio_path")),
    )
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
