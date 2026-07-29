import io
from pypdf import PdfReader
from backend.app.core.interfaces.document_parser import IDocumentParser

class PDFDocumentParser(IDocumentParser):
    """Parses PDF binary streams and extracts text pages."""
    def parse(self, file_bytes: bytes, file_name: str) -> str:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text.append(page_text)
        return "\n".join(text).strip()
