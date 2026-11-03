"""Delivery updates from the messaging providers.

These routes need no staff session (the providers call them), so each one verifies the
provider's signature before trusting anything in the request. Payloads are never logged:
they contain phone numbers and email addresses.
"""

import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.db.session import DbSession
from app.models import NotificationStatus
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.delivery import apply_provider_status

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Resend (Svix) signatures older than this are rejected, so captured requests cannot be replayed.
_MAX_AGE_SECONDS = 5 * 60

_TWILIO_STATUS = {
    "sent": NotificationStatus.SENT,
    "delivered": NotificationStatus.DELIVERED,
    "read": NotificationStatus.READ,
    "failed": NotificationStatus.FAILED,
    "undelivered": NotificationStatus.FAILED,
}
_META_STATUS = {
    "sent": NotificationStatus.SENT,
    "delivered": NotificationStatus.DELIVERED,
    "read": NotificationStatus.READ,
    "failed": NotificationStatus.FAILED,
}
# None: recorded as a note without changing the status.
_RESEND_EVENT = {
    "email.sent": NotificationStatus.SENT,
    "email.delivered": NotificationStatus.DELIVERED,
    "email.opened": NotificationStatus.READ,
    "email.bounced": NotificationStatus.FAILED,
    "email.failed": NotificationStatus.FAILED,
    "email.delivery_delayed": None,
    "email.complained": None,
}


def _reject(db: DbSession, request: Request, provider: str, reason: str) -> HTTPException:
    record_audit(
        db,
        action=AuditAction.WEBHOOK_REJECTED,
        outcome=AuditOutcome.DENIED,
        request=request,
        details={"provider": provider, "reason": reason},
    )
    db.commit()
    return HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature.")


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's scheme: HMAC-SHA1 over the URL followed by each parameter name and value, sorted."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


@router.post("/twilio/status")
async def twilio_status(request: Request, db: DbSession):
    settings = get_settings()
    token = settings.twilio_auth_token.get_secret_value()
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    # Twilio signs the public URL it called, which differs from ours behind a proxy.
    url = f"{settings.public_base_url.rstrip('/')}{request.url.path}"
    given = request.headers.get("X-Twilio-Signature", "")
    if not token or not hmac.compare_digest(twilio_signature(token, url, params), given):
        raise _reject(db, request, "twilio", "bad_signature")

    reported = params.get("MessageStatus", "")
    if reported in _TWILIO_STATUS:
        error = params.get("ErrorCode")
        detail = f"Twilio reported {reported}" + (f" (error {error})" if error else "") + "."
        apply_provider_status(
            db,
            provider="twilio",
            provider_message_id=params.get("MessageSid", ""),
            status=_TWILIO_STATUS[reported],
            detail=detail,
        )
    return {"ok": True}


@router.get("/meta/whatsapp")
def meta_verify(request: Request):
    """Meta's subscription check: echo the challenge if the verify token matches."""
    expected = get_settings().meta_verify_token.get_secret_value()
    query = request.query_params
    if (
        expected
        and query.get("hub.mode") == "subscribe"
        and hmac.compare_digest(query.get("hub.verify_token", ""), expected)
    ):
        return PlainTextResponse(query.get("hub.challenge", ""))
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed.")


@router.post("/meta/whatsapp")
async def meta_events(request: Request, db: DbSession):
    secret = get_settings().meta_app_secret.get_secret_value()
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(
        expected, request.headers.get("X-Hub-Signature-256", "")
    ):
        raise _reject(db, request, "meta", "bad_signature")

    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for update in change.get("value", {}).get("statuses", []):
                reported = update.get("status", "")
                if reported not in _META_STATUS:
                    continue
                errors = update.get("errors") or []
                reason = f" ({errors[0].get('title') or errors[0].get('code')})" if errors else ""
                apply_provider_status(
                    db,
                    provider="meta",
                    provider_message_id=update.get("id", ""),
                    status=_META_STATUS[reported],
                    detail=f"Meta reported {reported}{reason}.",
                )
    return {"ok": True}


def svix_signature(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{message_id}.{timestamp}.".encode() + body
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


@router.post("/resend")
async def resend_events(request: Request, db: DbSession):
    secret = get_settings().resend_webhook_secret.get_secret_value()
    body = await request.body()
    message_id = request.headers.get("svix-id", "")
    timestamp = request.headers.get("svix-timestamp", "")
    given = [part.split(",", 1)[-1] for part in request.headers.get("svix-signature", "").split()]
    try:
        fresh = abs(time.time() - int(timestamp)) <= _MAX_AGE_SECONDS
    except ValueError:
        fresh = False
    if not (secret and fresh):
        raise _reject(db, request, "resend", "stale_or_unconfigured")
    expected = svix_signature(secret, message_id, timestamp, body)
    if not any(hmac.compare_digest(expected, signature) for signature in given):
        raise _reject(db, request, "resend", "bad_signature")

    payload = await request.json()
    event = payload.get("type", "")
    if event in _RESEND_EVENT:
        email_id = (payload.get("data") or {}).get("email_id", "")
        apply_provider_status(
            db,
            provider="resend",
            provider_message_id=email_id,
            status=_RESEND_EVENT[event],
            detail=f"Resend reported {event.removeprefix('email.')}.",
        )
    return Response(status_code=status.HTTP_200_OK)
