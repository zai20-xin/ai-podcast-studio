"""TTS 服务封装 — MiMo（OpenAI 兼容）与 Edge TTS（免费旁路）"""
import os
import base64
import uuid
import random
import asyncio
import logging
from pathlib import Path
from weakref import WeakKeyDictionary

from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError

from app.config import (
    AUDIO_DIR,
    TEMP_DIR,
    DEFAULT_BASE_URL,
    TTS_TIMEOUT_SECONDS,
    TTS_MAX_RETRIES,
    TTS_CONCURRENCY,
    TTS_RATE_LIMIT_RETRIES,
    TTS_RATE_LIMIT_BASE_DELAY,
    TTS_RATE_LIMIT_MAX_DELAY,
    MAX_REFERENCE_AUDIO_BYTES,
    MAX_REFERENCE_AUDIO_B64_BYTES,
    runtime_config,
)
from app.providers import create_async_client, get_provider

logger = logging.getLogger(__name__)

MODEL_IDS = {
    "builtin": "mimo-v2.5-tts",
    "design": "mimo-v2.5-tts-voicedesign",
    "clone": "mimo-v2.5-tts-voiceclone",
}

# 语气修正短语：只描述「怎么说」的语气质感。
# 刻意不涉及语速（由 SPEED_PRESETS 唯一负责）与音色（由 voice_id 决定）。
# 旧版本 14 条里有 12 条自带「语速偏慢 / 语速急促」之类的表述，与语速字典叠加后
# 会让同一条指令对语速出现多种互斥要求，反而让模型行为不可预测。
STYLE_PRESETS = {
    "温柔": "语气温柔轻缓，像在安抚对方",
    "兴奋": "语气兴奋昂扬，透着按捺不住的劲头",
    "悲伤": "语气低沉克制，像在压着情绪说话",
    "愤怒": "语气凌厉带火气，字字用力",
    "严肃": "语气正式沉稳，带着不容置疑的分量",
    "幽默": "语气诙谐松弛，话里带着笑意",
    "紧张": "语气发紧发慌，像在赶时间",
    "平静": "语气平和从容，情绪不外露",
    "叙述": "用讲故事的口吻娓娓道来，有画面感",
    "新闻播报": "播报腔，客观克制，不带个人情绪",
    "撒娇": "语气软糯黏人，尾音拖着不肯放",
    "磁性低沉": "语气低回放松，像深夜电台",
    "活泼可爱": "语气轻快跳脱，朝气外放",
    "苍老": "语气迟缓沧桑，像在回忆往事",
}

SPEED_PRESETS = {
    "很慢": "语速非常缓慢，每个字都拖长，像在读诗",
    "偏慢": "语速偏慢，从容不迫",
    "正常": "语速适中",
    "偏快": "语速偏快，节奏紧凑",
    "很快": "语速非常快，像连珠炮一样",
}

AUDIO_TAG_STYLE_PRESETS = {
    "开心": "开心", "悲伤": "悲伤", "愤怒": "愤怒", "恐惧": "恐惧",
    "惊讶": "惊讶", "兴奋": "兴奋", "委屈": "委屈", "平静": "平静", "冷漠": "冷漠",
    "温柔": "温柔", "高冷": "高冷", "活泼": "活泼", "严肃": "严肃",
    "慵懒": "慵懒", "俏皮": "俏皮", "深沉": "深沉", "干练": "干练", "凌厉": "凌厉",
    "磁性": "磁性", "醇厚": "醇厚", "清亮": "清亮", "空灵": "空灵",
    "稚嫩": "稚嫩", "苍老": "苍老", "甜美": "甜美", "沙哑": "沙哑",
    "东北话": "东北话", "四川话": "四川话", "河南话": "河南话", "粤语": "粤语",
}

AUDIO_TAG_INLINE_PRESETS = {
    "吸气": "吸气", "深呼吸": "深呼吸", "叹气": "叹气", "喘息": "喘息",
    "紧张": "紧张", "害怕": "害怕", "激动": "激动", "疲惫": "疲惫",
    "委屈": "委屈", "撒娇": "撒娇", "心虚": "心虚", "震惊": "震惊",
    "颤抖": "颤抖", "破音": "破音", "鼻音": "鼻音", "气声": "气声",
    "笑": "笑", "轻笑": "轻笑", "大笑": "大笑", "冷笑": "冷笑",
    "抽泣": "抽泣", "呜咽": "呜咽", "哽咽": "哽咽", "嚎啕大哭": "嚎啕大哭",
}

# ChatTTS 风格标签 → MiMo
TAG_CONVERT_MAP = {
    "[uv_break]": "(停顿)",
    "[laugh]": "(轻笑)",
    "[breath]": "(深呼吸)",
    "[cough]": "(咳嗽)",
    "[sigh]": "(叹气)",
    "[whisper]": "(低语)",
}


