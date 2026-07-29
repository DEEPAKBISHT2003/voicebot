from abc import ABC, abstractmethod
from typing import Optional

class IPromptBuilder(ABC):
    """Interface defining requirements for mock interview instructions generation."""
    @abstractmethod
    def build_system_instruction(self, jd: str, resume: str, custom_prompt: Optional[str] = None) -> str:
        """Create mock interview system instruction rules for the LLM."""
        pass
