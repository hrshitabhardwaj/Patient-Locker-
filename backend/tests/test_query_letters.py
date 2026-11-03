"""Query letters through the API: upload, automatic extraction, identity check, access."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Job
from app.services import query_letters
from tests.files import make_pdf, make_text_pdf
from tests.helpers import FakeLLM, audit_entries, login, run_jobs

LETTERS = Path(__file__).parent / "fixtures" / "query_letters"


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


def upload_letter(client, patient, name=None, data=None):
    data = data or make_text_pdf((LETTERS / name).read_text())
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": ("query_letter.pdf", data, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_starts_extraction_automatically(client, db, patient):
    letter = upload_letter(client, patient, "01_numbered_brief_example.txt")

    assert letter["status"] == "received"
    [job] = db.scalars(select(Job)).all()
    assert job.kind == query_letters.EXTRACT_QUERY_JOB
    # The letter is not counted as a supporting document in the locker.
    assert client.get("/api/v1/patients").json()["items"][0]["document_count"] == 0


def test_points_are_extracted_and_stored(client, db, patient):
    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")["id"]

    assert run_jobs(db) == 4  # extraction, matching, the patient message, sending

    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()
    # The locker is empty, so every requirement must come from the patient.
    assert detail["status"] == "notified"
    assert detail["outcome"] == "send_missing_items_message"
    assert {p["current_match"]["status"] for p in detail["points"]} == {"needs_from_patient"}
    assert detail["identity_status"] == "match"
    assert detail["identity_details"] == {
        "claim_number": {"result": "match"},
        "policy_number": {"result": "match"},
        "patient_name": {"result": "match"},
    }
    assert detail["needs_review"] is False
    assert detail["letter_date"] == "28 October 2026"
    assert detail["claim_reference"] == "CLM/2026/00421"
    points = detail["points"]
    assert [(p["seq"], p["requirement_type"]) for p in points] == [
        (1, "discharge_summary"),
        (2, "pharmacy_bill"),
        (3, "clarification"),
    ]
    assert points[0]["requirement"] == "Discharge summary — signed by the treating doctor"
    assert points[0]["conditions"] == [
        {"code": "signed_by_doctor", "text": "signed by the treating doctor"}
    ]
    assert points[0]["source_text"] == "Submit a discharge summary signed by the treating doctor."

    skipped = [s for s in detail["extraction_details"]["segments"] if not s["used"]]
    assert {s["reason"] for s in skipped} == {
        "introduces a list",
        "general instruction, not a specific requirement",
    }

    [entry] = audit_entries(db, "query_letter.extracted")
    assert entry.details["points"] == 3
    assert entry.details["identity"] == "match"
    assert entry.actor_user_id is None


def test_letter_for_another_patient_is_flagged(client, db, patient):
    letter_id = upload_letter(client, patient, "05_identity_mismatch.txt")["id"]
    run_jobs(db)

    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()

    assert detail["identity_status"] == "mismatch"
    assert detail["identity_details"]["claim_number"] == {"result": "mismatch"}
    assert detail["identity_details"]["patient_name"] == {"result": "mismatch"}
    assert detail["needs_review"] is True
    assert "identity_mismatch" in detail["review_reasons"]


def test_unreadable_letter_fails_for_review(client, db, patient):
    letter_id = upload_letter(client, patient, data=make_pdf())["id"]
    run_jobs(db)

    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()

    assert detail["status"] == "failed"
    assert detail["error"] == "letter_unreadable"
    assert detail["needs_review"] is True
    assert audit_entries(db, "query_letter.failed")[0].details == {"error": "letter_unreadable"}


def test_model_second_opinion_is_recorded(client, db, patient, monkeypatch):
    fake = FakeLLM(
        {
            "LLMQueryItems": [
                {
                    "items": [
                        {
                            "requirement_type": "discharge_summary",
                            "requirement": "discharge summary signed by the treating doctor",
                            "conditions": [],
                        }
                    ]
                },
                {
                    "items": [
                        {
                            "requirement_type": "pharmacy_bill",
                            "requirement": "itemised pharmacy bill",
                            "conditions": ["itemised"],
                        }
                    ]
                },
                {
                    "items": [
                        {
                            "requirement_type": "clarification",
                            "requirement": "date of hospital admission",
                            "conditions": [],
                        }
                    ]
                },
            ]
        }
    )
    monkeypatch.setattr(query_letters, "get_llm_client", lambda: fake)
    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")["id"]

    run_jobs(db)

    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()
    assert [p["source"] for p in detail["points"]] == ["rules_and_model"] * 3
    assert detail["extraction_details"]["method"] == "rules_and_model"
    assert detail["extraction_details"]["model"] == "fake:test"
    assert all("not instructions" in call["system"] for call in fake.calls)


def test_other_staff_cannot_see_the_letter(app, client, db, make_user, patient):
    from fastapi.testclient import TestClient

    letter_id = upload_letter(client, patient, "01_numbered_brief_example.txt")["id"]
    outsider = TestClient(app)
    login(outsider, make_user().email)

    assert outsider.get(f"/api/v1/query-letters/{letter_id}").status_code == 404
    assert outsider.get(f"/api/v1/patients/{patient.id}/query-letters").status_code == 404


def test_viewing_is_audited_and_reprocess_is_deduplicated(client, db, patient):
    letter_id = upload_letter(client, patient, "03_prose_paragraph.txt")["id"]
    run_jobs(db)

    client.get(f"/api/v1/query-letters/{letter_id}")
    first = client.post(f"/api/v1/query-letters/{letter_id}/reprocess").json()
    second = client.post(f"/api/v1/query-letters/{letter_id}/reprocess").json()
    run_jobs(db)

    assert len(audit_entries(db, "query_letter.viewed")) == 1
    assert (first, second) == ({"queued": True}, {"queued": False})
    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()
    assert len(detail["points"]) == 4  # replaced, not duplicated


def test_patient_letter_list(client, db, patient):
    upload_letter(client, patient, "01_numbered_brief_example.txt")
    run_jobs(db)

    [letter] = client.get(f"/api/v1/patients/{patient.id}/query-letters").json()

    assert letter["point_count"] == 3
    assert letter["original_filename"] == "query_letter.pdf"
