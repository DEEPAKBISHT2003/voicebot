import os
import uuid
from typing import List, Dict, Any
from services.copilot.src.models.copilot import CopilotSessionModel
from services.copilot.src.core.config import Settings

class CopilotRepository:
    """Manages Copilot session metadata and filesystem resources."""
    def __init__(self, directory: str = Settings.DEFAULT_STORAGE_DIR):
        self.directory = os.path.join(directory, "copilots")
        os.makedirs(self.directory, exist_ok=True)

    async def create_session(
        self, 
        jd: str, 
        resume: str, 
        custom_prompt: str,
        session_id: str = None
    ) -> str:
        # Use provided session_id or generate a new one
        if session_id:
            sid_uuid = uuid.UUID(session_id)
        else:
            sid_uuid = uuid.uuid4()
        
        # Create session directory for local files
        session_dir = os.path.join(self.directory, str(sid_uuid))
        os.makedirs(session_dir, exist_ok=True)
        
        # Save JD file locally
        with open(os.path.join(session_dir, "jd.txt"), "w", encoding="utf-8") as f:
            f.write(jd)
            
        # Save Resume file locally
        with open(os.path.join(session_dir, "resume.txt"), "w", encoding="utf-8") as f:
            f.write(resume)
            
        # Save session metadata in database (skip if already exists)
        existing = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not existing:
            await CopilotSessionModel.create(
                session_id=sid_uuid,
                jd=jd,
                resume=resume,
                custom_prompt=custom_prompt,
                transcript=[]
            )
        return str(sid_uuid)

    async def save_session(self, session_id: str, data: dict) -> None:
        try:
            sid_uuid = uuid.UUID(session_id)
        except ValueError:
            from loguru import logger
            logger.warning(f"save_session: invalid UUID '{session_id}', skipping.")
            return
        await CopilotSessionModel.filter(session_id=sid_uuid).update(
            transcript=data.get("transcript", [])
        )

    async def load_session(self, session_id: str) -> dict:
        try:
            sid_uuid = uuid.UUID(session_id)
        except ValueError:
            raise FileNotFoundError(f"Invalid session id format: '{session_id}'")
        session = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not session:
            raise FileNotFoundError(f"Copilot record not found for session: {session_id}")
        return {
            "session_id": str(session.session_id),
            "timestamp": session.timestamp.isoformat() if session.timestamp else None,
            "jd": session.jd,
            "resume": session.resume,
            "custom_prompt": session.custom_prompt,
            "transcript": session.transcript
        }

    async def list_sessions(self) -> List[dict]:
        # Fetch all session details ordered by timestamp
        sessions = await CopilotSessionModel.all().order_by("-timestamp")
        return [
            {
                "session_id": str(s.session_id),
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "jd": s.jd,
                "resume": s.resume,
                "custom_prompt": s.custom_prompt,
                "transcript": s.transcript
            }
            for s in sessions
        ]
