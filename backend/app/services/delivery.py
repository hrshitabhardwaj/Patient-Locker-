"""Send a ready patient message on WhatsApp and email, automatically and exactly once.

- Triggered by message preparation, in the same transaction: no one presses send.
- One notification per (letter, channel, message content). The key is unique in the
  database, so retries and re-runs never send the same content twice.
- A notification is marked SENDING before the provider is called. If the worker stops at that
  moment the outcome is unknown, and it is flagged for staff instead of being sent again.
- Temporary provider errors are retried by the job queue; permanent ones are recorded and the
  letter goes to staff.
"""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.messaging.providers import (
    ProviderPermanentError,
    ProviderTemporaryError,
    email_provider,
    whatsapp_provider,
)
from app.models import (
    Channel,
    LetterOutcome,
    MessageStatus,
    Notification,
    NotificationEvent,
    NotificationStatus,
    Patient,
    PatientMessage,
    QueryLetterStatus,
)
from app.pipeline.jobs import enqueue
from app.services.audit import AuditAction, AuditOutcome, record_audit

DISPATCH_JOB = "message.dispatch"
NOTIFICATION_FAILED = "notification_failed"
_DONE = {NotificationStatus.SENT, NotificationStatus.DELIVERED, NotificationStatus.READ}


def queue_dispatch(db: Session, message: PatientMessage) -> None:
    enqueue(db, DISPATCH_JOB, {"message_id": str(message.id)}, dedupe_key=f"dispatch:{message.id}")


def idempotency_key(letter_id: uuid.UUID, channel: Channel, content_hash: str) -> str:
    return hashlib.sha256(f"{letter_id}:{channel.value}:{content_hash}".encode()).hexdigest()


def _event(notification: Notification, status: NotificationStatus, detail: str | None) -> None:
    notification.status = status
    notification.events.append(NotificationEvent(status=status, detail=(detail or "")[:300]))


def _send(notification: Notification, message: PatientMessage) -> None:
    reference = notification.idempotency_key
    if notification.channel == Channel.WHATSAPP:
        result = whatsapp_provider().send(
            to=notification.recipient, text=message.whatsapp_text or "", reference=reference
        )
    else:
        result = email_provider().send(
            to=notification.recipient,
            subject=message.email_subject or "",
            text=message.email_text or "",
            html=message.email_html or "",
            reference=reference,
        )
    now = utcnow()
    notification.provider_message_id = result.provider_message_id
    notification.sent_at = now
    _event(notification, NotificationStatus.SENT, "Accepted by the provider.")
    if result.status == NotificationStatus.DELIVERED:
        notification.delivered_at = now
        _event(notification, NotificationStatus.DELIVERED, result.detail)


def run_dispatch(db: Session, payload: dict[str, Any]) -> None:
    message = db.get(PatientMessage, uuid.UUID(payload["message_id"]))
    if (
        message is None
        or message.superseded_at is not None
        or message.status != MessageStatus.READY
    ):
        return
    letter = message.query_letter
    # Rule 3, checked again at the last moment: never send while the letter is held.
    if letter.outcome not in (
        LetterOutcome.SEND_MISSING_ITEMS_MESSAGE,
        LetterOutcome.NO_ADDITIONAL_DOCUMENTS_REQUIRED,
    ):
        return
    patient = db.get(Patient, letter.patient_id)
    recipients = {Channel.WHATSAPP: patient.phone_e164, Channel.EMAIL: patient.email}
    providers = {Channel.WHATSAPP: whatsapp_provider().name, Channel.EMAIL: email_provider().name}
    lease = timedelta(minutes=get_settings().job_lease_minutes)
    temporary_error: Exception | None = None

    for channel in (Channel.WHATSAPP, Channel.EMAIL):
        key = idempotency_key(letter.id, channel, message.content_hash)
        notification = db.scalar(select(Notification).where(Notification.idempotency_key == key))
        if notification is not None and notification.status in _DONE | {NotificationStatus.FAILED}:
            continue
        if notification is not None and notification.status == NotificationStatus.SENDING:
            last_try = notification.events[-1].occurred_at if notification.events else None
            if last_try is None or utcnow() - last_try < lease:
                continue  # another worker may still be sending it
            notification.failed_at = utcnow()
            _event(
                notification,
                NotificationStatus.FAILED,
                "Sending was interrupted; it is unknown whether the patient received it. "
                "Check with the provider before sending again.",
            )
            db.commit()
            continue
        if notification is None:
            notification = Notification(
                message_id=message.id,
                query_letter_id=letter.id,
                patient_id=patient.id,
                channel=channel,
                provider=providers[channel],
                recipient=recipients[channel],
                status=NotificationStatus.QUEUED,
                attempts=0,
                idempotency_key=key,
            )
            db.add(notification)
            _event(notification, NotificationStatus.QUEUED, "Queued for sending.")

        notification.attempts += 1
        _event(notification, NotificationStatus.SENDING, f"Attempt {notification.attempts}.")
        db.commit()

        try:
            _send(notification, message)
            outcome = AuditOutcome.SUCCESS
        except ProviderTemporaryError as exc:
            _event(notification, NotificationStatus.QUEUED, f"Temporary error: {exc}")
            notification.last_error = str(exc)[:300]
            temporary_error = exc
            outcome = AuditOutcome.FAILURE
        except ProviderPermanentError as exc:
            notification.failed_at = utcnow()
            notification.last_error = str(exc)[:300]
            _event(notification, NotificationStatus.FAILED, str(exc))
            outcome = AuditOutcome.FAILURE
        record_audit(
            db,
            action=AuditAction.NOTIFICATION_ATTEMPTED,
            outcome=outcome,
            entity_type="notification",
            entity_id=notification.id,
            patient_id=patient.id,
            details={
                "channel": channel.value,
                "provider": notification.provider,
                "status": notification.status.value,
                "attempt": notification.attempts,
                "message_id": str(message.id),
            },
        )
        db.commit()

    refresh_letter_status(db, message)
    db.commit()
    if temporary_error is not None:
        # The job is retried with backoff; channels already sent are skipped by their key.
        raise temporary_error


