"""Edge TTS 免费神经语音引擎。

非 OpenAI 兼容：通过 edge-tts 库调用微软在线服务。
仅支持内置音色；输出统一转成 wav，与后端合并/响度归一流水线兼容。

注意：微软侧偶发返回空流（NoAudioReceived），常见于限流 / 时钟校验 /
瞬时网络抖动。这里做有限次退避重试，而不是把一次失败直接抛给用户。
"""
from __future__ import annotations

import re
import uuid
import asyncio
import logging
from pathlib import Path
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)

# 精选常用音色，降低小白选择成本；完整列表可自行扩展
EDGE_VOICES: list[dict[str, str]] = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "lang": "中文", "gender": "女"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "lang": "中文", "gender": "女"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "lang": "中文", "gender": "男"},
    {"id": "zh-CN-YunxiNeural", "name": "云希", "lang": "中文", "gender": "男"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏", "lang": "中文", "gender": "男"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "lang": "中文", "gender": "男"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻", "lang": "中文（台湾）", "gender": "女"},
    {"id": "zh-TW-YunJheNeural", "name": "雲哲", "lang": "中文（台湾）", "gender": "男"},
    {"id": "zh-HK-HiuMaanNeural", "name": "曉曼", "lang": "中文（香港）", "gender": "女"},
    {"id": "zh-HK-WanLungNeural", "name": "雲龍", "lang": "中文（香港）", "gender": "男"},
    {"id": "en-US-AriaNeural", "name": "Aria", "lang": "英文（美）", "gender": "女"},
    {"id": "en-US-JennyNeural", "name": "Jenny", "lang": "英文（美）", "gender": "女"},
    {"id": "en-US-GuyNeural", "name": "Guy", "lang": "英文（美）", "gender": "男"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher", "lang": "英文（美）", "gender": "男"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia", "lang": "英文（英）", "gender": "女"},
    {"id": "en-GB-RyanNeural", "name": "Ryan", "lang": "英文（英）", "gender": "男"},
]

DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_EDGE_VOICE_B = "zh-CN-YunxiNeural"

# 语速预设 → Edge rate（相对默认语速的百分比）
SPEED_TO_RATE = {
    "很慢": "-25%",
    "偏慢": "-10%",
    "正常": "+0%",
    "偏快": "+15%",
    "很快": "+30%",
}

# MiMo/ChatTTS 风格标签在 Edge 上会原样念出，必须去掉
_TAG_RE = re.compile(r"[（(][^（()）]{1,24}[)）]")
# 控制字符 / 零宽字符：微软侧对脏字符可能直接空流
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ﻿]")

# 同一进程内限制在飞请求，降低触发微软侧空流/限流的概率
_MAX_EDGE_CONCURRENCY = 2
# WeakKeyDictionary：loop 结束后自动回收，避免跨 loop 复用陈旧锁
_edge_semaphores: "WeakKeyDictionary" = WeakKeyDictionary()


def _get_edge_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _edge_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_MAX_EDGE_CONCURRENCY)
        _edge_semaphores[loop] = sem
    return sem

# 空流 / 瞬时网络错误的重试策略（微软侧偶发性较强，多退避几轮）
_EDGE_RETRIES = 4
_EDGE_RETRY_BASE_DELAY = 1.0


def strip_tts_tags(text: str) -> str:
    cleaned = _TAG_RE.sub("", text or "")
    cleaned = _CTRL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def resolve_voice(voice_id: str | None) -> str:
    if not voice_id:
        return DEFAULT_EDGE_VOICE
    known = {v["id"] for v in EDGE_VOICES}
    if voice_id in known:
        return voice_id
    # 允许直接传完整 Edge 音色 ID（不在精选表里也能用）
    if re.match(r"^[a-z]{2,3}-[A-Za-z0-9-]+Neural$", voice_id):
        return voice_id
    return DEFAULT_EDGE_VOICE


def rate_for_speed(speed: str | None) -> str:
    if not speed:
        return "+0%"
    return SPEED_TO_RATE.get(speed, "+0%")


