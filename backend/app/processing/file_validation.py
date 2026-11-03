"""Decide whether an uploaded file is a genuine, usable PDF or image.

The file type is read from the content itself, never from the filename or the browser's
claimed content type, and each file is fully opened to prove it is not damaged.
"""

import io
import unicodedata
import warnings
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.processing.pdf import PDFIUM_LOCK

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"

EXTENSIONS = {PDF: ".pdf", JPEG: ".jpg", PNG: ".png"}
# Phone cameras often save JPEGs as MPO (multi-picture JPEG); they are ordinary photos.
_PIL_FORMATS = {JPEG: {"JPEG", "MPO"}, PNG: {"PNG"}}
_ACCEPTED_SUFFIXES = {PDF: (".pdf",), JPEG: (".jpg", ".jpeg"), PNG: (".png",)}


class UploadRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedFile:
    mime_type: str
    page_count: int


def detect_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    # The PDF header may follow a little leading junk; readers accept it within 1 KB.
    if b"%PDF-" in data[:1024]:
        return PDF
    return None


def validate_upload(data: bytes) -> ValidatedFile:
    settings = get_settings()
    if not data:
        raise UploadRejected("empty", "The file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected(
            "too_large", f"The file is larger than the {settings.max_upload_mb} MB limit."
        )

    mime_type = detect_mime_type(data)
    if mime_type is None:
        raise UploadRejected("unsupported_type", "Only PDF, JPEG and PNG files are accepted.")
    if mime_type == PDF:
        return ValidatedFile(PDF, _check_pdf(data))
    _check_image(data, mime_type)
    return ValidatedFile(mime_type, 1)


def _check_pdf(data: bytes) -> int:
    settings = get_settings()
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise UploadRejected(
                    "encrypted",
                    "This PDF is password-protected. Please upload an unlocked copy.",
                ) from None
            raise UploadRejected("corrupt", "This PDF is damaged and cannot be opened.") from None
        try:
            page_count = len(pdf)
            if page_count:
                # Opening the first page catches files whose page tree is broken.
                pdf[0].close()
        except pdfium.PdfiumError:
            raise UploadRejected("corrupt", "This PDF is damaged and cannot be opened.") from None
        finally:
            pdf.close()

    if page_count == 0:
        raise UploadRejected("corrupt", "This PDF has no pages.")
    if page_count > settings.max_pdf_pages:
        raise UploadRejected(
            "too_many_pages", f"This PDF has more than {settings.max_pdf_pages} pages."
        )
    return page_count


def _check_image(data: bytes, mime_type: str) -> None:
    settings = get_settings()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in _PIL_FORMATS[mime_type]:
                    raise UploadRejected("corrupt", "The image is damaged and cannot be opened.")
                width, height = image.size
                if width * height > settings.max_image_pixels:
                    raise UploadRejected("image_too_large", "The image dimensions are too large.")
                # Decode every pixel: catches truncated and malformed files.
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise UploadRejected("image_too_large", "The image dimensions are too large.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise UploadRejected("corrupt", "The image is damaged and cannot be opened.") from None


def clean_filename(name: str | None, mime_type: str) -> str:
    """A display-safe filename: no path, no control or reserved characters, right extension."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    base = base.strip(" .")[:200] or "document"
    if not base.lower().endswith(_ACCEPTED_SUFFIXES[mime_type]):
        base += EXTENSIONS[mime_type]
    return base
