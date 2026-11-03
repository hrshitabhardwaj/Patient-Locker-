"""Privacy guarantees, checked across the whole API rather than route by route."""

import io
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import RedactingFilter
from app.core.permissions import Role
from app.main import create_app
from app.models import Document, MatchResult, Patient, QueryLetter
from tests.files import make_pdf, make_text_pdf
from tests.helpers import DEFAULT_PASSWORD, audit_entries, login, run_jobs
from tests.test_company_dev_set import DEV, _set_up

LETTERS = Path(__file__).parent / "fixtures" / "query_letters"

# Request bodies for routes that change something; everything else is sent without a body.
BODIES = {
    ("PATCH", "/api/v1/patients/{patient_id}"): {"json": {"full_name": "Changed By Outsider"}},
    ("PUT", "/api/v1/patients/{patient_id}/assignments"): {"json": {"user_ids": []}},
    ("POST", "/api/v1/patients/{patient_id}/documents"): {
        "files": {"file": ("x.pdf", make_pdf(), "application/pdf")}
    },
    ("POST", "/api/v1/patients/{patient_id}/query-letters"): {
        "files": {"file": ("x.pdf", make_pdf(), "application/pdf")}
    },
    ("PATCH", "/api/v1/documents/{document_id}"): {"json": {"declared_category": "other"}},
    ("PATCH", "/api/v1/query-letters/{letter_id}"): {"json": {"insurer_name": "Outsider Insurer"}},
    ("POST", "/api/v1/query-letters/{letter_id}/confirm"): {"json": {"note": "outsider attempt"}},
    ("POST", "/api/v1/query-points/{point_id}/review"): {
        "json": {"decision": "needs_from_patient", "note": "outsider attempt"}
    },
    ("PATCH", "/api/v1/users/{user_id}"): {"json": {"full_name": "Changed"}},
}


def _routes_with_ids():
    for path, operations in create_app().openapi()["paths"].items():
        if path.startswith("/api/v1") and "{" in path and "/webhooks/" not in path:
            for method in operations:
                yield method.upper(), path


@pytest.fixture
def owned_records(app, client, db, make_user, make_patient):
    """A patient with a document, a matched letter and a sent message, owned by one staff member."""
    owner = make_user()
    patient = make_patient(assigned_to=(owner,), full_name="Aarav Mehta", claim_number="CLM-1")
    login(client, owner.email)
    document = client.post(
        f"/api/v1/patients/{patient.id}/documents",
        files={
            "file": (
                "summary.pdf",
                make_text_pdf("DISCHARGE SUMMARY\nPatient: Aarav Mehta"),
                "application/pdf",
            )
        },
        data={"category": "discharge_summary"},
    ).json()
    letter = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={
            "file": (
                "q.pdf",
                make_text_pdf((LETTERS / "01_numbered_brief_example.txt").read_text()),
                "application/pdf",
            )
        },
    ).json()
    run_jobs(db)
    point = client.get(f"/api/v1/query-letters/{letter['id']}").json()["points"][0]
    return {
        "patient_id": str(patient.id),
        "document_id": document["id"],
        "letter_id": letter["id"],
        "point_id": point["id"],
        "user_id": str(owner.id),
    }


def test_unassigned_staff_cannot_reach_anything(app, db, make_user, owned_records):
    outsider = TestClient(app)
    login(outsider, make_user(role=Role.STAFF).email)

    checked = []
    for method, path in _routes_with_ids():
        url = re.sub(r"\{(\w+)\}", lambda m: owned_records[m.group(1)], path)
        response = outsider.request(method, url, **BODIES.get((method, path), {}))
        assert response.status_code in (403, 404), f"{method} {path} -> {response.status_code}"
        assert "Aarav" not in response.text and "asha@" not in response.text, (
            f"{method} {path} leaked"
        )
        checked.append((method, path))

    # Every route that takes an ID was tried, including edits, deletes and uploads.
    assert len(checked) >= 25
    assert {m for m, _ in checked} >= {"GET", "POST", "PATCH", "PUT", "DELETE"}

    # Nothing changed.
    patient = db.get(Patient, owned_records["patient_id"])
    db.refresh(patient)
    assert patient.full_name == "Aarav Mehta"
    assert db.get(Document, owned_records["document_id"]).deleted_at is None
    assert db.scalars(select(MatchResult).where(MatchResult.decided_by == "staff")).all() == []
    assert db.get(QueryLetter, owned_records["letter_id"]).insurer_name != "Outsider Insurer"
    # And the attempts are on record.
    assert len(audit_entries(db, "access.denied")) >= 20


def test_logs_never_contain_patient_details(client, db, make_user, make_patient):
    """Run the whole pipeline and read what actually reached the log output."""
    handler = next(
        h
        for h in logging.getLogger().handlers
        if any(isinstance(f, RedactingFilter) for f in h.filters)
    )
    captured = io.StringIO()
    original = handler.setStream(captured)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        patient = _set_up(client, db, make_user, make_patient, "letter_01.pdf")
        letter = client.post(
            f"/api/v1/patients/{patient.id}/query-letters",
            files={"file": ("l.pdf", (DEV / "letter_01.pdf").read_bytes(), "application/pdf")},
        ).json()
        run_jobs(db)
        client.get(f"/api/v1/patients?q={patient.full_name}")
        client.get(f"/api/v1/query-letters/{letter['id']}")
        client.get(f"/api/v1/patients/{patient.id}/messages")
    finally:
        handler.setStream(original)
        logging.getLogger().setLevel(get_settings().log_level)

    output = captured.getvalue()
    assert "POST /api/v1/patients" in output  # the log really was captured
    for secret in (
        "Rohan",
        "Mehtani",
        "asha@example.com",
        "9876543210",
        "KHI/RC/2026/104418",
        "appendicitis",
        "Kavita",
        "Itemised final hospital bill",
    ):
        assert secret.lower() not in output.lower(), f"log output contains {secret!r}"


def test_repeated_failures_from_one_address_are_paused(client, db, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 3)
    accounts = [make_user() for _ in range(3)]
    for account in accounts:
        client.post(
            "/api/v1/auth/login", json={"email": account.email, "password": "wrong-pass-12"}
        )

    # Even a correct password is refused while the address is paused.
    response = client.post(
        "/api/v1/auth/login", json={"email": accounts[0].email, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert audit_entries(db, "auth.login_failed")[-1].details == {"reason": "address_throttled"}
