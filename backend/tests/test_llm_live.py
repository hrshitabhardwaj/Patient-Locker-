"""Checks against the real local model. Slow; run with `make test-llm` (needs Ollama running).

These record how the configured model actually performs on the sample documents. Signature
and stamp detection on a small local model is a known weak point; the assertions below are the
minimum the pipeline relies on, and the printed table shows the full picture.
"""

import pytest

from app.ai.document_profile import profile_document
from app.ai.llm import OllamaClient
from app.core.config import get_settings
from app.processing.extraction import extract_pages, render_page_images
from tests.helpers import SAMPLES

pytestmark = pytest.mark.ollama

EXPECTED_MARKS = {
    "01_discharge_summary.pdf": (False, False),
    "02_hospital_bill.pdf": (False, False),
    "03_pharmacy_bill.pdf": (False, False),
    "04_scanned_medical_note.pdf": (True, False),
    "05_discharge_summary_signature_stamp.pdf": (True, True),
}


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    return OllamaClient(settings.ollama_base_url, settings.ollama_model, 600)


@pytest.mark.parametrize("name", sorted(EXPECTED_MARKS))
def test_profile_on_sample(client, name):
    data = (SAMPLES / name).read_bytes()
    pages = extract_pages(data, "application/pdf")
    images = render_page_images(data, "application/pdf", max_pages=3, max_px=1024)

    outcome = profile_document(
        client,
        full_text="\n\n".join(p.text for p in pages),
        page_count=len(pages),
        images=images,
    )

    facts = outcome.facts
    found = (bool(facts["signatures"]), bool(facts["stamps"]))
    print(
        f"\n{name}: type={outcome.document_type.value} signature/stamp={found} "
        f"expected={EXPECTED_MARKS[name]} unchecked={facts['pages_unchecked_for_marks']} "
        f"{outcome.duration_ms / 1000:.0f}s"
    )
    # Whatever the model says, every reported text value must be checked against the text.
    assert all("verified" in field for field in facts["fields"].values())
    assert facts["pages_checked_for_marks"] or facts["pages_unchecked_for_marks"]
