"""配置管理"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = BACKEND_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{DATA_DIR / 'database' / 'app.db'}"
# 设置页写入的持久化文件（已 gitignore）；uvicorn -m 启动时也要能读到
ENV_PATH = BACKEND_DIR / ".env"

load_dotenv(ENV_PATH)

AUDIO_DIR = DATA_DIR / "audio"
VOICES_DIR = DATA_DIR / "voices"
TEMP_DIR = AUDIO_DIR / "temp"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VOICES_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "database").mkdir(parents=True, exist_ok=True)

DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"


def _default_tts_provider() -> str:
    """未显式指定时：有 MiMo Key 走完整路径，否则落到免费 Edge TTS。"""
    env = os.environ.get("TTS_PROVIDER", "").strip()
    if env:
        return env
    return "mimo" if os.environ.get("MIMO_API_KEY", "").strip() else "edge-tts"
TTS_TIMEOUT_SECONDS = 120.0
# SDK 内建重试关闭：限流与连接错误都由 TTSService 统一退避，避免双层重试放大请求量
TTS_MAX_RETRIES = 0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# 同时在飞的 TTS 请求数上限：过高会触发服务端限流，过低则长稿合成很慢
TTS_CONCURRENCY = _env_int("TTS_CONCURRENCY", 4)
# 触发限流后的退避重试策略（指数退避 + 抖动）
TTS_RATE_LIMIT_RETRIES = _env_int("TTS_RATE_LIMIT_RETRIES", 4)
TTS_RATE_LIMIT_BASE_DELAY = _env_float("TTS_RATE_LIMIT_BASE_DELAY", 1.0)
TTS_RATE_LIMIT_MAX_DELAY = _env_float("TTS_RATE_LIMIT_MAX_DELAY", 30.0)

# 参考音频限制：MiMo 官方约束是「Base64 编码后的字符串不超过 10MB」。
# base64 的体积约为原始的 4/3，故原始文件上限需相应折算，否则会在服务端被拒。
MAX_REFERENCE_AUDIO_B64_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_AUDIO_BYTES = MAX_REFERENCE_AUDIO_B64_BYTES * 3 // 4
# 响度归一 / 句间停顿（毫秒），被合并与字幕时间轴共同引用
SILENCE_GAP_MS = 600
# 停顿分级：让段落、章节之间有明显的呼吸感，而不是从头到尾一个值。
# 单一停顿是「AI 播客听起来平」的主要来源之一。
SILENCE_GAP_SHORT_MS = _env_int("SILENCE_GAP_SHORT_MS", 350)          # 句与句
SILENCE_GAP_PARAGRAPH_MS = _env_int("SILENCE_GAP_PARAGRAPH_MS", 900)  # 段落之间（空行）
SILENCE_GAP_SECTION_MS = _env_int("SILENCE_GAP_SECTION_MS", 1400)     # 章节之间（【章节】）
# 停顿层级：0=不停顿 1=句间 2=段落 3=章节
GAP_NONE, GAP_SHORT, GAP_PARAGRAPH, GAP_SECTION = 0, 1, 2, 3
GAP_LEVEL_TO_MS = {
    GAP_NONE: 0,
    GAP_SHORT: SILENCE_GAP_SHORT_MS,
    GAP_PARAGRAPH: SILENCE_GAP_PARAGRAPH_MS,
    GAP_SECTION: SILENCE_GAP_SECTION_MS,
}
# 中间产物清理：保留时长与扫描间隔（进程启动时也会先扫一次）
INTERMEDIATE_KEEP_HOURS = _env_int("INTERMEDIATE_KEEP_HOURS", 24)
CLEANUP_INTERVAL_HOURS = _env_float("CLEANUP_INTERVAL_HOURS", 6.0)

DEFAULT_LLM_BASE_URL = "http://localhost:3001/v1"
DEFAULT_LLM_MODEL = "auto"
LLM_TIMEOUT_SECONDS = 90.0
# 播客响度目标（LUFS），接近常见播客平台
PODCAST_LUFS = -16.0


class RuntimeConfig:
    """可热更新的运行时配置。

    凭证字段名仍沿用 MIMO_* / LLM_* 环境变量（历史兼容），
    语义上已是「当前 TTS 供应商 / 当前 LLM 供应商」的通用凭证，
    具体厂商由 tts_provider / llm_provider 决定鉴权方式与默认模型。
    """

    def __init__(self) -> None:
        self._tts_provider = _default_tts_provider()
        self._api_key = os.environ.get("MIMO_API_KEY", "")
        self._base_url = os.environ.get("MIMO_BASE_URL", DEFAULT_BASE_URL)
        self._llm_provider = os.environ.get("LLM_PROVIDER", "freellmapi")
        self._llm_api_key = os.environ.get("LLM_API_KEY", "")
        self._llm_base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        self._llm_model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)

    @property
    def tts_provider(self) -> str:
        return self._tts_provider or _default_tts_provider()

    @property
    def api_key(self) -> str:
        return self._api_key or os.environ.get("MIMO_API_KEY", "")

    @property
    def base_url(self) -> str:
        return self._base_url or os.environ.get("MIMO_BASE_URL", DEFAULT_BASE_URL)

    @property
    def llm_provider(self) -> str:
        return self._llm_provider or os.environ.get("LLM_PROVIDER", "freellmapi")

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
        tts_provider: str | None = None,
        llm_provider: str | None = None,
    ) -> None:
        """热更新内存配置，并写回 backend/.env，避免进程重启后丢失。"""
        applied: dict[str, str] = {}
        if tts_provider:
            self._tts_provider = tts_provider
            os.environ["TTS_PROVIDER"] = tts_provider
            applied["TTS_PROVIDER"] = tts_provider
        if api_key:
            self._api_key = api_key
            os.environ["MIMO_API_KEY"] = api_key
            applied["MIMO_API_KEY"] = api_key
        if base_url:
            self._base_url = base_url
            os.environ["MIMO_BASE_URL"] = base_url
            applied["MIMO_BASE_URL"] = base_url
        if llm_provider:
            self._llm_provider = llm_provider
            os.environ["LLM_PROVIDER"] = llm_provider
            applied["LLM_PROVIDER"] = llm_provider
        if llm_api_key:
            self._llm_api_key = llm_api_key
            os.environ["LLM_API_KEY"] = llm_api_key
            applied["LLM_API_KEY"] = llm_api_key
        if llm_base_url:
            self._llm_base_url = llm_base_url
            os.environ["LLM_BASE_URL"] = llm_base_url
            applied["LLM_BASE_URL"] = llm_base_url
        if llm_model:
            self._llm_model = llm_model
            os.environ["LLM_MODEL"] = llm_model
            applied["LLM_MODEL"] = llm_model
        if applied:
            persist_env(applied)


def persist_env(updates: dict[str, str]) -> None:
    """合并写入 backend/.env（保留注释与未涉及的键）。"""
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass


runtime_config = RuntimeConfig()
MIMO_API_KEY = runtime_config.api_key
MIMO_BASE_URL = runtime_config.base_url
