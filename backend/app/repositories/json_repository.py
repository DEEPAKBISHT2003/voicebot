import os
import json
from backend.app.core.interfaces.repository import IInterviewRepository
from backend.app.core.config import Settings

class JSONFileInterviewRepository(IInterviewRepository):
    """Saves and loads session records from local JSON files on disk."""
    def __init__(self, directory: str = Settings.DEFAULT_STORAGE_DIR):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)
        
    def save_session(self, session_id: str, data: dict) -> None:
        file_path = os.path.join(self.directory, f"{session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, default=str)
            
    def load_session(self, session_id: str) -> dict:
        file_path = os.path.join(self.directory, f"{session_id}.json")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Interview record not found for session: {session_id}")
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
