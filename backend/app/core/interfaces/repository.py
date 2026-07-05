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
