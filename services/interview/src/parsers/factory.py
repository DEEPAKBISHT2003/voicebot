from backend.app.core.interfaces.document_parser import IDocumentParser
from backend.app.parsers.pdf_parser import PDFDocumentParser
from backend.app.parsers.txt_parser import TXTDocumentParser

class DocumentParserFactory:
    """Orchestrates creating the appropriate parser subclasses based on file types (OCP)."""
    @staticmethod
    def get_parser(file_name: str) -> IDocumentParser:
        ext = file_name.split(".")[-1].lower()
        if ext == "pdf":
            return PDFDocumentParser()
        elif ext in ("txt", "md"):
            return TXTDocumentParser()
        else:
            raise ValueError(f"Unsupported file format: .{ext}")
