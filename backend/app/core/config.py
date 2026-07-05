import os
from dotenv import load_dotenv

# Force load dotenv values
load_dotenv(override=True)

class Settings:
    """Core environment configurations."""
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    DEFAULT_STORAGE_DIR: str = "interviews"

    @classmethod
    def validate(cls) -> None:
        """Helper to check if all keys are loaded."""
        if not cls.DEEPGRAM_API_KEY:
            raise ValueError("DEEPGRAM_API_KEY is not defined in environment.")
        if not cls.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not defined in environment.")
