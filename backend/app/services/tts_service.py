"""MiMo TTS 服务封装 — 内置音色 / 声音设计 / 声音克隆"""
import os
import base64
import uuid
import logging
from pathlib import Path

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from app.config import (
    AUDIO_DIR,
    TEMP_DIR,
    DEFAULT_BASE_URL,
    TTS_TIMEOUT_SECONDS,
    TTS_MAX_RETRIES,
    MAX_REFERENCE_AUDIO_BYTES,
    runtime_config,
)

logger = logging.getLogger(__name__)

MODEL_IDS = {
    "builtin": "mimo-v2.5-tts",
    "design": "mimo-v2.5-tts-voicedesign",
    "clone": "mimo-v2.5-tts-voiceclone",
}

STYLE_PRESETS = {
    "温柔": "用温柔舒缓的语气说话，语速偏慢，声音轻柔，像在哄孩子入睡",
    "兴奋": "用兴奋激动的语气说话，语速偏快，声音高亢，充满活力和热情",
    "悲伤": "用悲伤低沉的语气说话，语速缓慢，声音沙哑低沉，带着哽咽感",
    "愤怒": "用愤怒的语气说话，语速较快，声音尖锐有力，带着明显的怒气",
    "严肃": "用严肃正式的语气说话，语速适中，声音沉稳有力，带有权威感",
    "幽默": "用幽默诙谐的语气说话，语速轻快，声音带着笑意，轻松愉快",
    "紧张": "用紧张焦虑的语气说话，语速急促，声音颤抖，带着紧迫感",
    "平静": "用平静从容的语气说话，语速平稳，声音柔和，不带明显情绪波动",
    "叙述": "用讲故事的口吻叙述，语速适中，声音富有感染力，带有画面感",
    "新闻播报": "用新闻播报的专业语气，语速均匀，吐字清晰，语气客观正式",
    "撒娇": "用撒娇的语气说话，声音软糯，尾音拖长带着依赖感",
    "磁性低沉": "用低沉磁性的嗓音说话，声音浑厚有共鸣，像深夜电台主播",
    "活泼可爱": "用活泼可爱的语气说话，语速轻快，声音清脆明亮",
    "苍老": "用苍老沙哑的声音说话，语速缓慢，带着岁月沧桑感",
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


class AsyncMimoOpenAI(AsyncOpenAI):
    @property
    def auth_headers(self) -> dict[str, str]:
        return {"api-key": self.api_key}


class TTSService:
    def __init__(self):
        self.api_key = runtime_config.api_key
        self.base_url = runtime_config.base_url or DEFAULT_BASE_URL
        if not self.api_key:
            raise ValueError("需要配置 MIMO_API_KEY")
        self.client = AsyncMimoOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
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
        data = path.read_bytes()
        if len(data) > MAX_REFERENCE_AUDIO_BYTES:
            raise ValueError("音频文件不能超过 10MB")
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:{mime_map[suffix]};base64,{b64}"

    def _build_instruction(
        self,
        style: str | None = None,
        speed: str | None = None,
        emotion: str | None = None,
        global_instruction: str | None = None,
    ) -> str:
        parts = []
        if global_instruction:
            parts.append(global_instruction)
        if style and style in STYLE_PRESETS:
            parts.append(STYLE_PRESETS[style])
        elif style:
            parts.append(style)
        if speed and speed in SPEED_PRESETS:
            parts.append(SPEED_PRESETS[speed])
        elif speed:
            parts.append(f"语速{speed}")
        if emotion:
            parts.append(f"情绪：{emotion}")
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
    ) -> list[dict]:
        instruction = self._build_instruction(style, speed, emotion, global_instruction)
        speak_text = self._build_audio_tag_text(text, audio_tag_style)
        messages = []
        if instruction:
            messages.append({"role": "user", "content": instruction})
        messages.append({"role": "assistant", "content": speak_text})
        return messages

    async def _create_completion(self, model: str, messages: list[dict], audio_params: dict):
        try:
            return await self.client.chat.completions.create(
                model=model,
                messages=messages,
                audio=audio_params,
            )
        except APITimeoutError as e:
            raise RuntimeError(f"TTS 请求超时（{TTS_TIMEOUT_SECONDS}s）") from e
        except RateLimitError as e:
            raise RuntimeError("TTS API 触发限流，请稍后重试") from e
        except APIError as e:
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
    ) -> str:
        """合成语音，返回落盘路径"""
        messages = self._messages_for(
            text, style, speed, emotion, global_instruction, audio_tag_style
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
            extra = self._build_instruction(style, speed, emotion, None)
            full_description = voice_description
            if extra:
                full_description = f"{voice_description}。{extra}"
            # design 的描述放在 user，text 仍走 assistant
            design_messages = [
                {"role": "user", "content": full_description},
                {"role": "assistant", "content": self._build_audio_tag_text(text, audio_tag_style)},
            ]
            if global_instruction:
                design_messages.insert(
                    0, {"role": "user", "content": global_instruction}
                )
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
