"""Delivery updates from providers: signatures, replay protection, ordering, failures."""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.api.v1.webhooks import svix_signature, twilio_signature
from app.core.config import get_settings
from app.messaging import providers
from app.models import Notification, NotificationStatus
from tests.helpers import audit_entries, run_jobs
from tests.test_company_dev_set import DEV, _set_up

TWILIO_URL = "https://locker.example/api/v1/webhooks/twilio/status"
RESEND_SECRET = "whsec_" + base64.b64encode(b"resend-test-secret").decode()


@pytest.fixture
def secrets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "https://locker.example")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("tw-token"))
    monkeypatch.setattr(settings, "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(settings, "meta_verify_token", SecretStr("verify-me"))
    monkeypatch.setattr(settings, "resend_webhook_secret", SecretStr(RESEND_SECRET))


@pytest.fixture
def sent_letter(client, db, make_user, make_patient, monkeypatch):
    """Letter 01 sent through 'twilio' and 'resend' adapters that answer without the network."""
    settings = get_settings()
    monkeypatch.setattr(settings, "whatsapp_provider", "twilio")
    monkeypatch.setattr(settings, "email_provider", "resend")
    monkeypatch.setattr(
        providers.TwilioWhatsApp,
        "send",
        lambda self, **kw: providers.SendResult("SM-1", NotificationStatus.SENT, "Accepted."),
    )
    monkeypatch.setattr(
        providers.ResendEmail,
        "send",
        lambda self, **kw: providers.SendResult("em-1", NotificationStatus.SENT, "Accepted."),
    )
    patient = _set_up(client, db, make_user, make_patient, "letter_01.pdf")
    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": ("l.pdf", (DEV / "letter_01.pdf").read_bytes(), "application/pdf")},
    )
    run_jobs(db)
    return response.json()["id"]


def _notification(db, channel):
    return db.scalar(select(Notification).where(Notification.channel == channel))


def post_twilio(client, params, token="tw-token"):
    return client.post(
        "/api/v1/webhooks/twilio/status",
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": twilio_signature(token, TWILIO_URL, params),
        },
    )


def post_resend(client, payload, *, secret=RESEND_SECRET, timestamp=None):
    body = json.dumps(payload).encode()
    timestamp = str(timestamp or int(time.time()))
    signature = svix_signature(secret, "msg_1", timestamp, body)
    return client.post(
        "/api/v1/webhooks/resend",
        content=body,
        headers={
            "Content-Type": "application/json",
            "svix-id": "msg_1",
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        },
    )


def post_meta(client, payload, secret="meta-secret"):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/webhooks/meta/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )


def test_twilio_delivery_update(client, db, secrets, sent_letter):
    assert _notification(db, "whatsapp").status == NotificationStatus.SENT

    response = post_twilio(client, {"MessageSid": "SM-1", "MessageStatus": "delivered"})

    assert response.status_code == 200
    whatsapp = _notification(db, "whatsapp")
    db.refresh(whatsapp)
    assert whatsapp.status == NotificationStatus.DELIVERED
    assert whatsapp.delivered_at is not None
    assert audit_entries(db, "notification.status_updated")[0].details["status"] == "delivered"


def test_out_of_order_update_does_not_go_backwards(client, db, secrets, sent_letter):
    post_twilio(client, {"MessageSid": "SM-1", "MessageStatus": "read"})
    post_twilio(client, {"MessageSid": "SM-1", "MessageStatus": "sent"})

    whatsapp = _notification(db, "whatsapp")
    db.refresh(whatsapp)
    assert whatsapp.status == NotificationStatus.READ
    assert "ignored" in whatsapp.events[-1].detail


def test_undelivered_whatsapp_sends_the_letter_to_staff(client, db, secrets, sent_letter):
    post_twilio(
        client, {"MessageSid": "SM-1", "MessageStatus": "undelivered", "ErrorCode": "63016"}
    )

    letter = client.get(f"/api/v1/query-letters/{sent_letter}").json()
    assert letter["status"] == "awaiting_review"
    assert "notification_failed" in letter["review_reasons"]
    whatsapp = next(
        n for n in letter["current_message"]["notifications"] if n["channel"] == "whatsapp"
    )
    assert whatsapp["status"] == "failed" and "63016" in whatsapp["last_error"]


def test_resend_bounce_and_delivery(client, db, secrets, sent_letter):
    delivered = post_resend(client, {"type": "email.delivered", "data": {"email_id": "em-1"}})

    assert delivered.status_code == 200
    email = _notification(db, "email")
    db.refresh(email)
    assert email.status == NotificationStatus.DELIVERED


def test_meta_status_update(client, db, secrets, sent_letter, monkeypatch):
    # Pretend the WhatsApp notification went through Meta.
    whatsapp = _notification(db, "whatsapp")
    whatsapp.provider, whatsapp.provider_message_id = "meta", "wamid.1"
    db.commit()

    payload = {
        "entry": [
            {"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "delivered"}]}}]}
        ]
    }
    assert post_meta(client, payload).status_code == 200

    db.refresh(whatsapp)
    assert whatsapp.status == NotificationStatus.DELIVERED


def test_meta_subscription_check(client, secrets):
    ok = client.get(
        "/api/v1/webhooks/meta/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"},
    )
    wrong = client.get(
        "/api/v1/webhooks/meta/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )

    assert (ok.status_code, ok.text) == (200, "42")
    assert wrong.status_code == 403


def test_every_webhook_refuses_unsigned_or_forged_requests(client, db, secrets, sent_letter):
    forged = [
        post_twilio(client, {"MessageSid": "SM-1", "MessageStatus": "failed"}, token="wrong"),
        client.post("/api/v1/webhooks/twilio/status", data={"MessageSid": "SM-1"}),
        post_meta(client, {"entry": []}, secret="wrong"),
        client.post("/api/v1/webhooks/meta/whatsapp", json={"entry": []}),
        post_resend(
            client,
            {"type": "email.bounced", "data": {"email_id": "em-1"}},
            secret="whsec_" + base64.b64encode(b"other").decode(),
        ),
        client.post("/api/v1/webhooks/resend", json={"type": "email.bounced"}),
    ]

    assert [r.status_code for r in forged] == [403] * 6
    # Nothing changed, and every attempt is in the audit log.
    assert _notification(db, "whatsapp").status == NotificationStatus.SENT
    assert _notification(db, "email").status == NotificationStatus.SENT
    assert len(audit_entries(db, "webhook.rejected")) == 6


def test_replayed_resend_event_is_refused(client, db, secrets, sent_letter):
    old = int(time.time()) - 3600

    response = post_resend(
        client, {"type": "email.bounced", "data": {"email_id": "em-1"}}, timestamp=old
    )

    assert response.status_code == 403


def test_webhooks_refuse_everything_when_not_configured(client, db):
    assert client.post("/api/v1/webhooks/twilio/status", data={}).status_code == 403
    assert (
        client.get("/api/v1/webhooks/meta/whatsapp", params={"hub.mode": "subscribe"}).status_code
        == 403
    )
