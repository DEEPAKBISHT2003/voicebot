from abc import ABC, abstractmethod

class IInterviewRepository(ABC):
    """Interface defining storage operations for completed mock interviews."""
    @abstractmethod
    def save_session(self, session_id: str, data: dict) -> None:
        """Persist session dialog script and context data."""
        pass
    
    @abstractmethod
    def load_session(self, session_id: str) -> dict:
        """Retrieve previously saved session logs."""
        pass

    @abstractmethod
    def create_session(self, jd: str, resume: str, custom_prompt: str) -> str:
        """Create a new interview session folder, generate UUID, and write initial files."""
        pass

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """Return a list of all saved session IDs."""
        pass