def convert_tags(text: str) -> str:
    result = text
    for old_tag, new_tag in TAG_CONVERT_MAP.items():
        result = result.replace(old_tag, new_tag)
    return result


# 并发闸门：按事件循环缓存，使同一进程内所有 TTSService 实例共享同一个并发上限。
# TTSService 是「每次请求新建」的，若把信号量挂在实例上则完全起不到限制作用。
# 用 WeakKeyDictionary：loop 结束后自动回收，避免 id(loop) 复用拿到陈旧 Semaphore。
_semaphores: WeakKeyDictionary = WeakKeyDictionary()


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(TTS_CONCURRENCY)
        _semaphores[loop] = sem
    return sem


class TTSService:
    def __init__(self):
        provider_id = runtime_config.tts_provider or "mimo"
        spec = get_provider("tts", provider_id)
        self.provider_id = provider_id
        self.spec = spec
        self.supported_model_types = (
            tuple(spec.supported_model_types) if spec else ("builtin", "design", "clone")
        )

        # Edge TTS：非 OpenAI 协议，无 Key，单独引擎
        if provider_id == "edge-tts":
            self.api_key = ""
            self.base_url = ""
            self.client = None
            self.is_edge = True
            return

        self.is_edge = False
        self.api_key = runtime_config.api_key
        self.base_url = runtime_config.base_url or (spec.default_base_url if spec else DEFAULT_BASE_URL)
        if not self.api_key:
            label = spec.name if spec else "TTS"
            raise ValueError(f"需要配置 {label} 的 API Key（设置页可填，或切换到免费的 Edge TTS）")
        if spec and spec.status != "supported":
            logger.warning(
                "TTS 供应商 %s 状态为 %s：仅按 OpenAI 兼容协议适配，未完整验证",
                provider_id,
                spec.status,
            )
        self.client = create_async_client(
            api_key=self.api_key,
            base_url=self.base_url,
            provider_id=provider_id,
            kind="tts",
            timeout=TTS_TIMEOUT_SECONDS,
            max_retries=TTS_MAX_RETRIES,
        )

    def _encode_audio(self, audio_path: str) -> str:
        from app.config import VOICES_DIR

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        if not path.is_file():
            raise ValueError(f"参考音频路径无效: {audio_path}")
        # 仅允许读取克隆音色目录下的文件，防止任意路径读取外发
        try:
            resolved = path.resolve()
            voices_root = VOICES_DIR.resolve()
            if not resolved.is_relative_to(voices_root):
                raise ValueError("参考音频必须位于服务端克隆音色目录")
        except AttributeError:
            # Python < 3.9 fallback
            if not str(path.resolve()).startswith(str(VOICES_DIR.resolve())):
                raise ValueError("参考音频必须位于服务端克隆音色目录")
        suffix = path.suffix.lower().lstrip(".")
        mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav"}
        if suffix not in mime_map:
            raise ValueError(f"不支持的音频格式: {suffix}，仅支持 wav 和 mp3")
        size = path.stat().st_size
        # 粗筛：base64 会膨胀约 4/3，先用折算后的原始体积上限挡住超大文件，避免整个读进内存
        if size > MAX_REFERENCE_AUDIO_BYTES:
            raise ValueError(
                f"参考音频 {size / 1024 / 1024:.1f}MB 超过上限 "
                f"{MAX_REFERENCE_AUDIO_BYTES / 1024 / 1024:.1f}MB（MiMo 限制 Base64 编码后不超过 10MB）"
            )
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        # 服务端约束落在编码后的字符串上，这里按同一口径复核
        if len(b64) > MAX_REFERENCE_AUDIO_B64_BYTES:
            raise ValueError(
                f"参考音频 Base64 编码后为 {len(b64) / 1024 / 1024:.1f}MB，"
                "超过 MiMo 的 10MB 上限，请换用更短或更低码率的样本"
            )
        return f"data:{mime_map[suffix]};base64,{b64}"

    def _build_instruction(
        self,
        style: str | None = None,
        speed: str | None = None,
        emotion: str | None = None,
        global_instruction: str | None = None,
    ) -> str:
        """组装发给 TTS 的「导演指令」。

        MiMo TTS 的约定（实测）：
        - 只用 user（指令）+ assistant（待读文本），**不支持 system**
        - 指令过长会稀释重点，且会拉长语速；应短而完整
        - 维度职责分离：global 只写「怎么演」，语速只出现一次

        维度职责分离，避免同一维度被多个来源重复描述：
          - global_instruction：情境描述（角色 / 场景 / 怎么演）
          - speed：语速的唯一来源
          - style / emotion：可选微调，默认留空
        """
        parts: list[str] = []

        direction = (global_instruction or "").strip()
        # 实测长指令会显著拉长成片；截到约 300 字仍保留角色/场景核心
        if len(direction) > 300:
            direction = direction[:300].rstrip() + "…"
        direction = direction.rstrip("。，,.")
        if direction:
            parts.append(direction)

        # 语速全流程只在这里出现一次；放在情境之后，作为对「节奏」的明确覆盖
        if speed:
            parts.append(SPEED_PRESETS.get(speed) or f"语速{speed}")

        if style:
            parts.append(STYLE_PRESETS.get(style) or style)

        if emotion:
            emotion_text = str(emotion).strip()
            if emotion_text:
                parts.append(f"整体情绪偏{emotion_text}")

        return "。".join(parts)

    def _build_audio_tag_text(self, text: str, audio_tag_style: str | None) -> str:
        text = convert_tags(text)
        if audio_tag_style and audio_tag_style in AUDIO_TAG_STYLE_PRESETS:
            tag = AUDIO_TAG_STYLE_PRESETS[audio_tag_style]
            return f"({tag}){text}"
        return text

    def _save_audio(self, audio_data_b64: str, prefix: str, dest_dir: Path | None = None) -> str:
        audio_bytes = base64.b64decode(audio_data_b64)
        output_name = f"{prefix}_{uuid.uuid4().hex[:8]}.wav"
        target_dir = dest_dir or AUDIO_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / output_name
        output_path.write_bytes(audio_bytes)
        logger.info("合成完成: %s (%d bytes)", output_path, len(audio_bytes))
        return str(output_path)

    def _messages_for(
        self,
        text: str,
        style: str | None,
        speed: str | None,
        emotion: str | None,
        global_instruction: str | None,
        audio_tag_style: str | None,
        context_prev: str | None = None,
    ) -> list[dict]:
        """构造 chat messages。

        context_prev：同一主播上一句已合成文本。实测把上一句放进多轮
        history 会改变韵律（成片更连贯、更像在「接着说」），因此播客正文
        应按脚本顺序串行，并带上上一句。
        """
        instruction = self._build_instruction(style, speed, emotion, global_instruction)
        speak_text = self._build_audio_tag_text(text, audio_tag_style)
        messages: list[dict] = []
        if instruction:
            messages.append({"role": "user", "content": instruction})
        if context_prev:
            messages.append({"role": "assistant", "content": context_prev})
            messages.append({"role": "user", "content": "继续说，语气和节奏保持连贯"})
        messages.append({"role": "assistant", "content": speak_text})
        return messages

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """指数退避 + 抖动；asyncio.sleep 可被取消，等待期间仍能响应停止请求"""
        delay = min(TTS_RATE_LIMIT_BASE_DELAY * (2**attempt), TTS_RATE_LIMIT_MAX_DELAY)
        delay *= 0.5 + random.random() * 0.5
        logger.info("触发限流，%.1fs 后重试（第 %d 次）", delay, attempt + 1)
        await asyncio.sleep(delay)

    async def _create_completion(self, model: str, messages: list[dict], audio_params: dict):
        """受并发闸门约束的单次调用；对限流/连接错误/5xx 做退避重试。

        CancelledError 继承自 BaseException，不会被下面的 except 分支吞掉，
        因此任务被取消时能正常向上传播，并中断正在进行的 HTTP 请求。
        SDK 的 max_retries 保持 0，避免与这里的退避叠成双层重试。
        """
        attempt = 0
        while True:
            try:
                async with _get_semaphore():
                    return await self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        audio=audio_params,
                    )
            except asyncio.CancelledError:
                raise
            except RateLimitError as e:
                if attempt >= TTS_RATE_LIMIT_RETRIES:
                    raise RuntimeError(
                        f"TTS 持续限流（已重试 {attempt} 次），"
                        "请调低 TTS_CONCURRENCY 或稍后再试"
                    ) from e
                await self._backoff(attempt)
                attempt += 1
            except APITimeoutError as e:
                if attempt >= TTS_RATE_LIMIT_RETRIES:
                    raise RuntimeError(f"TTS 请求超时（{TTS_TIMEOUT_SECONDS}s）") from e
                logger.warning("TTS 超时，退避后重试（第 %d 次）", attempt + 1)
                await self._backoff(attempt)
                attempt += 1
            except APIConnectionError as e:
                if attempt >= TTS_RATE_LIMIT_RETRIES:
                    raise RuntimeError("TTS 网络连接失败，请检查网络后重试") from e
                logger.warning("TTS 连接失败，退避后重试（第 %d 次）: %s", attempt + 1, e)
                await self._backoff(attempt)
                attempt += 1
            except APIError as e:
                status = getattr(e, "status_code", None)
                if status is not None and status >= 500 and attempt < TTS_RATE_LIMIT_RETRIES:
                    logger.warning("TTS 服务端 %s，退避后重试（第 %d 次）", status, attempt + 1)
                    await self._backoff(attempt)
                    attempt += 1
                    continue
                raise RuntimeError(f"TTS API 错误: {e}") from e

    async def synthesize(
        self,
        text: str,
        model_type: str = "builtin",
        voice_id: str | None = None,
        reference_audio: str | None = None,
        voice_description: str | None = None,
        style: str | None = None,
        speed: str | None = None,
        emotion: str | None = None,
        global_instruction: str | None = None,
        audio_tag_style: str | None = None,
        dest_dir: Path | None = None,
        context_prev: str | None = None,
    ) -> str:
        """合成语音，返回落盘路径。context_prev 为同主播上一句，用于韵律连贯。"""
        if model_type not in self.supported_model_types:
            allowed = " / ".join(self.supported_model_types)
            raise ValueError(
                f"当前 TTS 供应商（{self.provider_id}）不支持「{model_type}」模式，仅支持：{allowed}"
            )

        if self.is_edge:
            from app.services.edge_tts_engine import synthesize_to_wav

            # Edge 无「导演指令」语义：仅取语速；标签会原样念出，由引擎剥离
            return await synthesize_to_wav(
                text,
                voice_id=voice_id,
                speed=speed,
                dest_dir=dest_dir,
                timeout=TTS_TIMEOUT_SECONDS,
            )

        messages = self._messages_for(
            text, style, speed, emotion, global_instruction, audio_tag_style,
            context_prev=context_prev,
        )

        if model_type == "clone":
            if not reference_audio:
                raise ValueError("声音克隆需要提供参考音频")
            voice_data_uri = self._encode_audio(reference_audio)
            completion = await self._create_completion(
                MODEL_IDS["clone"],
                messages,
                {"format": "wav", "voice": voice_data_uri},
            )
            prefix = "tts_clone"
        elif model_type == "design":
            if not voice_description:
                raise ValueError("声音设计需要提供声音描述")
            # design 的 user 必须以「音色」为主维度。
            # 整段「角色/场景」导向若原样塞进去，模型容易当成音色设定去改声线，
            # 与用户写的描述抢戏。这里只保留短节奏/语气，并把导向压成一句「怎么说话」。
            perf = self._build_instruction(style, speed, emotion, None)
            scene_hint = (global_instruction or "").strip().rstrip("。，,.")
            if len(scene_hint) > 80:
                scene_hint = scene_hint[:80].rstrip() + "…"
            voice_line = (voice_description or "").strip()
            parts = [voice_line]
            if scene_hint:
                parts.append(f"说话方式：{scene_hint}")
            if perf:
                parts.append(perf)
            full_description = "。".join(p for p in parts if p)[:500]
            design_messages: list[dict] = [
                {"role": "user", "content": full_description},
            ]
            speak_text = self._build_audio_tag_text(text, audio_tag_style)
            if context_prev:
                design_messages.append({"role": "assistant", "content": context_prev})
                design_messages.append({"role": "user", "content": "继续说，语气和节奏保持连贯"})
            design_messages.append({"role": "assistant", "content": speak_text})
            completion = await self._create_completion(
                MODEL_IDS["design"],
                design_messages,
                {"format": "wav"},
            )
            prefix = "tts_design"
        else:
            voice = voice_id or "mimo_default"
            completion = await self._create_completion(
                MODEL_IDS["builtin"],
                messages,
                {"format": "wav", "voice": voice},
            )
            prefix = "tts_builtin"

        message = completion.choices[0].message
        if not message.audio or not message.audio.data:
            raise RuntimeError("API 未返回音频数据")

        return self._save_audio(message.audio.data, prefix, dest_dir)

    async def synthesize_batch(
        self,
        texts: list[str],
        model_type: str = "builtin",
        **kwargs,
    ) -> list[str]:
        """批量串行合成；任一条失败则抛出，已生成文件路径在异常属性中可取"""
        results: list[str] = []
        dest_dir = kwargs.pop("dest_dir", TEMP_DIR)
        try:
            for i, text in enumerate(texts):
                logger.info("批量合成第 %d/%d 条", i + 1, len(texts))
                path = await self.synthesize(
                    text=text,
                    model_type=model_type,
                    dest_dir=dest_dir,
                    **kwargs,
                )
                results.append(path)
        except Exception as e:
            e.partial_paths = results  # type: ignore[attr-defined]
            raise
        return results
