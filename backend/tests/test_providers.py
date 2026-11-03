"""The real provider adapters, against recorded-style responses (no network).

These prove the requests we build and how we read answers and errors. They do not prove
live delivery; that is checked in Phase 12 with the company's accounts.
"""

import json

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.messaging.providers import (
    MetaWhatsApp,
    ProviderPermanentError,
    ProviderTemporaryError,
    ResendEmail,
    TwilioWhatsApp,
)
from app.models import NotificationStatus


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings()
    for name, value in {
        "twilio_account_sid": "AC123",
        "twilio_auth_token": SecretStr("tw-token"),
        "twilio_whatsapp_from": "+14155238886",
        "meta_wa_token": SecretStr("meta-token"),
        "meta_wa_phone_number_id": "1099",
        "resend_api_key": SecretStr("re_key"),
        "email_from": "Claims <claims@clinic.example>",
        "public_base_url": "https://locker.example",
    }.items():
        monkeypatch.setattr(settings, name, value)


def client_answering(status: int, body: dict | None = None, seen: list | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=body or {})

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def failing_client():
    def handler(request):
        raise httpx2.ConnectError("refused")

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_twilio_request_and_answer(configured):
    seen = []
    provider = TwilioWhatsApp(client_answering(201, {"sid": "SM1", "status": "queued"}, seen))

    result = provider.send(to="+919812345678", text="Hello", reference="ref")

    assert result.provider_message_id == "SM1"
    assert result.status == NotificationStatus.SENT
    [request] = seen
    assert request.url.path == "/2010-04-01/Accounts/AC123/Messages.json"
    form = dict(httpx2.QueryParams(request.content.decode()))
    assert form["From"] == "whatsapp:+14155238886"
    assert form["To"] == "whatsapp:+919812345678"
    assert form["StatusCallback"] == "https://locker.example/api/v1/webhooks/twilio/status"
    assert request.headers["authorization"].startswith("Basic ")


def test_meta_request_and_answer(configured):
    seen = []
    provider = MetaWhatsApp(client_answering(200, {"messages": [{"id": "wamid.X"}]}, seen))

    result = provider.send(to="+919812345678", text="Hello", reference="ref")

    assert result.provider_message_id == "wamid.X"
    [request] = seen
    assert request.url.path == "/v22.0/1099/messages"
    body = json.loads(request.content)
    assert body["to"] == "919812345678"
    assert body["text"]["body"] == "Hello"
    assert request.headers["authorization"] == "Bearer meta-token"


def test_resend_request_carries_an_idempotency_key(configured):
    seen = []
    provider = ResendEmail(client_answering(200, {"id": "em_1"}, seen))

    result = provider.send(
        to="a@b.example", subject="S", text="T", html="<p>T</p>", reference="key-1"
    )

    assert result.provider_message_id == "em_1"
    [request] = seen
    assert request.headers["idempotency-key"] == "key-1"
    assert json.loads(request.content)["to"] == ["a@b.example"]


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, ProviderTemporaryError), (503, ProviderTemporaryError), (400, ProviderPermanentError)],
)
def test_error_classes(configured, status, error):
    for provider in (
        TwilioWhatsApp(client_answering(status, {"message": "nope"})),
        MetaWhatsApp(client_answering(status, {"error": {"message": "nope"}})),
    ):
        with pytest.raises(error):
            provider.send(to="+919812345678", text="x", reference="r")
    with pytest.raises(error):
        ResendEmail(client_answering(status, {"message": "nope"})).send(
            to="a@b.example", subject="s", text="t", html="h", reference="r"
        )


def test_network_failure_is_temporary(configured):
    with pytest.raises(ProviderTemporaryError):
        TwilioWhatsApp(failing_client()).send(to="+91", text="x", reference="r")


def test_missing_credentials_never_pretend_to_send():
    for provider in (TwilioWhatsApp(failing_client()), MetaWhatsApp(failing_client())):
        with pytest.raises(ProviderPermanentError, match="not configured"):
            provider.send(to="+91", text="x", reference="r")
    with pytest.raises(ProviderPermanentError, match="not configured"):
        ResendEmail(failing_client()).send(to="a@b", subject="s", text="t", html="h", reference="r")


def test_overlong_whatsapp_is_refused_before_sending(configured):
    seen = []
    with pytest.raises(ProviderPermanentError):
        TwilioWhatsApp(client_answering(201, {"sid": "x"}, seen)).send(
            to="+91", text="x" * 5000, reference="r"
        )
    assert seen == []
