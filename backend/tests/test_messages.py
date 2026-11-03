"""Patient messages: template adherence, usage rules, and the pipeline around them."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.messaging import templates
from app.messaging.items import MissingItem, format_missing_items, is_safe
from app.messaging.render import render
from app.models import MessageKind, QueryLetter, RequirementType
from app.services import messages
from tests.files import make_pdf, make_text_pdf
from tests.helpers import audit_entries, login, run_jobs
from tests.test_company_dev_set import DEV, _set_up

LETTERS = Path(__file__).parent / "fixtures" / "query_letters"


@pytest.fixture
def on_25_september(monkeypatch):
    """Messages are prepared the day after letter 01 was written."""
    monkeypatch.setattr(messages, "utcnow", lambda: datetime(2026, 9, 25, 10, tzinfo=UTC))


def _dev_letter(client, db, make_user, make_patient, name):
    patient = _set_up(client, db, make_user, make_patient, name)
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": (name, (DEV / name).read_bytes(), "application/pdf")},
    )
    run_jobs(db)
    return client.get(f"/api/v1/query-letters/{response.json()['id']}").json()


# ------------------------------------------------------------------ golden messages


def test_documents_needed_message_follows_the_template_exactly(
    client, db, make_user, make_patient, on_25_september
):
    letter = _dev_letter(client, db, make_user, make_patient, "letter_01.pdf")
    message = letter["current_message"]

    assert letter["status"] == "notified"  # prepared, then sent automatically
    assert message["status"] == "ready"
    assert message["kind"] == "documents_needed"
    assert message["whatsapp_text"] == (
        "Dear Rohan Mehtani,\n\n"
        "We reviewed the query from Kestrelline Health Insurance Ltd. for claim "
        "KHI/RC/2026/104418. Please share:\n\n"
        "1. Itemised final hospital bill: the insurer needs the breakup of room, operation "
        "theatre and consumable charges.\n"
        "2. Date on which your symptoms first started: the insurer needs this to review your "
        "claim.\n\n"
        "Please send these by 9 October 2026. Reply here or email claims@northfield.example.\n\n"
        "You don't need to resend any other documents.\n\n"
        "Regards,\nNorthfield Claims Desk"
    )
    assert message["email_subject"] == "Action needed: documents for claim KHI/RC/2026/104418"
    assert message["email_text"].startswith(
        "Dear Rohan Mehtani,\n\nWe reviewed the query received from Kestrelline Health "
        "Insurance Ltd. for claim KHI/RC/2026/104418. Your claim needs the items below before "
        "it can move forward.\n\n1. Itemised final hospital bill"
    )
    assert message["email_text"].endswith(
        "You can reply to this email or contact us at 1800-000-0141.\n\n"
        "You don't need to resend any other documents.\n\n"
        "Regards,\nNorthfield Claims Desk Claims Team"
    )
    assert message["values"]["due_date_source"] == "insurer_deadline"
    # Only the two NEEDS_FROM_PATIENT points; the covered summary and USG are not mentioned.
    assert len(message["point_ids"]) == 2
    assert "Discharge summary" not in message["whatsapp_text"]


def test_nothing_needed_message_when_everything_is_covered(client, db, make_user, make_patient):
    letter = _dev_letter(client, db, make_user, make_patient, "letter_03.pdf")
    message = letter["current_message"]

    assert message["kind"] == "nothing_needed"
    assert message["email_subject"] == "No documents needed for claim FT/KHI/2026/44172"
    assert (
        message["whatsapp_text"]
        == message["email_text"]
        == (
            "Dear Arjun Bhatnagar,\n\n"
            "We reviewed the query from Fernhill TPA Services Pvt. Ltd. for claim "
            "FT/KHI/2026/44172. "
            "We already have the required documents, so you don't need to send anything more right "
            "now.\n\n"
            "We'll contact you if anything else is needed. This message doesn't confirm claim "
            "approval.\n\n"
            "Regards,\nNorthfield Claims Desk"
        )
    )


def test_no_message_while_a_point_needs_review(client, db, make_user, make_patient):
    letter = _dev_letter(client, db, make_user, make_patient, "letter_02.pdf")

    assert letter["outcome"] == "hold_for_staff_review"
    assert letter["current_message"] is None


# ------------------------------------------------------------------ usage rules


def test_missing_template_value_blocks_and_sends_to_staff(
    client, db, make_user, make_patient, monkeypatch
):
    monkeypatch.setattr(get_settings(), "company_name", "")
    letter = _dev_letter(client, db, make_user, make_patient, "letter_03.pdf")

    message = letter["current_message"]
    assert message["status"] == "blocked"
    assert message["blocked_reasons"] == ["missing:company_name"]
    assert message["whatsapp_text"] is None
    assert letter["status"] == "awaiting_review"
    assert "message_values_missing" in letter["review_reasons"]

    # Once the value exists, matching again prepares the message and clears the flag.
    monkeypatch.setattr(get_settings(), "company_name", "Northfield Claims Desk")
    client.post(f"/api/v1/query-letters/{letter['id']}/rematch")
    run_jobs(db)
    fixed = client.get(f"/api/v1/query-letters/{letter['id']}").json()
    assert fixed["current_message"]["status"] == "ready"
    assert "message_values_missing" not in fixed["review_reasons"]
    assert len(client.get(f"/api/v1/query-letters/{letter['id']}/messages").json()) == 2


def test_staff_can_supply_a_missing_insurer_name(client, db, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,), full_name="Aarav Mehta")
    login(client, staff.email)
    letter_text = "Dear Sir,\n\nPlease submit the discharge summary for the above claim.\n"
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": ("letter.pdf", make_text_pdf(letter_text), "application/pdf")},
    )
    letter_id = response.json()["id"]
    run_jobs(db)
    blocked = client.get(f"/api/v1/query-letters/{letter_id}").json()["current_message"]
    assert set(blocked["blocked_reasons"]) == {"missing:insurer_name", "missing:claim_number"}

    client.patch(
        f"/api/v1/query-letters/{letter_id}",
        json={"insurer_name": "Northfield General Insurance", "claim_reference": "CLM-77"},
    )
    run_jobs(db)

    message = client.get(f"/api/v1/query-letters/{letter_id}").json()["current_message"]
    assert message["status"] == "ready"
    assert "Northfield General Insurance for claim CLM-77" in message["whatsapp_text"]
    assert audit_entries(db, "query_letter.updated")[0].details == {
        "changed": ["claim_reference", "insurer_name"]
    }


def test_message_is_withdrawn_when_the_letter_goes_back_to_review(
    client, db, make_user, make_patient
):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,), full_name="Aarav Mehta", claim_number="CLM-1")
    login(client, staff.email)
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={
            "file": (
                "l.pdf",
                make_text_pdf((LETTERS / "05_identity_mismatch.txt").read_text()),
                "application/pdf",
            )
        },
    )
    letter_id = response.json()["id"]
    run_jobs(db)
    client.post(
        f"/api/v1/query-letters/{letter_id}/confirm", json={"note": "Checked with insurer."}
    )
    run_jobs(db)
    assert client.get(f"/api/v1/query-letters/{letter_id}").json()["current_message"]

    # An unreadable upload might be the missing summary: the letter must go back to review.
    client.post(
        f"/api/v1/patients/{patient.id}/documents",
        files={"file": ("scan.pdf", make_pdf(), "application/pdf")},
    )
    run_jobs(db)
    client.post(f"/api/v1/query-letters/{letter_id}/rematch")
    run_jobs(db)

    letter = client.get(f"/api/v1/query-letters/{letter_id}").json()
    assert letter["outcome"] == "hold_for_staff_review"
    assert letter["current_message"] is None


def test_preparing_twice_does_not_create_a_duplicate(
    client, db, make_user, make_patient, on_25_september
):
    letter = _dev_letter(client, db, make_user, make_patient, "letter_01.pdf")

    messages.queue_compose(db, db.get(QueryLetter, letter["id"]))
    db.commit()
    run_jobs(db)

    assert len(client.get(f"/api/v1/query-letters/{letter['id']}/messages").json()) == 1
    assert len(audit_entries(db, "message.prepared")) == 1


# ------------------------------------------------------------------ item wording


def _item(requirement, kind, source, conditions=()):
    return MissingItem(requirement, kind, list(conditions), source)


def test_medical_details_never_reach_the_message():
    items = [
        _item("NS1 antigen report", RequirementType.INVESTIGATION_REPORT, "the NS1 antigen report"),
        _item(
            "Clarify whether the patient had any history of diabetes prior to admission",
            RequirementType.CLARIFICATION,
            "Please clarify whether the patient had any history of diabetes prior to admission.",
        ),
        _item(
            "Ultrasound report",
            RequirementType.INVESTIGATION_REPORT,
            "Pre-operative ultrasound report of the abdomen.",
        ),
    ]

    text = format_missing_items(items, letter_date="14/10/2026")

    assert text == (
        "1. Test report requested in the insurer's letter dated 14/10/2026: the insurer needs "
        "this to review your claim.\n"
        "2. Written explanation requested in the insurer's letter dated 14/10/2026: the insurer "
        "needs this to review your claim.\n"
        "3. Test report requested in the insurer's letter dated 14/10/2026: the insurer needs "
        "this to review your claim."
    )
    for word in ("NS1", "antigen", "diabetes", "ultrasound", "abdomen"):
        assert word.lower() not in text.lower()


def test_signature_and_stamp_reason_and_written_confirmation():
    items = [
        _item(
            "Discharge summary — signed by the treating doctor; with hospital stamp or seal",
            RequirementType.DISCHARGE_SUMMARY,
            "Discharge summary signed and stamped by the treating doctor.",
            [{"code": "signed_by_doctor"}, {"code": "stamped"}],
        ),
        _item(
            "Confirm in writing whether you hold any other health insurance policy that covers "
            "this hospitalisation",
            RequirementType.CLARIFICATION,
            "You are also requested to confirm in writing whether you hold any other health "
            "insurance policy that covers this hospitalisation.",
        ),
    ]

    lines = format_missing_items(items, letter_date=None).splitlines()

    assert lines[0] == (
        "1. Discharge summary: the insurer needs it signed by the treating doctor and stamped by "
        "the hospital."
    )
    assert lines[1] == (
        "2. Written confirmation of whether you hold any other health insurance policy that "
        "covers this hospitalisation: the insurer needs this in writing to review your claim."
    )


def test_letter_wide_note_is_added_when_safe():
    item = _item("Indoor case papers", RequirementType.OTHER, "Indoor case papers")

    text = format_missing_items(
        [item], letter_date=None, notes=["Note: All documents should be attested by the hospital."]
    )

    assert text.endswith("Please note: all documents should be attested by the hospital.")


def test_allowlist():
    assert is_safe("Itemised final hospital bill")
    assert not is_safe("Report showing dengue")


def test_values_are_inserted_literally_and_escaped_in_html():
    rendered = render(
        MessageKind.NOTHING_NEEDED,
        {
            "patient_name": "Asha <b>{claim_number}</b>",
            "insurer_name": "Insurer & Co",
            "claim_number": "C-1",
            "company_name": "Desk",
        },
    )

    assert rendered.whatsapp_text.startswith("Dear Asha <b>{claim_number}</b>,")
    assert "Asha &lt;b&gt;{claim_number}&lt;/b&gt;" in rendered.email_html
    assert "Insurer &amp; Co" in rendered.email_html


def test_every_template_placeholder_is_known():
    import re

    for template, fields in (
        (templates.WHATSAPP_DOCUMENTS_NEEDED, templates.DOCUMENTS_NEEDED_FIELDS),
        (templates.EMAIL_DOCUMENTS_NEEDED, templates.DOCUMENTS_NEEDED_FIELDS),
        (templates.NOTHING_NEEDED, templates.NOTHING_NEEDED_FIELDS),
    ):
        assert set(re.findall(r"\{([a-z_]+)\}", template)) <= set(fields)
