"""按供应商创建 OpenAI 兼容客户端。

业务代码不要直接 new AsyncOpenAI/OpenAI，统一走这里，
以便集中处理鉴权头差异（MiMo 用 api-key，其余 Bearer）。
"""
from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from app.providers.catalog import AuthStyle, ProviderSpec, get_provider


class _AuthMixin:
    _auth_style: AuthStyle = "bearer"

    @property
    def auth_headers(self) -> dict[str, str]:
        if self._auth_style == "api-key":
            return {"api-key": self.api_key}
        # OpenAI SDK 默认：Authorization: Bearer <key>
        return {"Authorization": f"Bearer {self.api_key}"}


class ProviderAsyncOpenAI(_AuthMixin, AsyncOpenAI):
    def __init__(self, *args, auth_style: AuthStyle = "bearer", **kwargs):
        self._auth_style = auth_style
        super().__init__(*args, **kwargs)


class ProviderOpenAI(_AuthMixin, OpenAI):
    def __init__(self, *args, auth_style: AuthStyle = "bearer", **kwargs):
        self._auth_style = auth_style
        super().__init__(*args, **kwargs)


def create_async_client(
    *,
    api_key: str,
    base_url: str,
    provider_id: str | None = None,
    kind: str = "tts",
    timeout: float | None = None,
    max_retries: int = 0,
) -> ProviderAsyncOpenAI:
    spec: ProviderSpec | None = get_provider(kind, provider_id)  # type: ignore[arg-type]
    auth: AuthStyle = spec.auth if spec else "bearer"
    return ProviderAsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        auth_style=auth,
        timeout=timeout,
        max_retries=max_retries,
    )


def create_sync_client(
    *,
    api_key: str,
    base_url: str,
    provider_id: str | None = None,
    kind: str = "llm",
    timeout: float | None = None,
    max_retries: int = 1,
) -> ProviderOpenAI:
    spec: ProviderSpec | None = get_provider(kind, provider_id)  # type: ignore[arg-type]
    auth: AuthStyle = spec.auth if spec else "bearer"
    return ProviderOpenAI(
        api_key=api_key,
        base_url=base_url,
        auth_style=auth,
        timeout=timeout,
        max_retries=max_retries,
    )
