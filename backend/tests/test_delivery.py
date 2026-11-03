"""Automatic sending: one message per channel, never twice, retries, failures, rule 3."""

import pytest
from sqlalchemy import select

from app.messaging import providers
from app.models import Notification, NotificationStatus
from tests.helpers import audit_entries, run_jobs
from tests.test_company_dev_set import DEV, _set_up


def _letter(client, db, make_user, make_patient, name):
    patient = _set_up(client, db, make_user, make_patient, name)
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": (name, (DEV / name).read_bytes(), "application/pdf")},
    )
    run_jobs(db)
    return client.get(f"/api/v1/query-letters/{response.json()['id']}").json()


def test_ready_message_is_sent_on_both_channels_without_anyone_pressing_send(
    client, db, make_user, make_patient
):
    letter = _letter(client, db, make_user, make_patient, "letter_01.pdf")

    assert letter["status"] == "notified"
    message = letter["current_message"]
    channels = {n["channel"]: n for n in message["notifications"]}
    assert set(channels) == {"whatsapp", "email"}
    assert channels["whatsapp"]["recipient"].startswith("+91")
    assert channels["email"]["recipient"] == "asha@example.com"
    for notification in channels.values():
        assert notification["status"] == "delivered"
        assert notification["sent_at"] and notification["delivered_at"]
        assert notification["provider"] == "test-outbox"
        assert [e["status"] for e in notification["events"]] == [
            "queued",
            "sending",
            "sent",
            "delivered",
        ]
        assert "no real message was sent" in notification["events"][-1]["detail"]
    assert len(audit_entries(db, "notification.attempted")) == 2


def test_nothing_needed_letters_are_also_notified(client, db, make_user, make_patient):
    letter = _letter(client, db, make_user, make_patient, "letter_03.pdf")

    assert letter["status"] == "notified"
    assert letter["current_message"]["kind"] == "nothing_needed"
    assert len(letter["current_message"]["notifications"]) == 2


def test_held_letter_sends_nothing(client, db, make_user, make_patient):
    letter = _letter(client, db, make_user, make_patient, "letter_02.pdf")

    assert letter["status"] == "awaiting_review"
    assert db.scalars(select(Notification)).all() == []


def test_rerunning_never_sends_the_same_content_twice(client, db, make_user, make_patient):
    letter = _letter(client, db, make_user, make_patient, "letter_01.pdf")

    client.post(f"/api/v1/query-letters/{letter['id']}/rematch")
    run_jobs(db)

    assert len(db.scalars(select(Notification)).all()) == 2
    detail = client.get(f"/api/v1/query-letters/{letter['id']}").json()
    assert detail["status"] == "notified"


def test_temporary_provider_failure_is_retried_without_duplicating(
    client, db, make_user, make_patient, monkeypatch
):
    calls = {"n": 0}
    real_send = providers.TestOutboxEmail.send

    def flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise providers.ProviderTemporaryError("connection reset")
        return real_send(self, **kwargs)

    monkeypatch.setattr(providers.TestOutboxEmail, "send", flaky)
    letter = _letter(client, db, make_user, make_patient, "letter_01.pdf")

    email = next(n for n in letter["current_message"]["notifications"] if n["channel"] == "email")
    assert email["status"] == "queued" and email["last_error"] == "connection reset"
    assert letter["status"] == "ready_to_notify"

    from app.models import Job

    db.execute(Job.__table__.update().values(run_after=Job.created_at))
    run_jobs(db)

    notifications = db.scalars(select(Notification)).all()
    assert len(notifications) == 2  # WhatsApp was not sent again
    assert {n.status for n in notifications} == {NotificationStatus.DELIVERED}
    assert next(n for n in notifications if n.channel == "email").attempts == 2


def test_permanent_provider_failure_goes_to_staff(client, db, make_user, make_patient, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "whatsapp_provider", "twilio")
    letter = _letter(client, db, make_user, make_patient, "letter_01.pdf")

    whatsapp = next(
        n for n in letter["current_message"]["notifications"] if n["channel"] == "whatsapp"
    )
    assert whatsapp["status"] == "failed"
    assert "not configured" in whatsapp["last_error"]
    assert letter["status"] == "awaiting_review"
    assert "notification_failed" in letter["review_reasons"]


def test_profile_shows_message_history_with_delivery(client, db, make_user, make_patient):
    letter = _letter(client, db, make_user, make_patient, "letter_01.pdf")

    rows = client.get(f"/api/v1/patients/{letter['patient_id']}/messages").json()

    assert len(rows) == 1
    assert rows[0]["insurer_name"] == "Kestrelline Health Insurance Ltd."
    assert {n["status"] for n in rows[0]["message"]["notifications"]} == {"delivered"}


def test_dashboard_counts_and_review_queue(client, db, make_user, make_patient):
    _letter(client, db, make_user, make_patient, "letter_02.pdf")

    stats = client.get("/api/v1/dashboard/stats").json()
    queue = client.get("/api/v1/dashboard/letters", params={"status": "awaiting_review"}).json()

    assert stats["total_patients"] == 1
    assert stats["total_documents"] == 6
    assert stats["letters_awaiting_review"] == 1
    assert [row["patient_name"] for row in queue] == ["Neha Sarangi"]
    [patient] = client.get("/api/v1/patients").json()["items"]
    assert patient["latest_letter_status"] == "awaiting_review"


@pytest.mark.parametrize("name", ["letter_01.pdf"])
def test_message_text_is_what_was_sent(client, db, make_user, make_patient, name):
    letter = _letter(client, db, make_user, make_patient, name)

    notification = db.scalar(select(Notification).where(Notification.channel == "whatsapp"))
    assert str(notification.message_id) == letter["current_message"]["id"]
