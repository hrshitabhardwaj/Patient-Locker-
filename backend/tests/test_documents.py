from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.permissions import Role
from tests.files import make_encrypted_pdf, make_image, make_pdf
from tests.helpers import audit_entries, login


@pytest.fixture
def staff_with_patient(client, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,))
    login(client, staff.email)
    return staff, patient


def upload(client, patient, data, filename="discharge.pdf", **form):
    return client.post(
        f"/api/v1/patients/{patient.id}/documents",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
    )


def test_pdf_upload_is_stored_encrypted_and_audited(client, db, staff_with_patient):
    staff, patient = staff_with_patient
    data = make_pdf(pages=3)

    response = upload(client, patient, data, category="discharge_summary")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mime_type"] == "application/pdf"
    assert body["page_count"] == 3
    assert body["declared_category"] == "discharge_summary"
    assert body["processing_status"] == "uploaded"
    assert body["uploaded_by_name"] == staff.full_name

    [stored] = list(Path(get_settings().storage_dir).rglob(body["id"].replace("-", "")))
    on_disk = stored.read_bytes()
    assert b"%PDF" not in on_disk and data not in on_disk

    [entry] = audit_entries(db, "document.uploaded")
    assert entry.patient_id == patient.id
    assert entry.details["page_count"] == 3


@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_image_uploads_are_accepted(client, staff_with_patient, fmt, mime):
    _, patient = staff_with_patient

    response = upload(client, patient, make_image(fmt), filename=f"scan.{fmt.lower()}")

    assert response.status_code == 201
    assert response.json()["mime_type"] == mime


@pytest.mark.parametrize(
    ("data", "filename", "message"),
    [
        (b"", "empty.pdf", "empty"),
        (b"hello, this is plain text", "notes.pdf", "Only PDF, JPEG and PNG"),
        (b"MZ\x90\x00 fake executable", "bill.pdf", "Only PDF, JPEG and PNG"),
        (b"%PDF-1.7\n this is not really a pdf", "broken.pdf", "damaged"),
        (make_image("PNG")[:60], "cut.png", "damaged"),
        (make_image("GIF"), "anim.gif", "Only PDF, JPEG and PNG"),
    ],
)
def test_invalid_files_are_rejected_and_audited(
    client, db, staff_with_patient, data, filename, message
):
    _, patient = staff_with_patient

    response = upload(client, patient, data, filename=filename)

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert audit_entries(db, "document.upload_rejected")[0].outcome == "failure"


def test_password_protected_pdf_is_rejected(client, staff_with_patient):
    _, patient = staff_with_patient

    response = upload(client, patient, make_encrypted_pdf())

    assert response.status_code == 422
    assert "password-protected" in response.json()["detail"]


def test_size_page_and_pixel_limits(client, staff_with_patient, monkeypatch):
    _, patient = staff_with_patient
    settings = get_settings()
    monkeypatch.setattr(settings, "max_pdf_pages", 2)
    monkeypatch.setattr(settings, "max_image_pixels", 1000)
    monkeypatch.setattr(settings, "max_upload_mb", 1)

    too_many_pages = upload(client, patient, make_pdf(pages=3))
    too_many_pixels = upload(client, patient, make_image(size=(100, 100)), filename="big.png")
    too_big = upload(client, patient, make_pdf() + b"0" * (1024 * 1024))

    assert "more than 2 pages" in too_many_pages.json()["detail"]
    assert "too large" in too_many_pixels.json()["detail"]
    assert "1 MB" in too_big.json()["detail"]


def test_duplicate_file_is_refused(client, staff_with_patient):
    _, patient = staff_with_patient
    data = make_pdf()
    upload(client, patient, data, filename="first.pdf")

    response = upload(client, patient, data, filename="again.pdf")

    assert response.status_code == 409
    assert "first.pdf" in response.json()["detail"]


def test_unsafe_filenames_are_cleaned(client, staff_with_patient):
    _, patient = staff_with_patient

    response = upload(client, patient, make_pdf(), filename="..\\..\\etc/pass<wd>|*?.exe")

    assert response.json()["original_filename"] == "passwd.exe.pdf"


