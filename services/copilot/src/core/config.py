import os
import socket
from dotenv import load_dotenv

# Force load dotenv values
load_dotenv(override=True)

def get_local_ip() -> str:
    """Retrieve the primary local IP address of the laptop dynamically."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class Settings:
    """Core environment configurations for Copilot Service."""
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite://db.sqlite3")

    # CORS
    CORS_ALLOWED_ORIGINS: str = os.getenv("CORS_ALLOWED_ORIGINS", "*")

    # Storage
    DEFAULT_STORAGE_DIR: str = os.getenv("DEFAULT_STORAGE_DIR", "interviews")

    # DeepSeek / OpenAI
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", os.getenv("GROQ_API_KEY", ""))
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # Groq
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_COPILOT_MODEL: str = os.getenv("GROQ_COPILOT_MODEL", "llama-3.1-8b-instant")

    # Optimization Flags
    ENABLE_DETERMINISTIC_ROLE_CLASSIFIER: bool = os.getenv("ENABLE_DETERMINISTIC_ROLE_CLASSIFIER", "true").lower() in ("true", "1", "yes")
    ENABLE_QA_FSM: bool = os.getenv("ENABLE_QA_FSM", "true").lower() in ("true", "1", "yes")
    QA_FSM_SILENCE_THRESHOLD: float = float(os.getenv("QA_FSM_SILENCE_THRESHOLD", "10.0"))
    ENABLE_UNIFIED_QA_WORKER_SHADOW: bool = os.getenv("ENABLE_UNIFIED_QA_WORKER_SHADOW", "true").lower() in ("true", "1", "yes")
    ENABLE_UNIFIED_QA_WORKER: bool = os.getenv("ENABLE_UNIFIED_QA_WORKER", "true").lower() in ("true", "1", "yes")
    ENABLE_LEGACY_FALLBACK: bool = os.getenv("ENABLE_LEGACY_FALLBACK", "true").lower() in ("true", "1", "yes")
    ENABLE_FINAL_EVAL_SHADOW: bool = os.getenv("ENABLE_FINAL_EVAL_SHADOW", "true").lower() in ("true", "1", "yes")
    ENABLE_OPTIMIZED_FINAL_EVAL: bool = os.getenv("ENABLE_OPTIMIZED_FINAL_EVAL", "true").lower() in ("true", "1", "yes")
    ENABLE_LEGACY_FINAL_EVAL_FALLBACK: bool = os.getenv("ENABLE_LEGACY_FINAL_EVAL_FALLBACK", "true").lower() in ("true", "1", "yes")

    @classmethod
    def validate(cls) -> None:
        pass
