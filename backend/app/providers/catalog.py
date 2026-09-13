"""供应商目录：TTS / LLM 的可选项与能力标注。

设计原则
--------
- 业务层只依赖「OpenAI 兼容 chat.completions」这一种调用形态，
  具体厂商差异收敛在 auth header 与默认 Base URL。
- catalog 里 status 必须诚实：
  * supported      —— 本仓库已实测/主路径
  * experimental   —— 仅按 OpenAI 兼容协议适配，未完整验证
- 扩展新厂商：在 TTS_PROVIDERS / LLM_PROVIDERS 加一条即可；
  若鉴权不是 Bearer / api-key，在 clients.py 增加 auth 样式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AuthStyle = Literal["bearer", "api-key", "none"]
ProviderKind = Literal["tts", "llm"]
ProviderStatus = Literal["supported", "experimental"]
# MiMo 专有能力：design=文字描述音色，clone=参考音频克隆
TTSCapability = Literal["builtin", "design", "clone"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    kind: ProviderKind
    auth: AuthStyle
    default_base_url: str
    status: ProviderStatus
    description: str
    model_hints: tuple[str, ...] = field(default_factory=tuple)
    # 给开发者/用户看的接入说明（设置页与 README 共用）
    note: str = ""
    # 是否必须配置 API Key（edge-tts 等免费通道为 False）
    requires_key: bool = True
    # TTS 支持的模型类型；LLM 忽略
    supported_model_types: tuple[TTSCapability, ...] = (
        "builtin",
        "design",
        "clone",
    )


TTS_PROVIDERS: list[ProviderSpec] = [
    ProviderSpec(
        id="edge-tts",
        name="Edge TTS（免费）",
        kind="tts",
        auth="none",
        default_base_url="",
        status="supported",
        description="微软 Edge 神经语音，无需 API Key，开箱即用",
        note=(
            "免费、无需密钥；仅支持内置音色。"
            "声音设计 / 克隆请切换到 MiMo TTS。"
            "依赖微软在线服务（非官方封装），请遵守当地服务条款。"
        ),
        requires_key=False,
        supported_model_types=("builtin",),
    ),
    ProviderSpec(
        id="mimo",
        name="Xiaomi MiMo TTS",
        kind="tts",
        auth="api-key",
        default_base_url="https://token-plan-cn.xiaomimimo.com/v1",
        status="supported",
        description="完整支持内置音色 / 声音设计 / 声音克隆的 TTS",
        model_hints=(
            "mimo-v2.5-tts",
            "mimo-v2.5-tts-voicedesign",
            "mimo-v2.5-tts-voiceclone",
        ),
        note=(
            "鉴权使用 api-key 请求头（非 Bearer）。"
            "内置音色 ID、design/clone 模型名均为 MiMo 专有，其他厂商未必兼容。"
        ),
        requires_key=True,
        supported_model_types=("builtin", "design", "clone"),
    ),
    ProviderSpec(
        id="openai_compatible",
        name="其他 OpenAI 兼容 TTS",
        kind="tts",
        auth="bearer",
        default_base_url="",
        status="experimental",
        description="自备兼容 /v1/chat/completions 且返回 audio 的服务",
        note=(
            "仅按 OpenAI 兼容形态接了通用客户端，未验证音色/克隆/设计语义。"
            "内置音色列表、模型 ID 仍按 MiMo 硬编码，换厂商后合成大概率失败。"
            "扩展请见 app/providers/ 与 README「供应商兼容层」。"
        ),
        requires_key=True,
        supported_model_types=("builtin",),
    ),
]

LLM_PROVIDERS: list[ProviderSpec] = [
    ProviderSpec(
        id="freellmapi",
        name="freellmapi",
        kind="llm",
        auth="bearer",
        default_base_url="http://localhost:3001/v1",
        status="supported",
        description="本地/自建 OpenAI 兼容网关，写稿主路径",
        model_hints=("auto", "fusion"),
        note="模型可填 auto / fusion，或网关暴露的具体 model id。",
    ),
    ProviderSpec(
        id="openai",
        name="OpenAI",
        kind="llm",
        auth="bearer",
        default_base_url="https://api.openai.com/v1",
        status="supported",
        description="官方 Chat Completions",
        model_hints=("gpt-4o-mini", "gpt-4o"),
        note="填写官方 API Key 与具体 model id。",
    ),
    ProviderSpec(
        id="ollama",
        name="Ollama",
        kind="llm",
        auth="bearer",
        default_base_url="http://127.0.0.1:11434/v1",
        status="experimental",
        description="本机 Ollama OpenAI 兼容端口",
        model_hints=("qwen2.5", "llama3.1"),
        note="Key 可任意非空；模型名须与 ollama list 一致。",
    ),
    ProviderSpec(
        id="custom",
        name="自定义 OpenAI 兼容",
        kind="llm",
        auth="bearer",
        default_base_url="",
        status="experimental",
        description="任意兼容 /v1/chat/completions 的服务（vLLM、OneAPI 等）",
        note="Base URL 须指向 …/v1；鉴权为 Bearer。本仓库未逐一实测。",
    ),
]


def _index(specs: list[ProviderSpec]) -> dict[str, ProviderSpec]:
    return {p.id: p for p in specs}


TTS_BY_ID = _index(TTS_PROVIDERS)
LLM_BY_ID = _index(LLM_PROVIDERS)


def get_provider(kind: ProviderKind, provider_id: str | None) -> ProviderSpec | None:
    if not provider_id:
        return None
    table = TTS_BY_ID if kind == "tts" else LLM_BY_ID
    return table.get(provider_id)


def catalog_payload() -> dict:
    def pack(specs: list[ProviderSpec]) -> list[dict]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "auth": p.auth,
                "status": p.status,
                "description": p.description,
                "default_base_url": p.default_base_url,
                "model_hints": list(p.model_hints),
                "note": p.note,
            }
            for p in specs
        ]

    def pack_tts(specs: list[ProviderSpec]) -> list[dict]:
        rows = pack(specs)
        for row, spec in zip(rows, specs):
            row["requires_key"] = spec.requires_key
            row["supported_model_types"] = list(spec.supported_model_types)
        return rows

    return {"tts": pack_tts(TTS_PROVIDERS), "llm": pack(LLM_PROVIDERS)}
