from abc import ABC, abstractmethod

class IDocumentParser(ABC):
    """Interface defining parsing capabilities for job descriptions or resumes."""
    @abstractmethod
    def parse(self, file_bytes: bytes, file_name: str) -> str:
        """Parse raw file bytes and extract clear string contents."""
        pass
