"""配置管理"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{DATA_DIR / 'database' / 'app.db'}"

AUDIO_DIR = DATA_DIR / "audio"
VOICES_DIR = DATA_DIR / "voices"
TEMP_DIR = AUDIO_DIR / "temp"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VOICES_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "database").mkdir(parents=True, exist_ok=True)

DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
TTS_TIMEOUT_SECONDS = 120.0
TTS_MAX_RETRIES = 2
MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024

DEFAULT_LLM_BASE_URL = "http://localhost:3001/v1"
DEFAULT_LLM_MODEL = "auto"
LLM_TIMEOUT_SECONDS = 90.0
# 播客响度目标（LUFS），接近常见播客平台
PODCAST_LUFS = -16.0


class RuntimeConfig:
    """可热更新的运行时配置"""

    def __init__(self) -> None:
        self._api_key = os.environ.get("MIMO_API_KEY", "")
        self._base_url = os.environ.get("MIMO_BASE_URL", DEFAULT_BASE_URL)
        self._llm_api_key = os.environ.get("LLM_API_KEY", "")
        self._llm_base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        self._llm_model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)

    @property
    def api_key(self) -> str:
        return self._api_key or os.environ.get("MIMO_API_KEY", "")

    @property
    def base_url(self) -> str:
        return self._base_url or os.environ.get("MIMO_BASE_URL", DEFAULT_BASE_URL)

    @property
    def llm_api_key(self) -> str:
        return self._llm_api_key or os.environ.get("LLM_API_KEY", "")

    @property
    def llm_base_url(self) -> str:
        return self._llm_base_url or os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)

    @property
    def llm_model(self) -> str:
        return self._llm_model or os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)

    def update(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        if api_key:
            self._api_key = api_key
            os.environ["MIMO_API_KEY"] = api_key
        if base_url:
            self._base_url = base_url
            os.environ["MIMO_BASE_URL"] = base_url
        if llm_api_key:
            self._llm_api_key = llm_api_key
            os.environ["LLM_API_KEY"] = llm_api_key
        if llm_base_url:
            self._llm_base_url = llm_base_url
            os.environ["LLM_BASE_URL"] = llm_base_url
        if llm_model:
            self._llm_model = llm_model
            os.environ["LLM_MODEL"] = llm_model


runtime_config = RuntimeConfig()
MIMO_API_KEY = runtime_config.api_key
MIMO_BASE_URL = runtime_config.base_url
