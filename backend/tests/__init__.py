"""共享测试路径与环境"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("MIMO_API_KEY", "test-key-for-unit-tests")
os.environ.setdefault("MIMO_BASE_URL", "https://example.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9/v1")
os.environ.setdefault("LLM_MODEL", "auto")
os.environ.setdefault("APP_TOKEN", "")

CONTRACT_PATH = REPO_ROOT / "contracts" / "api-contract.json"
