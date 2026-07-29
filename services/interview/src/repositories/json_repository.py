import os
import json
import uuid
import datetime
from backend.app.core.interfaces.repository import IInterviewRepository
from backend.app.core.config import Settings

class JSONFileInterviewRepository(IInterviewRepository):
    """Saves and loads session records from local files on disk."""
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
        session_id = str(uuid.uuid4())
        session_dir = os.path.join(self.directory, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        # Save JD
        with open(os.path.join(session_dir, "jd.txt"), "w", encoding="utf-8") as f:
            f.write(jd)
            
        # Save Resume
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
                logger.error(f"Error saving raw resume file in JSON repo: {e}")
            
        # Create initial metadata
        initial_data = {
            "session_id": session_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "jd": jd,
            "resume": resume,
            "custom_prompt": custom_prompt,
            "transcript": []
        }
        await self.save_session(session_id, initial_data)
        return session_id
        
    async def save_session(self, session_id: str, data: dict) -> None:
        session_dir = os.path.join(self.directory, session_id)
        os.makedirs(session_dir, exist_ok=True)
        file_path = os.path.join(session_dir, "session.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, default=str)
            
    async def load_session(self, session_id: str) -> dict:
        session_dir = os.path.join(self.directory, session_id)
        file_path = os.path.join(session_dir, "session.json")
        
        # Fallback to legacy path if folder/file doesn't exist
        if not os.path.exists(file_path):
            legacy_file_path = os.path.join(self.directory, f"{session_id}.json")
            if os.path.exists(legacy_file_path):
                with open(legacy_file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise FileNotFoundError(f"Interview record not found for session: {session_id}")
            
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    async def list_sessions(self) -> list[str]:
        if not os.path.exists(self.directory):
            return []
        sessions = []
        for name in os.listdir(self.directory):
            path = os.path.join(self.directory, name)
            if os.path.isdir(path):
                if os.path.exists(os.path.join(path, "session.json")):
                    sessions.append(name)
            elif name.endswith(".json"):
                sessions.append(name.replace(".json", ""))
        return sessions