def test_view_and_download_return_the_original_bytes(client, db, staff_with_patient):
    _, patient = staff_with_patient
    data = make_pdf(marker="view")
    document_id = upload(client, patient, data, filename="Bill räksmörgås.pdf").json()["id"]

    viewed = client.get(f"/api/v1/documents/{document_id}/content")
    downloaded = client.get(f"/api/v1/documents/{document_id}/content?download=true")

    assert viewed.content == data == downloaded.content
    assert viewed.headers["content-type"] == "application/pdf"
    assert viewed.headers["content-disposition"].startswith("inline;")
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "UTF-8''Bill%20r%C3%A4ksm%C3%B6rg%C3%A5s.pdf" in downloaded.headers[
        "content-disposition"
    ]
    assert viewed.headers["cache-control"] == "no-store"
    assert len(audit_entries(db, "document.viewed")) == 1
    assert len(audit_entries(db, "document.downloaded")) == 1


def test_images_are_served_in_a_sandbox(client, staff_with_patient):
    _, patient = staff_with_patient
    document_id = upload(client, patient, make_image(), filename="scan.png").json()["id"]

    response = client.get(f"/api/v1/documents/{document_id}/content")

    assert response.headers["content-security-policy"].startswith("sandbox")


def test_unassigned_staff_cannot_reach_documents(app, client, make_user, staff_with_patient):
    from fastapi.testclient import TestClient

    _, patient = staff_with_patient
    document_id = upload(client, patient, make_pdf()).json()["id"]
    outsider = TestClient(app)
    login(outsider, make_user().email)

    for path in ("", "/content", "/history"):
        assert outsider.get(f"/api/v1/documents/{document_id}{path}").status_code == 404
    assert upload(outsider, patient, make_pdf(marker="x")).status_code == 404


def test_replacing_a_document_keeps_history(client, db, staff_with_patient):
    _, patient = staff_with_patient
    first = upload(client, patient, make_pdf(marker="v1"), category="hospital_bill").json()

    second = upload(
        client,
        patient,
        make_pdf(marker="v2"),
        category="hospital_bill",
        replaces_document_id=first["id"],
    ).json()

    listed = client.get(f"/api/v1/patients/{patient.id}/documents").json()
    assert [d["id"] for d in listed] == [second["id"]]
    everything = client.get(
        f"/api/v1/patients/{patient.id}/documents", params={"include_history": True}
    ).json()
    assert {d["id"] for d in everything} == {first["id"], second["id"]}

    history = client.get(f"/api/v1/documents/{first['id']}/history").json()
    assert [v["id"] for v in history["versions"]] == [first["id"], second["id"]]
    assert [e["action"] for e in history["events"]] == ["document.uploaded"] * 2

    again = upload(
        client, patient, make_pdf(marker="v3"), replaces_document_id=first["id"]
    )
    assert again.status_code == 400


def test_only_senior_roles_delete_and_deleted_documents_disappear(
    app, client, db, make_user, staff_with_patient
):
    from fastapi.testclient import TestClient

    _, patient = staff_with_patient
    document_id = upload(client, patient, make_pdf()).json()["id"]

    assert client.delete(f"/api/v1/documents/{document_id}").status_code == 403

    manager = TestClient(app)
    login(manager, make_user(role=Role.CASE_MANAGER).email)
    assert manager.delete(f"/api/v1/documents/{document_id}").status_code == 204

    assert client.get(f"/api/v1/patients/{patient.id}/documents").json() == []
    assert client.get(f"/api/v1/documents/{document_id}/content").status_code == 404
    history = client.get(f"/api/v1/documents/{document_id}/history").json()
    assert history["versions"][0]["deleted_at"] is not None
    assert history["events"][-1]["action"] == "document.deleted"


def test_category_can_be_corrected(client, db, staff_with_patient):
    _, patient = staff_with_patient
    document_id = upload(client, patient, make_pdf(), category="other").json()["id"]

    response = client.patch(
        f"/api/v1/documents/{document_id}", json={"declared_category": "pharmacy_bill"}
    )

    assert response.json()["declared_category"] == "pharmacy_bill"
    [entry] = audit_entries(db, "document.updated")
    assert entry.details == {"category_from": "other", "category_to": "pharmacy_bill"}


def test_patient_list_counts_current_documents(client, staff_with_patient):
    _, patient = staff_with_patient
    first = upload(client, patient, make_pdf(marker="a")).json()
    upload(client, patient, make_pdf(marker="b"), replaces_document_id=first["id"])
    upload(client, patient, make_image(), filename="card.png")

    [item] = client.get("/api/v1/patients").json()["items"]

    assert item["document_count"] == 2


def test_oversized_request_is_refused_before_reading(client, staff_with_patient):
    _, patient = staff_with_patient
    limit = get_settings().max_upload_bytes

    response = client.post(
        f"/api/v1/patients/{patient.id}/documents",
        content=b"x",
        headers={"content-length": str(limit + 2 * 1024 * 1024), "content-type": "text/plain"},
    )

    assert response.status_code == 413
