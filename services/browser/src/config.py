import os
from dotenv import load_dotenv

# Load .env file with override=False so runtime environment variables passed by caller take precedence
load_dotenv(override=False)


class BrowserConfig:
    """Core environment configurations for the Browser Service & Teams Bot."""

    # WebSocket endpoints
    BACKEND_WS_BASE: str = os.getenv("COPILOT_WS_BASE", os.getenv("BACKEND_WS_BASE", "ws://127.0.0.1:8000"))
    LOCAL_AUDIO_WS_BASE: str = os.getenv("LOCAL_AUDIO_WS_BASE", "ws://127.0.0.1:8000")

    # Shared namespace: when True, skip AudioProxy and connect directly to FastAPI via localhost:8000
    USE_SHARED_NAMESPACE: bool = os.getenv("USE_SHARED_NAMESPACE", "true").lower() == "true"

    # Bot Identity & Role
    BOT_DISPLAY_NAME: str = os.getenv("BOT_DISPLAY_NAME", "Mia - AI Interviewer")
    BOT_ROLE: str = os.getenv("BOT_ROLE", "interviewer")

    # Browser Execution Settings
    BOT_HEADLESS: bool = os.getenv("BOT_HEADLESS", "true").lower() == "true"
    BOT_PREJOIN_TIMEOUT_MS: int = int(os.getenv("BOT_PREJOIN_TIMEOUT_MS", "75000"))

    # Audio & Join Timing / Buffering
    MIA_JOIN_ONLY: bool = os.getenv("MIA_JOIN_ONLY", "false").lower() == "true"
    MIA_INITIAL_BUFFER_MS: int = int(os.getenv("MIA_INITIAL_BUFFER_MS", "350"))
    MIA_RECOVERY_BUFFER_MS: int = int(os.getenv("MIA_RECOVERY_BUFFER_MS", "250"))

    # Proxy Host / Port (for legacy non-shared namespace relay)
    BACKEND_HOST: str = os.getenv("BACKEND_HOST", "backend-services")
    COPILOT_PORT: int = int(os.getenv("COPILOT_PORT", "8000"))

    @classmethod
    def get_ws_url(cls, session_id: str, bot_role: str | None = None) -> str:
        """Construct the target WebSocket URL based on bot_role ('interviewer' vs 'observer')."""
        role = (bot_role or cls.BOT_ROLE).lower()
        if role == "interviewer":
            return f"{cls.LOCAL_AUDIO_WS_BASE}/api/ws/interview/{session_id}"
        return f"{cls.LOCAL_AUDIO_WS_BASE}/api/ws/copilot/{session_id}?mode=audio_stream"


# Singleton instance
config = BrowserConfig()

# Expose module-level constants
BACKEND_WS_BASE = config.BACKEND_WS_BASE
LOCAL_AUDIO_WS_BASE = config.LOCAL_AUDIO_WS_BASE
USE_SHARED_NAMESPACE = config.USE_SHARED_NAMESPACE
BOT_DISPLAY_NAME = config.BOT_DISPLAY_NAME
BOT_ROLE = config.BOT_ROLE
BOT_HEADLESS = config.BOT_HEADLESS
BOT_PREJOIN_TIMEOUT_MS = config.BOT_PREJOIN_TIMEOUT_MS
MIA_JOIN_ONLY = config.MIA_JOIN_ONLY
MIA_INITIAL_BUFFER_MS = config.MIA_INITIAL_BUFFER_MS
MIA_RECOVERY_BUFFER_MS = config.MIA_RECOVERY_BUFFER_MS
BACKEND_HOST = config.BACKEND_HOST
COPILOT_PORT = config.COPILOT_PORT
