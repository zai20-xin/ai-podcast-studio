"""供应商兼容层：目录 + 客户端工厂。

新厂商接入步骤见 catalog.py 模块注释与 README。
"""
from app.providers.catalog import (
    LLM_BY_ID,
    LLM_PROVIDERS,
    TTS_BY_ID,
    TTS_PROVIDERS,
    ProviderSpec,
    catalog_payload,
    get_provider,
)
from app.providers.clients import (
    ProviderAsyncOpenAI,
    ProviderOpenAI,
    create_async_client,
    create_sync_client,
)

__all__ = [
    "LLM_BY_ID",
    "LLM_PROVIDERS",
    "TTS_BY_ID",
    "TTS_PROVIDERS",
    "ProviderSpec",
    "catalog_payload",
    "get_provider",
    "ProviderAsyncOpenAI",
    "ProviderOpenAI",
    "create_async_client",
    "create_sync_client",
]
