import os
import uuid
from typing import List, Dict, Any, Optional
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

        update_fields = {}
        if "transcript" in data:
            update_fields["transcript"] = data["transcript"]
        if "final_report" in data:
            update_fields["final_report"] = data["final_report"]
        elif "intelligence" in data and "assistance" in data and data.get("is_finalized"):
            update_fields["final_report"] = {
                "transcript": data.get("transcript", []),
                "intelligence": data.get("intelligence", {}),
                "assistance": data.get("assistance", {})
            }

        if update_fields:
            await CopilotSessionModel.filter(session_id=sid_uuid).update(**update_fields)

    async def load_session(self, session_id: str) -> dict:
        try:
            sid_uuid = uuid.UUID(session_id)
        except ValueError:
            raise FileNotFoundError(f"Invalid session id format: '{session_id}'")
        session = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not session:
            raise FileNotFoundError(f"Copilot record not found for session: {session_id}")
        is_service_off = os.path.exists(os.path.join("interviews", str(sid_uuid), "service_off.flag")) or os.path.exists(os.path.join(self.directory, str(sid_uuid), "service_off.flag"))
        return {
            "session_id": str(session.session_id),
            "timestamp": session.timestamp.isoformat() if session.timestamp else None,
            "jd": session.jd,
            "resume": session.resume,
            "custom_prompt": session.custom_prompt,
            "transcript": session.transcript,
            "final_report": session.final_report,
            "service_off": is_service_off
        }

    async def list_sessions(self, limit: Optional[int] = None) -> List[dict]:
        # Fetch all session details ordered by timestamp
        query = CopilotSessionModel.all().order_by("-timestamp")
        if limit is not None and limit > 0:
            query = query.limit(limit)
        sessions = await query
        return [
            {
                "session_id": str(s.session_id),
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "jd": s.jd,
                "resume": s.resume,
                "custom_prompt": s.custom_prompt,
                "transcript": s.transcript,
                "final_report": s.final_report
            }
            for s in sessions
        ]
