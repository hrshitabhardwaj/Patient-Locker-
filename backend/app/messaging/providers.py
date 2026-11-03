"""Delivery providers for WhatsApp and email, chosen in .env.

- test outbox ("fake"): records the message and reports it delivered; nothing leaves the
  machine.
- Twilio WhatsApp, Meta WhatsApp Cloud API, Resend email: real adapters. They are tested
  against recorded provider responses only; live delivery is confirmed in Phase 12 with the
  company's accounts. Missing credentials fail loudly rather than pretending to send.

Error handling: 429 and 5xx (and network failures) mean the provider did not accept the
message, so it is safe to retry (ProviderTemporaryError). Other 4xx mean it was refused and
retrying will not help (ProviderPermanentError).
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx2

from app.core.config import get_settings
from app.models import NotificationStatus


class ProviderTemporaryError(Exception):
    """Provider unreachable or rate-limited; the message was not accepted. Safe to retry."""


class ProviderPermanentError(Exception):
    """The provider refused the message (bad number, not configured...). Retrying will not help."""


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    status: NotificationStatus  # SENT, or DELIVERED when the provider confirms at once
    detail: str


class WhatsAppProvider(Protocol):
    name: str

    def send(self, *, to: str, text: str, reference: str) -> SendResult: ...


class EmailProvider(Protocol):
    name: str

    def send(
        self, *, to: str, subject: str, text: str, html: str, reference: str
    ) -> SendResult: ...


TEST_OUTBOX_NOTE = "Test provider: recorded in the local outbox; no real message was sent."
# WhatsApp's per-message text limit (Twilio and Meta).
WHATSAPP_MAX_CHARS = 4096


def _check_response(response: httpx2.Response, provider: str) -> dict:
    if response.status_code == 429 or response.status_code >= 500:
        raise ProviderTemporaryError(f"{provider} returned {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        reason = body.get("message") or body.get("error", {}).get("message") or response.text[:200]
        raise ProviderPermanentError(
            f"{provider} refused the message ({response.status_code}): {reason}"
        )
    return body


def _post(client: httpx2.Client, provider: str, url: str, **kwargs) -> dict:
    try:
        response = client.post(url, **kwargs)
    except httpx2.TransportError as exc:
        raise ProviderTemporaryError(f"{provider} not reachable: {type(exc).__name__}") from None
    return _check_response(response, provider)


def _client() -> httpx2.Client:
    return httpx2.Client(timeout=get_settings().provider_timeout_seconds)


# ------------------------------------------------------------------ test outbox


class TestOutboxWhatsApp:
    name = "test-outbox"

    def send(self, *, to: str, text: str, reference: str) -> SendResult:
        return SendResult(
            f"wa-test-{uuid.uuid4().hex[:16]}", NotificationStatus.DELIVERED, TEST_OUTBOX_NOTE
        )


class TestOutboxEmail:
    name = "test-outbox"

    def send(self, *, to: str, subject: str, text: str, html: str, reference: str) -> SendResult:
        return SendResult(
            f"em-test-{uuid.uuid4().hex[:16]}", NotificationStatus.DELIVERED, TEST_OUTBOX_NOTE
        )


# ------------------------------------------------------------------ real providers


class TwilioWhatsApp:
    """Twilio Messaging API, WhatsApp channel. Delivery updates arrive at the status webhook.

    Free-form text works in the Twilio sandbox and inside WhatsApp's 24-hour reply window;
    messages that start a conversation need an approved template (Phase 12).
    """

    name = "twilio"

    def __init__(self, client: httpx2.Client | None = None):
        settings = get_settings()
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token.get_secret_value()
        self.sender = settings.twilio_whatsapp_from
        self.callback = f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/twilio/status"
        self.client = client or _client()

    def send(self, *, to: str, text: str, reference: str) -> SendResult:
        if not (self.account_sid and self.auth_token and self.sender):
            raise ProviderPermanentError("Twilio is not configured (account SID, token, sender)")
        if len(text) > WHATSAPP_MAX_CHARS:
            raise ProviderPermanentError("Message is longer than WhatsApp allows")
        body = _post(
            self.client,
            "Twilio",
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            auth=(self.account_sid, self.auth_token),
            data={
                "From": f"whatsapp:{self.sender}",
                "To": f"whatsapp:{to}",
                "Body": text,
                "StatusCallback": self.callback,
            },
        )
        sid = body.get("sid")
        if not sid:
            raise ProviderTemporaryError("Twilio did not return a message id")
        return SendResult(
            sid, NotificationStatus.SENT, f"Accepted by Twilio ({body.get('status')})."
        )


class MetaWhatsApp:
    """Meta WhatsApp Cloud API. Delivery updates arrive at the Meta webhook.

    Free-form text is only delivered inside the 24-hour customer window; starting a
    conversation needs an approved template (Phase 12).
    """

    name = "meta"

    def __init__(self, client: httpx2.Client | None = None):
        settings = get_settings()
        self.token = settings.meta_wa_token.get_secret_value()
        self.phone_number_id = settings.meta_wa_phone_number_id
        self.version = settings.meta_graph_version
        self.client = client or _client()

    def send(self, *, to: str, text: str, reference: str) -> SendResult:
        if not (self.token and self.phone_number_id):
            raise ProviderPermanentError("Meta WhatsApp is not configured (token, phone number id)")
        if len(text) > WHATSAPP_MAX_CHARS:
            raise ProviderPermanentError("Message is longer than WhatsApp allows")
        body = _post(
            self.client,
            "Meta",
            f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to.lstrip("+"),
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )
        messages = body.get("messages") or []
        if not messages or not messages[0].get("id"):
            raise ProviderTemporaryError("Meta did not return a message id")
        return SendResult(messages[0]["id"], NotificationStatus.SENT, "Accepted by Meta.")


class ResendEmail:
    """Resend email API. The idempotency key makes Resend itself drop an accidental repeat."""

    name = "resend"

    def __init__(self, client: httpx2.Client | None = None):
        settings = get_settings()
        self.api_key = settings.resend_api_key.get_secret_value()
        self.sender = settings.email_from
        self.client = client or _client()

    def send(self, *, to: str, subject: str, text: str, html: str, reference: str) -> SendResult:
        if not (self.api_key and self.sender):
            raise ProviderPermanentError("Resend is not configured (API key, sender address)")
        body = _post(
            self.client,
            "Resend",
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {self.api_key}", "Idempotency-Key": reference},
            json={"from": self.sender, "to": [to], "subject": subject, "text": text, "html": html},
        )
        if not body.get("id"):
            raise ProviderTemporaryError("Resend did not return a message id")
        return SendResult(body["id"], NotificationStatus.SENT, "Accepted by Resend.")


def whatsapp_provider() -> WhatsAppProvider:
    choice = get_settings().whatsapp_provider
    if choice == "twilio":
        return TwilioWhatsApp()
    if choice == "meta":
        return MetaWhatsApp()
    return TestOutboxWhatsApp()


def email_provider() -> EmailProvider:
    return ResendEmail() if get_settings().email_provider == "resend" else TestOutboxEmail()