def _friendly_error(exc: BaseException) -> str:
    msg = str(exc) or exc.__class__.__name__
    name = exc.__class__.__name__
    if name == "NoAudioReceived" or "No audio was received" in msg:
        return (
            "Edge TTS 未返回音频（微软免费通道限流或瞬时故障）。"
            "请稍后重试；若频繁失败，请在演播设定切换到 MiMo TTS"
        )
    if "403" in msg or "handshake" in msg.lower():
        return "Edge TTS 连接被拒绝（网络限制或校验失败）；可稍后重试，或切换到 MiMo TTS"
    if name == "TimeoutError" or "timeout" in msg.lower():
        return "Edge TTS 超时；请检查网络后重试"
    return f"Edge TTS 合成失败: {msg}"


async def _save_once(
    speak: str,
    *,
    voice: str,
    rate: str,
    tmp_mp3: Path,
    timeout: float,
) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(speak, voice=voice, rate=rate)
    await asyncio.wait_for(communicate.save(str(tmp_mp3)), timeout=timeout)
    if not tmp_mp3.exists() or tmp_mp3.stat().st_size == 0:
        raise RuntimeError("Edge TTS 未返回音频数据")


async def synthesize_to_wav(
    text: str,
    *,
    voice_id: str | None = None,
    speed: str | None = None,
    dest_dir: Path | None = None,
    timeout: float = 60.0,
) -> str:
    """合成一句话并落盘为 wav，返回路径。"""
    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "未安装 edge-tts，请在后端虚拟环境执行：pip install edge-tts"
        ) from e

    from pydub import AudioSegment

    from app.config import AUDIO_DIR, TEMP_DIR

    speak = strip_tts_tags(text)
    if not speak:
        raise ValueError("文本为空（可能全是语气标签），无法合成")

    voice = resolve_voice(voice_id)
    rate = rate_for_speed(speed)
    target_dir = dest_dir or AUDIO_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out_wav = target_dir / f"tts_edge_{uuid.uuid4().hex[:8]}.wav"
    tmp_mp3 = (dest_dir or TEMP_DIR) / f"tts_edge_{uuid.uuid4().hex[:8]}.mp3"
    tmp_mp3.parent.mkdir(parents=True, exist_ok=True)

    sem = _get_edge_semaphore()
    last_exc: BaseException | None = None
    async with sem:
        for attempt in range(_EDGE_RETRIES):
            try:
                await _save_once(
                    speak, voice=voice, rate=rate, tmp_mp3=tmp_mp3, timeout=timeout
                )
                last_exc = None
                break
            except asyncio.TimeoutError as e:
                last_exc = e
            except Exception as e:
                last_exc = e
                # 空流 / 握手 / 连接类错误可重试；文本非法直接失败
                name = e.__class__.__name__
                retryable = (
                    name in {"NoAudioReceived", "WebSocketError", "UnexpectedResponse"}
                    or "No audio was received" in str(e)
                    or "403" in str(e)
                    or "handshake" in str(e).lower()
                    or "未返回音频" in str(e)
                    or isinstance(e, ConnectionError)
                )
                if not retryable:
                    break
            tmp_mp3.unlink(missing_ok=True)
            if attempt < _EDGE_RETRIES - 1:
                delay = _EDGE_RETRY_BASE_DELAY * (2**attempt) * (0.7 + 0.6 * (attempt % 2))
                logger.warning(
                    "Edge TTS 第 %d/%d 次失败，%.1fs 后重试: %s",
                    attempt + 1,
                    _EDGE_RETRIES,
                    delay,
                    last_exc,
                )
                await asyncio.sleep(delay)

        if last_exc is not None:
            tmp_mp3.unlink(missing_ok=True)
            raise RuntimeError(_friendly_error(last_exc)) from last_exc

    if not tmp_mp3.exists() or tmp_mp3.stat().st_size == 0:
        tmp_mp3.unlink(missing_ok=True)
        raise RuntimeError("Edge TTS 未返回音频数据，请稍后重试")

    try:
        # Edge 输出 mp3；转 wav 以兼容合并/loudnorm 流水线
        audio = AudioSegment.from_file(tmp_mp3, format="mp3")
        if audio.channels != 1:
            audio = audio.set_channels(1)
        audio.export(out_wav, format="wav")
    except Exception as e:
        out_wav.unlink(missing_ok=True)
        raise RuntimeError(f"Edge TTS 音频转码失败: {e}") from e
    finally:
        tmp_mp3.unlink(missing_ok=True)

    logger.info("Edge TTS 合成完成: %s voice=%s rate=%s", out_wav, voice, rate)
    return str(out_wav)
