"""Text extraction, OCR, rule classification and field reading, on the real sample documents."""

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.models import DocumentCategory, TextMethod
from app.processing.classifier import classify_text
from app.processing.extraction import MIN_READABLE_CHARS, extract_pages, render_page_images
from app.processing.fields import extract_fields
from tests.files import make_pdf
from tests.helpers import sample

EXPECTED = {
    "01_discharge_summary.pdf": DocumentCategory.DISCHARGE_SUMMARY,
    "02_hospital_bill.pdf": DocumentCategory.HOSPITAL_BILL,
    "03_pharmacy_bill.pdf": DocumentCategory.PHARMACY_BILL,
    "04_scanned_medical_note.pdf": DocumentCategory.INVESTIGATION_REPORT,
    "05_discharge_summary_signature_stamp.pdf": DocumentCategory.DISCHARGE_SUMMARY,
}


def _text_image(lines: list[str], fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (1700, 200 + 90 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    for i, line in enumerate(lines):
        draw.text((80, 80 + 90 * i), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_samples_are_classified_correctly(name):
    pages = extract_pages(sample(name), "application/pdf")
    full_text = "\n\n".join(p.text for p in pages)

    result = classify_text(pages[0].text, full_text)

    assert result.category == EXPECTED[name]
    assert result.confidence >= 0.9


def test_digital_pdf_uses_the_text_layer():
    [page] = extract_pages(sample("01_discharge_summary.pdf"), "application/pdf")

    assert page.method == TextMethod.TEXT_LAYER
    assert "Left total hip arthroplasty" in page.text
    assert page.ocr_confidence is None


def test_scanned_pdf_is_ocrd_despite_a_small_text_overlay():
    # The scan carries a 29-character text overlay; a "has text?" check alone would skip OCR.
    [page] = extract_pages(sample("04_scanned_medical_note.pdf"), "application/pdf")

    assert page.method == TextMethod.OCR
    assert "Post-operative pelvis X-ray" in page.text
    assert "DEMO-THA-001" in page.text
    assert page.ocr_confidence > 80


def test_photo_of_a_document_is_ocrd():
    data = _text_image(["PHARMACY BILL", "Invoice number PH-555-12", "Total 1,250.00"], "JPEG")

    [page] = extract_pages(data, "image/jpeg")

    assert page.method == TextMethod.OCR
    assert "PHARMACY BILL" in page.text
    assert classify_text(page.text, page.text).category == DocumentCategory.PHARMACY_BILL


def test_blank_document_has_no_readable_text():
    pages = extract_pages(make_pdf(pages=2), "application/pdf")

    assert len(pages) == 2
    assert sum(p.char_count for p in pages) < MIN_READABLE_CHARS


def test_unclassifiable_text_gets_no_category():
    assert classify_text("Dear Sir, thank you.", "Dear Sir, thank you.").category is None


def test_body_mentions_do_not_override_the_title():
    text = "FINAL HOSPITAL BILL\nAs per the discharge summary attached\nTotal 5,000.00"

    result = classify_text(text, text)

    assert result.category == DocumentCategory.HOSPITAL_BILL


def test_labelled_fields_from_the_hospital_bill():
    pages = extract_pages(sample("02_hospital_bill.pdf"), "application/pdf")

    found = extract_fields([(p.page_number, p.text) for p in pages])
    values = {name: field["value"] for name, field in found["fields"].items()}

    assert values["patient_name"] == "Aarav Mehta"
    assert values["patient_id"] == "DEMO-THA-001"
    assert values["date_of_birth"] == "14 Feb 1981"
    assert values["invoice_number"] == "NFH-DEMO-2026-1042"
    assert values["admission_date"] == "18 October 2026"
    assert values["discharge_date"] == "23 October 2026"
    assert values["total_amount"] == "3,25,000.00"
    assert found["line_item_count"] == 7


def test_labelled_fields_ignore_lookalike_headings():
    text = "Discharge advice\nDischarge condition Stable\nDischarge date 23 October 2026"

    found = extract_fields([(1, text)])

    assert found["fields"]["discharge_date"]["value"] == "23 October 2026"


def test_fields_from_the_scanned_note():
    pages = extract_pages(sample("04_scanned_medical_note.pdf"), "application/pdf")

    found = extract_fields([(p.page_number, p.text) for p in pages])["fields"]

    assert found["document_date"]["value"] == "20 October 2026"
    assert found["patient_id"]["value"] == "DEMO-THA-001"


def test_page_images_for_the_model_are_small_jpegs():
    [image] = render_page_images(
        sample("05_discharge_summary_signature_stamp.pdf"),
        "application/pdf",
        max_pages=3,
        max_px=1024,
    )

    with Image.open(io.BytesIO(image)) as decoded:
        assert decoded.format == "JPEG"
        assert max(decoded.size) <= 1024
