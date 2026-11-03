"""Turn a stored file into per-page text, using the PDF text layer where it exists and OCR
where the page is a scan."""

import io
import re
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.models import TextMethod
from app.processing.file_validation import PDF
from app.processing.ocr import ocr_image
from app.processing.pdf import PDFIUM_LOCK

# A page with fewer letters and digits than this in its text layer is treated as a scan.
MIN_TEXT_LAYER_CHARS = 200
# A page where images cover more than this share of the area is OCR'd as well, since a
# digital page can carry a scanned attachment.
OCR_IMAGE_COVERAGE = 0.3
# A document with fewer letters and digits than this in total is unreadable.
MIN_READABLE_CHARS = 30


class ExtractionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    method: TextMethod
    ocr_confidence: float | None
    char_count: int


def count_chars(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", text))


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pages(data: bytes, mime_type: str) -> list[ExtractedPage]:
    if mime_type == PDF:
        return _extract_pdf(data)
    try:
        with Image.open(io.BytesIO(data)) as image:
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except OSError:
        raise ExtractionError("image_unreadable") from None
    text, confidence = ocr_image(upright)
    text = _tidy(text)
    return [ExtractedPage(1, text, TextMethod.OCR, confidence, count_chars(text))]


def _image_coverage(page: pdfium.PdfPage) -> float:
    width, height = page.get_size()
    covered = 0.0
    for obj in page.get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,)):
        left, bottom, right, top = obj.get_pos()
        covered += max(0.0, right - left) * max(0.0, top - bottom)
    return min(1.0, covered / (width * height)) if width and height else 0.0


def _extract_pdf(data: bytes) -> list[ExtractedPage]:
    scale = get_settings().ocr_dpi / 72
    pages = []
    # The worker is a single process, so holding the PDFium lock through OCR blocks nothing.
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise ExtractionError("pdf_unreadable") from None
        try:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    textpage = page.get_textpage()
                    layer = _tidy(textpage.get_text_bounded())
                    textpage.close()
                    layer_chars = count_chars(layer)
                    needs_ocr = (
                        layer_chars < MIN_TEXT_LAYER_CHARS
                        or _image_coverage(page) > OCR_IMAGE_COVERAGE
                    )
                    rendered = page.render(scale=scale).to_pil() if needs_ocr else None
                finally:
                    page.close()

                if rendered is None:
                    pages.append(
                        ExtractedPage(index + 1, layer, TextMethod.TEXT_LAYER, None, layer_chars)
                    )
                    continue

                ocr_text, confidence = ocr_image(rendered)
                ocr_text = _tidy(ocr_text)
                if layer_chars >= MIN_TEXT_LAYER_CHARS:
                    text, method = f"{layer}\n\n{ocr_text}".strip(), TextMethod.TEXT_LAYER_AND_OCR
                elif count_chars(ocr_text) >= layer_chars:
                    text, method = ocr_text, TextMethod.OCR
                else:
                    text, method, confidence = layer, TextMethod.TEXT_LAYER, None
                pages.append(
                    ExtractedPage(index + 1, text, method, confidence, count_chars(text))
                )
        except pdfium.PdfiumError:
            raise ExtractionError("pdf_unreadable") from None
        finally:
            pdf.close()
    return pages


def render_page_images(data: bytes, mime_type: str, *, max_pages: int, max_px: int) -> list[bytes]:
    """JPEG images of the first pages, for the vision model."""
    images: list[Image.Image] = []
    if mime_type == PDF:
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                for index in range(min(max_pages, len(pdf))):
                    page = pdf[index]
                    try:
                        scale = max_px / max(page.get_size())
                        images.append(page.render(scale=scale).to_pil())
                    finally:
                        page.close()
            finally:
                pdf.close()
    else:
        with Image.open(io.BytesIO(data)) as image:
            upright = ImageOps.exif_transpose(image)
            upright.thumbnail((max_px, max_px))
            images.append(upright.copy())

    encoded = []
    for image in images:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=85)
        encoded.append(buffer.getvalue())
    return encoded
