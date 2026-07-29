from backend.app.core.interfaces.document_parser import IDocumentParser

class TXTDocumentParser(IDocumentParser):
    """Parses plain text document files."""
    def parse(self, file_bytes: bytes, file_name: str) -> str:
        try:
            return file_bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1").strip()
