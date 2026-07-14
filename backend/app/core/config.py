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
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
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
        if not cls.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not defined in environment.")

