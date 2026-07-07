from abc import ABC, abstractmethod
from typing import Callable, Optional

class IBotRunner(ABC):
    """Interface defining thread runner operations for the mock interview voice loop."""
    @abstractmethod
    def start(
        self, 
        jd: str, 
        resume: str, 
        session_id: str, 
        status_callback: Optional[Callable[[str], None]] = None, 
        transcript_callback: Optional[Callable[[dict], None]] = None,
        custom_prompt: Optional[str] = None
    ) -> None:
        """Start the voice loop in a background thread."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Terminate active pipeline capture and stop the event loop."""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Return True if background bot daemon is running, else False."""
        pass
