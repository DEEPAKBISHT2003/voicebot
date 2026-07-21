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
    """Core environment configurations."""
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    
    # DeepSeek / OpenAI API Configurations
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", os.getenv("GROQ_API_KEY", ""))
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    
    # Legacy Groq fallback settings
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_COPILOT_MODEL: str = os.getenv("GROQ_COPILOT_MODEL", "llama-3.1-8b-instant")
    DEFAULT_STORAGE_DIR: str = "interviews"
    
    # Database connection URL
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite://db.sqlite3")
    
    # Dynamic URLs based on local IP configuration
    CORS_ALLOWED_ORIGINS: str = os.getenv("CORS_ALLOWED_ORIGINS", os.getenv("FRONTEND_URL", "*"))

    @classmethod
    def validate(cls) -> None:
        """Helper to check if all keys are loaded."""
        if not cls.DEEPGRAM_API_KEY:
            raise ValueError("DEEPGRAM_API_KEY is not defined in environment.")
        if not cls.DEEPSEEK_API_KEY and not cls.GROQ_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is not defined in environment.")

