"""Matching inside the pipeline: waiting for documents, staff review, re-matching, AI check."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Job, JobStatus
from app.services import matching
from tests.files import make_text_pdf
from tests.helpers import FakeLLM, audit_entries, login, run_jobs

LETTERS = Path(__file__).parent / "fixtures" / "query_letters"
SUMMARY = "DISCHARGE SUMMARY\nPatient: Aarav Mehta\nDiagnosis: Osteoarthritis left hip"


@pytest.fixture
def staff(make_user):
    return make_user()


@pytest.fixture
def patient(client, staff, make_patient):
    patient = make_patient(
        assigned_to=(staff,),
        full_name="Aarav Mehta",
        claim_number="CLM/2026/00421",
        policy_number="POL-778812",
    )
    login(client, staff.email)
    return patient


def upload_document(client, patient, text, name, category):
    response = client.post(
        f"/api/v1/patients/{patient.id}/documents",
        files={"file": (name, make_text_pdf(text), "application/pdf")},
        data={"category": category},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload_letter(client, patient, name):
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={
            "file": ("letter.pdf", make_text_pdf((LETTERS / name).read_text()), "application/pdf")
        },
    )
    return response.json()["id"]


def letter(client, letter_id):
    return client.get(f"/api/v1/query-letters/{letter_id}").json()


def test_matching_waits_for_documents_still_being_processed(client, db, patient):
    upload_document(client, patient, SUMMARY, "summary.pdf", "discharge_summary")
    letter_id = upload_letter(client, patient, "05_identity_mismatch.txt")
    # Run only the letter's jobs: the summary's extraction is still queued.
    db.execute(
        Job.__table__.update()
        .where(Job.kind == "document.extract")
        .values(run_after=Job.created_at + __import__("datetime").timedelta(hours=1))
    )
    run_jobs(db)

    match_job = db.scalar(select(Job).where(Job.kind == matching.MATCH_JOB))
    assert match_job.status == JobStatus.QUEUED  # deferred, not run on half the evidence
    assert letter(client, letter_id)["outcome"] is None


def test_staff_review_resolves_a_held_letter(client, db, patient):
    document_id = upload_document(client, patient, SUMMARY, "summary.pdf", "discharge_summary")
    run_jobs(db)
    # The brief example asks for a signed summary; with no AI check the signature is unknown.
    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")
    run_jobs(db)

    held = letter(client, letter_id)
    assert held["outcome"] == "hold_for_staff_review"
    assert held["status"] == "awaiting_review"
    signed_point = held["points"][0]
    assert signed_point["current_match"]["status"] == "needs_review"

    response = client.post(
        f"/api/v1/query-points/{signed_point['id']}/review",
        json={
            "decision": "covered",
            "document_id": document_id,
            "note": "Signature checked on the original scan.",
        },
    )

    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["outcome"] == "send_missing_items_message"
    assert resolved["status"] == "ready_to_notify"
    match = resolved["points"][0]["current_match"]
    assert match["decided_by"] == "staff"
    assert match["status"] == "covered"
    [entry] = audit_entries(db, "query_point.reviewed")
    assert entry.details["decision"] == "covered"


def test_staff_decisions_survive_rematching(client, db, patient):
    document_id = upload_document(client, patient, SUMMARY, "summary.pdf", "discharge_summary")
    run_jobs(db)
    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")
    run_jobs(db)
    point_id = letter(client, letter_id)["points"][0]["id"]
    client.post(
        f"/api/v1/query-points/{point_id}/review",
        json={"decision": "covered", "document_id": document_id, "note": "Checked by hand."},
    )

    assert client.post(f"/api/v1/query-letters/{letter_id}/rematch").status_code == 202
    run_jobs(db)

    assert letter(client, letter_id)["points"][0]["current_match"]["decided_by"] == "staff"


def test_staff_can_dismiss_a_point_that_is_not_a_requirement(client, db, patient):
    run_jobs(db)
    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")
    run_jobs(db)
    point_id = letter(client, letter_id)["points"][2]["id"]

    response = client.post(
        f"/api/v1/query-points/{point_id}/review",
        json={"decision": "not_a_requirement", "note": "Duplicate of point 1."},
    )

    assert response.json()["points"][2]["dismissed_at"] is not None


def test_covered_needs_a_document_and_reviews_need_permission(app, client, db, patient, make_user):
    from fastapi.testclient import TestClient

    from app.core.permissions import Role

    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")
    run_jobs(db)
    point_id = letter(client, letter_id)["points"][0]["id"]

    no_document = client.post(
        f"/api/v1/query-points/{point_id}/review",
        json={"decision": "covered", "note": "Looks fine."},
    )
    assert no_document.status_code == 422

    outsider = TestClient(app)
    login(outsider, make_user(role=Role.STAFF).email)
    assert (
        outsider.post(
            f"/api/v1/query-points/{point_id}/review",
            json={"decision": "needs_from_patient", "note": "Not mine."},
        ).status_code
        == 404
    )


def test_identity_mismatch_holds_until_staff_confirm(client, db, patient):
    letter_id = upload_letter(client, patient, "05_identity_mismatch.txt")
    run_jobs(db)

    held = letter(client, letter_id)
    assert held["outcome"] == "hold_for_staff_review"
    assert "identity_mismatch" in held["review_reasons"]

    confirmed = client.post(
        f"/api/v1/query-letters/{letter_id}/confirm",
        json={"note": "Insurer confirmed by phone: wrong name on letter, right claim."},
    ).json()

    assert confirmed["outcome"] == "send_missing_items_message"
    assert "identity_mismatch" not in confirmed["review_reasons"]
    assert audit_entries(db, "query_letter.confirmed")[0].details["cleared"] == [
        "identity_mismatch"
    ]


def test_ai_check_can_only_raise_doubt(client, db, patient, monkeypatch):
    upload_document(client, patient, SUMMARY, "summary.pdf", "discharge_summary")
    run_jobs(db)
    fake = FakeLLM(
        {
            "LLMQueryItems": {"items": []},
            "LLMMatchOpinion": {"satisfies": "no", "reason": "This is a lab report."},
        }
    )
    monkeypatch.setattr(matching, "get_llm_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "match_model_check", True)
    letter_id = upload_letter(client, patient, "05_identity_mismatch.txt")
    run_jobs(db)

    match = letter(client, letter_id)["points"][0]["current_match"]
    assert match["status"] == "needs_review"
    assert "AI check disagreed" in match["explanation"]
    assert match["model_opinion"]["satisfies"] == "no"


def test_ai_check_agreeing_keeps_covered(client, db, patient, monkeypatch):
    upload_document(client, patient, SUMMARY, "summary.pdf", "discharge_summary")
    run_jobs(db)
    fake = FakeLLM(
        {"LLMMatchOpinion": {"satisfies": "yes", "reason": "It is a discharge summary."}}
    )
    monkeypatch.setattr(matching, "get_llm_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "match_model_check", True)
    letter_id = upload_letter(client, patient, "05_identity_mismatch.txt")
    run_jobs(db)

    match = letter(client, letter_id)["points"][0]["current_match"]
    assert match["status"] == "covered"
    assert match["model_opinion"]["satisfies"] == "yes"
    [entry] = audit_entries(db, "query_letter.matched")
    assert entry.details["points"][0]["model"] == "yes"
