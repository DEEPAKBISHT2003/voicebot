from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

router = APIRouter(prefix="/api/copilot")

class StartCopilotRequest(BaseModel):
    jd: str
    resume: str
    custom_prompt: str = ""

@router.post("/start")
async def start_copilot(req: StartCopilotRequest):
    logger.info("Starting AI Copilot Session...")
    return {"session_id": "copilot-stub-session-id", "status": "Copilot session initialized"}

@router.post("/{session_id}/stop")
async def stop_copilot(session_id: str):
    logger.info(f"Stopping AI Copilot Session: {session_id}")
    return {"status": "stopped"}

@router.get("/{session_id}/status")
async def get_copilot_status(session_id: str):
    return {
        "status": "ready",
        "transcript": [
            {"role": "assistant", "text": "Hello, I am your Copilot. How can I help you today?"}
        ]
    }