def refresh_letter_status(db: Session, message: PatientMessage) -> None:
    """Mark the letter notified once every channel delivered; flag it if any failed."""
    letter = message.query_letter
    notifications = db.scalars(
        select(Notification).where(Notification.message_id == message.id)
    ).all()
    statuses = {n.channel: n.status for n in notifications}
    reasons = [r for r in letter.review_reasons if r != NOTIFICATION_FAILED]
    if any(s == NotificationStatus.FAILED for s in statuses.values()):
        reasons.append(NOTIFICATION_FAILED)
        letter.status = QueryLetterStatus.AWAITING_REVIEW
    elif len(statuses) == 2 and all(s in _DONE for s in statuses.values()):
        letter.status = QueryLetterStatus.NOTIFIED
    letter.review_reasons = reasons


_RANK = {
    NotificationStatus.QUEUED: 0,
    NotificationStatus.SENDING: 1,
    NotificationStatus.SENT: 2,
    NotificationStatus.DELIVERED: 3,
    NotificationStatus.READ: 4,
}


def apply_provider_status(
    db: Session,
    *,
    provider: str,
    provider_message_id: str,
    status: NotificationStatus | None,
    detail: str,
) -> bool:
    """Record a delivery update reported by a provider. Returns False for unknown messages.

    Updates arriving out of order never move a notification backwards (a late "sent" does
    not undo "delivered"); a failure after delivery is recorded but does not overwrite it.
    """
    notification = db.scalar(
        select(Notification).where(
            Notification.provider == provider,
            Notification.provider_message_id == provider_message_id,
        )
    )
    if notification is None:
        return False

    current = notification.status
    if status is None:
        notification.events.append(NotificationEvent(status=current, detail=detail[:300]))
    elif status == NotificationStatus.FAILED:
        if current in (NotificationStatus.DELIVERED, NotificationStatus.READ):
            notification.events.append(
                NotificationEvent(
                    status=current, detail=f"Late failure report ignored: {detail}"[:300]
                )
            )
        else:
            notification.failed_at = utcnow()
            notification.last_error = detail[:300]
            _event(notification, NotificationStatus.FAILED, detail)
    elif _RANK.get(status, 0) > _RANK.get(current, 0) and current != NotificationStatus.FAILED:
        if status in (NotificationStatus.DELIVERED, NotificationStatus.READ):
            notification.delivered_at = notification.delivered_at or utcnow()
        _event(notification, status, detail)
    else:
        notification.events.append(
            NotificationEvent(
                status=current, detail=f"Out-of-order update '{status}' ignored."[:300]
            )
        )

    record_audit(
        db,
        action=AuditAction.NOTIFICATION_STATUS_UPDATED,
        entity_type="notification",
        entity_id=notification.id,
        patient_id=notification.patient_id,
        details={
            "channel": notification.channel.value,
            "provider": provider,
            "reported": status.value if status else None,
            "status": notification.status.value,
        },
    )
    refresh_letter_status(db, db.get(PatientMessage, notification.message_id))
    db.commit()
    return True


def dispatch_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    message = db.get(PatientMessage, uuid.UUID(payload["message_id"]))
    if message is None:
        return
    for notification in db.scalars(
        select(Notification).where(Notification.message_id == message.id)
    ):
        if notification.status == NotificationStatus.QUEUED:
            notification.failed_at = utcnow()
            _event(notification, NotificationStatus.FAILED, f"Gave up after retries: {error}")
    refresh_letter_status(db, message)
