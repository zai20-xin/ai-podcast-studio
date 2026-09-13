"""LLM 写稿服务（OpenAI 兼容，供应商见 app.providers）"""
import logging

from openai import APIError, APITimeoutError

from app.config import runtime_config, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, LLM_TIMEOUT_SECONDS
from app.providers import create_sync_client, get_provider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是专业播客编剧。根据用户提供的大纲或文章，写出可直接用于 TTS 合成的口播稿。

规则：
1. 口语化，短句，避免书面腔和长难句。
2. 可在句中插入语气标签：(轻笑)(停顿)(叹气)(深呼吸)。
3. 不要输出解释、标题以外的元信息。
4. 双人模式严格使用「角色名：台词」格式，每行一句，角色名固定用 A、B。
5. 单人模式直接输出台词段落，空行分段。
6. 长度贴近用户要求；未指定时控制在 2–4 分钟口播量。"""


class LLMService:
    def __init__(self) -> None:
        provider_id = runtime_config.llm_provider or "freellmapi"
        spec = get_provider("llm", provider_id)
        key = runtime_config.llm_api_key
        if not key:
            label = spec.name if spec else "LLM"
            raise ValueError(f"未配置 {label} 的 API Key，请到「设置」中填写")
        if spec and spec.status != "supported":
            logger.warning(
                "LLM 供应商 %s 状态为 %s：OpenAI 兼容适配，未逐一实测",
                provider_id,
                spec.status,
            )
        self.provider_id = provider_id
        self.client = create_sync_client(
            api_key=key,
            base_url=runtime_config.llm_base_url or (spec.default_base_url if spec else DEFAULT_LLM_BASE_URL),
            provider_id=provider_id,
            kind="llm",
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=1,
        )
        self.model = runtime_config.llm_model or DEFAULT_LLM_MODEL

    def generate_script(
        self,
        source: str,
        mode: str = "dialogue",
        topic: str | None = None,
        style_hint: str | None = None,
        target_minutes: float | None = None,
    ) -> str:
        mode_label = "双人对谈（A/B）" if mode == "dialogue" else "单人朗读"
        minutes = target_minutes or 3
        user_parts = [
            f"播客模式：{mode_label}",
            f"目标时长：约 {minutes} 分钟",
        ]
        if topic:
            user_parts.append(f"主题：{topic}")
        if style_hint:
            user_parts.append(f"风格要求：{style_hint}")
        user_parts.append("原始材料（大纲/文章/要点）：")
        user_parts.append(source.strip()[:12000])

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(user_parts)},
                ],
                temperature=0.7,
            )
        except APITimeoutError as e:
            raise RuntimeError("写稿服务超时，请稍后重试") from e
        except APIError as e:
            raise RuntimeError(f"写稿服务错误：{e}") from e

        text = (completion.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("写稿服务返回空内容")
        return text

    def rewrite_script(
        self,
        script: str,
        mode: str = "dialogue",
        action: str = "colloquial",
        ratio: float | None = None,
    ) -> str:
        """action: colloquial | shorten | polish | perform"""
        mode_label = "双人对谈（A/B）" if mode == "dialogue" else "单人朗读"
        if action == "shorten":
            pct = int(round((ratio or 0.6) * 100))
            instruction = (
                f"把脚本压缩到约原长的 {pct}%。保留核心观点与转折，删掉重复和水词，"
                "语气标签可保留或删减。不要增加新信息。"
            )
        elif action == "polish":
            instruction = (
                "润色脚本：理顺逻辑与口语节奏，修正别扭表达，可微调语气标签，"
                "不改变事实与整体结构。"
            )
        elif action == "perform":
            # 「演绎化」：面向不懂音频参数的普通用户，把干稿变成可直接合成的演播稿。
            # 断句与情绪提示交给脚本层面解决，用户就不必自己去调参数。
            instruction = (
                "把脚本改写成「可直接合成的演播稿」，目标是让配音念出来自然、有起伏、有呼吸感。"
                "要求：\n"
                "1) 长句按语义拆成适合口播的短句，一句话只承载一个意思；\n"
                "2) 在情绪转折、需要停顿或需要强调的落点插入标签，"
                "可用 (停顿)(轻笑)(叹气)(深呼吸)(小声)(提高音量) 等；"
                "标签要克制，平均每两三句最多一处，不要每句都加，否则听起来会做作；\n"
                "3) 用空行区分段落，同一段落内句子保持连贯；\n"
                "4) 严格保留原意与全部信息，不新增事实、不改变立场、不删减要点；\n"
                "5) 不要输出任何解释、标题或说明文字。"
            )
        else:
            instruction = (
                "把书面腔改成自然口语：短句、可停顿、像真人聊天；"
                "可适当加入(停顿)(轻笑)等标签。不要改成另一个话题。"
            )

        user = (
            f"播客模式：{mode_label}\n"
            f"任务：{instruction}\n"
            f"输出要求：只输出改写后的完整脚本，不要解释。\n"
            f"双人模式必须保持「A：」「B：」每行一句。\n\n"
            f"原脚本：\n{script.strip()[:12000]}"
        )
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=0.55,
            )
        except APITimeoutError as e:
            raise RuntimeError("改写服务超时，请稍后重试") from e
        except APIError as e:
            raise RuntimeError(f"改写服务错误：{e}") from e

        text = (completion.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("改写服务返回空内容")
        return text


def friendly_error(exc: Exception | str) -> str:
    """把工程错误翻译成用户能懂的话"""
    msg = str(exc)
    low = msg.lower()
    # Edge 引擎已写好可操作的中文说明，不要被下面的通用分支盖掉
    if msg.startswith("Edge TTS"):
        return msg
    if "api key" in low or "unauthorized" in low or "401" in low:
        return "API Key 无效或未配置，请到「设置」检查"
    if "timeout" in low or "timed out" in low:
        return "请求超时，网络或服务较慢，请重试"
    if "rate" in low or "429" in low:
        return "请求过于频繁或额度受限，请稍后再试"
    if (
        "音频文件不存在" in msg
        or "参考音频路径无效" in msg
        or "参考音频必须位于" in msg
    ):
        return "参考音频文件已不存在，请重新选择后再试"
    if "Base64 编码后" in msg or "超过上限" in msg:
        return "参考音频过大：MiMo 限制 Base64 编码后不超过 10MB（约合原始文件 7.5MB）"
    if "超过单集上限" in msg:
        return msg  # 文案里已含具体句数与上限，直接透出
    if "reference" in low or "参考音频" in msg:
        return "参考音频有问题：请换 10–15 秒干净人声（wav/mp3，≤7.5MB）"
    if "脚本为空" in msg or "无法解析" in msg:
        return "脚本无法解析出有效台词，请检查「角色：台词」格式"
    if "未返回音频" in msg or "No audio was received" in msg:
        return (
            "语音服务没有返回音频（多为免费 Edge 通道限流）。"
            "请稍后重试；若频繁出现，请在演播设定切换到 MiMo TTS"
        )
    if "connection" in low or "connect" in low:
        return "无法连接服务，请确认本地 API 或网络是否可用"
    return msg[:200]
