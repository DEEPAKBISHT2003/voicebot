from abc import ABC, abstractmethod

class IPromptBuilder(ABC):
    """Interface defining requirements for mock interview instructions generation."""
    @abstractmethod
    def build_system_instruction(self, jd: str, resume: str) -> str:
        """Create mock interview system instruction rules for the LLM."""
        pass
