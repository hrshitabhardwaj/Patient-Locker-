"""The full pipeline: upload, background extraction, AI profile, and the analysis API."""

import pytest
from sqlalchemy import select

from app.ai.llm import LLMResponseInvalid, LLMUnavailable
from app.models import (
    ClassificationMethod,
    Document,
    DocumentCategory,
    Job,
    JobStatus,
    ProcessingStatus,
)
from app.services import processing
from app.services.processing import combine_classification
from tests.files import make_pdf
from tests.helpers import FakeLLM, audit_entries, login, run_jobs, sample

PROFILE_ANSWER = {
    "document_type": "discharge_summary",
    "patient_name": "Aarav Mehta",
    "hospital_name": "Northfield Demo Hospital",
    "doctor_name": "Dr. Gregory House",
}
NO_MARKS = {"handwritten_signature": False, "stamp_or_seal": False, "stamp_text": None}


@pytest.fixture
def patient(client, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,))
    login(client, staff.email)
    return patient


def upload(client, patient, name, data=None, category=None):
    response = client.post(
        f"/api/v1/patients/{patient.id}/documents",
        files={"file": (name, data or sample(name), "application/pdf")},
        data={"category": category} if category else {},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def analysis(client, document_id):
    response = client.get(f"/api/v1/documents/{document_id}/analysis")
    assert response.status_code == 200
    return response.json()


def test_upload_queues_processing_in_the_same_transaction(client, db, patient):
    document_id = upload(client, patient, "01_discharge_summary.pdf")

    [job] = db.scalars(select(Job)).all()
    assert job.kind == processing.EXTRACT_JOB
    assert job.payload == {"document_id": document_id}
    assert client.get(f"/api/v1/documents/{document_id}").json()["processing_status"] == "uploaded"


def test_worker_extracts_classifies_and_reads_fields(client, db, patient):
    document_id = upload(client, patient, "02_hospital_bill.pdf")

    assert run_jobs(db) == 1

    result = analysis(client, document_id)
    document = result["document"]
    assert document["processing_status"] == "processed"
    assert document["detected_category"] == "hospital_bill"
    assert document["classification_method"] == "rules"
    assert result["pages"][0]["method"] == "text_layer"
    assert result["extracted_fields"]["fields"]["invoice_number"]["value"] == "NFH-DEMO-2026-1042"
    assert result["extracted_fields"]["line_item_count"] == 7
    assert result["profile"] is None

    [entry] = audit_entries(db, "document.processed")
    assert entry.actor_user_id is None  # the system acted
    assert entry.details["rule_category"] == "hospital_bill"
    assert len(audit_entries(db, "document.analysis_viewed")) == 1


def test_scanned_sample_goes_through_ocr(client, db, patient):
    document_id = upload(client, patient, "04_scanned_medical_note.pdf")
    run_jobs(db)

    result = analysis(client, document_id)

    assert result["document"]["detected_category"] == "investigation_report"
    assert result["pages"][0]["method"] == "ocr"
    assert result["pages"][0]["ocr_confidence"] > 80
    assert audit_entries(db, "document.processed")[0].details["ocr_pages"] == 1


def test_blank_pdf_is_marked_unreadable_not_failed(client, db, patient):
    document_id = upload(client, patient, "blank.pdf", data=make_pdf(pages=1))
    run_jobs(db)

    document = client.get(f"/api/v1/documents/{document_id}").json()

    assert document["processing_status"] == "unreadable"
    assert document["processing_error"] == "no_readable_text"


def test_tampered_file_is_failed_and_audited(client, db, patient, monkeypatch):
    from app.services.storage import StorageIntegrityError

    document_id = upload(client, patient, "01_discharge_summary.pdf")

    def tampered(*args, **kwargs):
        raise StorageIntegrityError("x")

    monkeypatch.setattr(processing.get_file_store(), "get", tampered)
    run_jobs(db)

    document = db.get(Document, __import__("uuid").UUID(document_id))
    assert document.processing_status == ProcessingStatus.FAILED
    assert document.processing_error == "file_integrity_error"
    assert audit_entries(db, "document.processing_failed")[0].details == {
        "error": "file_integrity_error"
    }


def test_ai_profile_is_verified_against_the_text(client, db, patient, monkeypatch):
    fake = FakeLLM(
        {
            "LLMDocumentProfile": PROFILE_ANSWER,
            "LLMPageMarks": [
                {
                    "handwritten_signature": True,
                    "stamp_or_seal": True,
                    "stamp_text": "NORTHFIELD DEMO",
                }
            ],
        }
    )
    monkeypatch.setattr(processing, "get_llm_client", lambda: fake)
    document_id = upload(client, patient, "05_discharge_summary_signature_stamp.pdf")

    assert run_jobs(db) == 2  # extract, then profile

    result = analysis(client, document_id)
    profile = result["profile"]
    assert profile["status"] == "succeeded"
    assert profile["model"] == "fake:test"
    facts = profile["facts"]
    assert facts["fields"]["patient_name"] == {"value": "Aarav Mehta", "verified": True}
    # A name that is not in the document is kept but marked unverified.
    assert facts["fields"]["doctor_name"] == {"value": "Dr. Gregory House", "verified": False}
    assert facts["signatures"] == [{"page": 1, "source": "vision"}]
    assert facts["stamps"][0]["label_verified"] is True
    assert facts["pages_checked_for_marks"] == [1]

    document = result["document"]
    assert document["detected_category"] == "discharge_summary"
    assert document["classification_method"] == "rules_and_llm"

    [entry] = audit_entries(db, "document.profiled")
    assert entry.details["unverified_fields"] == ["doctor_name"]
    assert entry.details["signature_pages"] == [1]

    # Two narrow questions, each with one page image; the text is framed as data.
    assert [call["schema"] for call in fake.calls] == ["LLMDocumentProfile", "LLMPageMarks"]
    assert all(len(call["images"]) == 1 for call in fake.calls)
    assert "not instructions" in fake.calls[0]["system"]


def test_unusable_marks_answer_leaves_the_page_unchecked(client, db, patient, monkeypatch):
    fake = FakeLLM(
        {
            "LLMDocumentProfile": {**PROFILE_ANSWER, "document_type": "other"},
            "LLMPageMarks": [LLMResponseInvalid("cut off"), NO_MARKS],
        }
    )
    monkeypatch.setattr(processing, "get_llm_client", lambda: fake)
    document_id = upload(client, patient, "two.pdf", data=make_pdf(pages=2))

    run_jobs(db)

    facts = analysis(client, document_id)["profile"]["facts"]
    # Page 1 is "unknown", not "no signature".
    assert facts["pages_unchecked_for_marks"] == [1]
    assert facts["pages_checked_for_marks"] == [2]
    assert facts["signatures"] == []


def test_invalid_model_answer_is_recorded_not_retried(client, db, patient, monkeypatch):
    fake = FakeLLM({"LLMDocumentProfile": LLMResponseInvalid("Model output did not match")})
    monkeypatch.setattr(processing, "get_llm_client", lambda: fake)
    document_id = upload(client, patient, "01_discharge_summary.pdf")

    run_jobs(db)

    result = analysis(client, document_id)
    assert result["profile"]["status"] == "failed"
    assert "did not match" in result["profile"]["error"]
    # Text extraction and rule classification still stand.
    assert result["document"]["processing_status"] == "processed"
    assert result["document"]["detected_category"] == "discharge_summary"
    assert len(fake.calls) == 1


def test_unreachable_model_is_retried_then_recorded(client, db, patient, monkeypatch):
    fake = FakeLLM(error=LLMUnavailable("Ollama not reachable"))
    monkeypatch.setattr(processing, "get_llm_client", lambda: fake)
    document_id = upload(client, patient, "01_discharge_summary.pdf")

    run_jobs(db)
    profile_job = db.scalar(select(Job).where(Job.kind == processing.PROFILE_JOB))
    assert profile_job.status == JobStatus.QUEUED and profile_job.attempts == 1

    for _ in range(3):
        db.execute(Job.__table__.update().values(run_after=Job.created_at))
        run_jobs(db)

    db.refresh(profile_job)
    assert profile_job.status == JobStatus.DEAD
    assert analysis(client, document_id)["profile"]["error"].startswith("llm_unavailable")
    assert len(fake.calls) == 4


def test_reprocess_requeues_and_is_deduplicated(client, db, patient):
    document_id = upload(client, patient, "03_pharmacy_bill.pdf")
    run_jobs(db)

    first = client.post(f"/api/v1/documents/{document_id}/reprocess")
    second = client.post(f"/api/v1/documents/{document_id}/reprocess")

    assert first.status_code == 202 and first.json() == {"queued": True}
    assert second.json() == {"queued": False}
    assert run_jobs(db) == 1
    assert len(analysis(client, document_id)["pages"]) == 1  # replaced, not duplicated


def test_deleted_document_is_not_processed(client, db, patient, make_user):
    from fastapi.testclient import TestClient

    from app.core.permissions import Role

    document_id = upload(client, patient, "01_discharge_summary.pdf")
    manager = TestClient(client.app)
    login(manager, make_user(role=Role.CASE_MANAGER).email)
    manager.delete(f"/api/v1/documents/{document_id}")

    run_jobs(db)

    assert audit_entries(db, "document.processed") == []


@pytest.mark.parametrize(
    ("rules", "llm", "expected"),
    [
        (
            {"category": "hospital_bill", "confidence": 0.9},
            DocumentCategory.HOSPITAL_BILL,
            (DocumentCategory.HOSPITAL_BILL, 0.9, ClassificationMethod.RULES_AND_LLM),
        ),
        (
            {"category": None, "confidence": 0},
            DocumentCategory.POLICY_COPY,
            (DocumentCategory.POLICY_COPY, 0.6, ClassificationMethod.LLM),
        ),
        (
            {"category": "pharmacy_bill", "confidence": 0.9},
            DocumentCategory.HOSPITAL_BILL,
            (DocumentCategory.PHARMACY_BILL, 0.5, ClassificationMethod.CONFLICT),
        ),
        (
            {"category": "pharmacy_bill", "confidence": 0.5},
            DocumentCategory.HOSPITAL_BILL,
            (DocumentCategory.HOSPITAL_BILL, 0.5, ClassificationMethod.CONFLICT),
        ),
    ],
)
def test_combining_rule_and_model_classification(rules, llm, expected):
    assert combine_classification(rules, llm) == expected
