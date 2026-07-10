import os
import uuid
from typing import List
from backend.app.core.interfaces.repository import IInterviewRepository
from backend.app.models.interview import InterviewSessionModel
from backend.app.core.config import Settings

class PostgresInterviewRepository(IInterviewRepository):
    """Saves and loads session records from a database using Tortoise ORM."""
    def __init__(self, directory: str = Settings.DEFAULT_STORAGE_DIR):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    async def create_session(
        self, 
        jd: str, 
        resume: str, 
        custom_prompt: str, 
        resume_filename: str = "resume.txt", 
        resume_base64: str = ""
    ) -> str:
        # Generate a unique session ID
        session_id = uuid.uuid4()
        
        # Create session directory for local audio recording
        session_dir = os.path.join(self.directory, str(session_id))
        os.makedirs(session_dir, exist_ok=True)
        
        # Save JD file locally
        with open(os.path.join(session_dir, "jd.txt"), "w", encoding="utf-8") as f:
            f.write(jd)
            
        # Save Resume file locally
        with open(os.path.join(session_dir, "resume.txt"), "w", encoding="utf-8") as f:
            f.write(resume)
            
        # Decode and save the original resume file if provided
        if resume_base64:
            try:
                import base64
                file_bytes = base64.b64decode(resume_base64)
                target_name = "resume.pdf" if resume_filename.lower().endswith(".pdf") else "resume.txt"
                with open(os.path.join(session_dir, target_name), "wb") as f:
                    f.write(file_bytes)
            except Exception as e:
                from loguru import logger
                logger.error(f"Error saving raw resume file: {e}")
            
        # Save session metadata in database
        await InterviewSessionModel.create(
            session_id=session_id,
            jd=jd,
            resume=resume,
            custom_prompt=custom_prompt,
            transcript=[]
        )
        return str(session_id)

    async def save_session(self, session_id: str, data: dict) -> None:
        sid_uuid = uuid.UUID(session_id)
        # Update session record in database
        await InterviewSessionModel.filter(session_id=sid_uuid).update(
            transcript=data.get("transcript", [])
        )

    async def load_session(self, session_id: str) -> dict:
        sid_uuid = uuid.UUID(session_id)
        session = await InterviewSessionModel.get_or_none(session_id=sid_uuid)
        if not session:
            raise FileNotFoundError(f"Interview record not found for session: {session_id}")
        return {
            "session_id": str(session.session_id),
            "timestamp": session.timestamp.isoformat() if session.timestamp else None,
            "jd": session.jd,
            "resume": session.resume,
            "custom_prompt": session.custom_prompt,
            "transcript": session.transcript
        }

    async def list_sessions(self) -> List[str]:
        # Fetch all session_ids from the database
        sessions = await InterviewSessionModel.all().values_list("session_id", flat=True)
        return [str(sid) for sid in sessions]
