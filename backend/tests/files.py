"""Build real PDF and image files in memory for upload tests."""

import io

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader, PdfWriter


def make_pdf(pages: int = 1, *, marker: str = "") -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    buffer = io.BytesIO()
    pdf.save(buffer)
    pdf.close()
    # A trailing comment makes otherwise-identical test PDFs distinct files.
    return buffer.getvalue() + f"\n% {marker}\n".encode()


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf())))
    writer.encrypt(user_password="secret", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_text_pdf(text: str) -> bytes:
    """A digital PDF with a real text layer, one line per text line."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    for line in text.splitlines():
        if y < 60:
            pdf.showPage()
            y = 800
        pdf.drawString(50, y, line)
        y -= 16
    pdf.save()
    return buffer.getvalue()


def make_image(fmt: str = "PNG", size: tuple[int, int] = (40, 30), color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()
