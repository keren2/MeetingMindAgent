from functools import lru_cache
from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
VECTOR_DIR = DATA_DIR / "vectorstore"
DB_PATH = DATA_DIR / "meetingmind.db"


def _load_root_env() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache
def settings() -> dict:
    _load_root_env()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "app_name": "MeetingMind Agent Pro",
        "secret_key": os.getenv("SECRET_KEY", "meetingmind-dev-secret-change-me"),
        "llm_api_key": os.getenv("LLM_API_KEY", ""),
        "llm_model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        "embedding_api_key": os.getenv("EMBEDDING_API_KEY", os.getenv("LLM_API_KEY", "")),
        "embedding_model": os.getenv("EMBEDDING_MODEL", ""),
        "embedding_base_url": os.getenv("EMBEDDING_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.deepseek.com")),
        "embedding_dimensions": int(os.getenv("EMBEDDING_DIMENSIONS", "384")),
        "vector_store": os.getenv("VECTOR_STORE", "sqlite").lower(),
        "default_daily_limit": int(os.getenv("DEFAULT_DAILY_LIMIT", "3")),
        "allow_demo_fallback": os.getenv("ALLOW_DEMO_FALLBACK", "true").lower() == "true",
    }
