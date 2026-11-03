"""Prepare the patient message for a letter whose outcome is settled.

Runs after matching and after every staff decision. Usage rules from the templates:
1. Only template placeholders are filled, from stored data; a missing value blocks the
   message and sends the letter to staff.
2. Only NEEDS_FROM_PATIENT items are listed, each with a one-line reason.
3. No message while the letter has a NEEDS_REVIEW point (the outcome is then "hold").
4. No medical details (enforced by the item wording allowlist).
"""

import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.matching.engine import parse_date
from app.messaging.items import MissingItem, format_missing_items
from app.messaging.render import render
from app.messaging.templates import TEMPLATE_VERSION
from app.models import (
    LetterOutcome,
    MatchStatus,
    MessageKind,
    MessageStatus,
    Patient,
    PatientMessage,
    QueryLetter,
    QueryLetterStatus,
    User,
)
from app.pipeline.jobs import enqueue
from app.services.audit import AuditAction, record_audit

COMPOSE_JOB = "query_letter.compose"
MESSAGE_VALUES_MISSING = "message_values_missing"

_KIND_FOR_OUTCOME = {
    LetterOutcome.SEND_MISSING_ITEMS_MESSAGE: MessageKind.DOCUMENTS_NEEDED,
    LetterOutcome.NO_ADDITIONAL_DOCUMENTS_REQUIRED: MessageKind.NOTHING_NEEDED,
}
_STATUS_FOR_KIND = {
    MessageKind.DOCUMENTS_NEEDED: QueryLetterStatus.READY_TO_NOTIFY,
    MessageKind.NOTHING_NEEDED: QueryLetterStatus.NO_ACTION_REQUIRED,
}


def queue_compose(db: Session, letter: QueryLetter) -> None:
    """Queue (re)preparing the message, in the caller's transaction."""
    enqueue(db, COMPOSE_JOB, {"query_letter_id": str(letter.id)}, dedupe_key=f"compose:{letter.id}")


def _format_date(value: date) -> str:
    return f"{value.day} {value:%B %Y}"


def due_date(letter: QueryLetter, today: date) -> tuple[str | None, str]:
    """The reply-by date and where it came from."""
    window = get_settings().patient_response_days
    if letter.response_days:
        start = parse_date(letter.letter_date or "") or letter.created_at.date()
        insurer_due = start + timedelta(days=letter.response_days)
        if insurer_due >= today:
            return _format_date(insurer_due), "insurer_deadline"
        # The insurer's deadline has already passed: ask promptly using the company window.
        if window > 0:
            return _format_date(today + timedelta(days=window)), "company_window_deadline_passed"
        return None, "insurer_deadline_passed"
    if window > 0:
        return _format_date(today + timedelta(days=window)), "company_window"
    return None, "no_deadline"


def build_values(
    letter: QueryLetter, patient: Patient, kind: MessageKind, today: date
) -> tuple[dict[str, Any], list[str]]:
    settings = get_settings()
    point_ids = []
    items = []
    for point in letter.points:
        match = point.current_match
        if point.dismissed_at is None and match and match.status == MatchStatus.NEEDS_FROM_PATIENT:
            point_ids.append(str(point.id))
            items.append(
                MissingItem(
                    point.requirement, point.requirement_type, point.conditions, point.source_text
                )
            )
    notes = [n["text"] for n in letter.extraction_details.get("general_conditions", [])]
    due, due_source = due_date(letter, today)
    values = {
        "patient_name": patient.full_name,
        "insurer_name": letter.insurer_name,
        "claim_number": letter.claim_reference or patient.claim_number,
        "missing_items": (
            format_missing_items(items, letter_date=letter.letter_date, notes=notes)
            if kind == MessageKind.DOCUMENTS_NEEDED
            else None
        ),
        "due_date": due,
        "due_date_source": due_source,
        "support_email": settings.support_email,
        "support_phone": settings.support_phone,
        "company_name": settings.company_name,
    }
    return values, point_ids


def run_compose(db: Session, payload: dict[str, Any]) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is None:
        return
    current = letter.current_message
    kind = _KIND_FOR_OUTCOME.get(letter.outcome) if letter.outcome else None

    if kind is None:
        # Held for review (or not matched yet): any prepared message is withdrawn.
        if current is not None:
            current.superseded_at = utcnow()
            db.commit()
        return

    patient = db.get(Patient, letter.patient_id)
    values, point_ids = build_values(letter, patient, kind, utcnow().date())
    rendered = render(kind, values)
    status = MessageStatus.BLOCKED if rendered.missing else MessageStatus.READY
    content_hash = (
        hashlib.sha256(("blocked:" + ",".join(rendered.missing)).encode()).hexdigest()
        if rendered.missing
        else rendered.content_hash
    )

    if current and current.content_hash == content_hash and current.status == status:
        # Nothing changed. Keep the letter's status in step with what was already delivered.
        if status == MessageStatus.READY:
            from app.services.delivery import refresh_letter_status

            letter.status = _STATUS_FOR_KIND[kind]
            refresh_letter_status(db, current)
            db.commit()
        return

    if current is not None:
        current.superseded_at = utcnow()
    message = PatientMessage(
        query_letter_id=letter.id,
        kind=kind,
        status=status,
        blocked_reasons=[f"missing:{name}" for name in rendered.missing],
        template_version=TEMPLATE_VERSION,
        whatsapp_text=rendered.whatsapp_text,
        email_subject=rendered.email_subject,
        email_text=rendered.email_text,
        email_html=rendered.email_html,
        values=values,
        point_ids=point_ids,
        content_hash=content_hash,
    )
    db.add(message)

    reasons = [r for r in letter.review_reasons if r != MESSAGE_VALUES_MISSING]
    if status == MessageStatus.BLOCKED:
        reasons.append(MESSAGE_VALUES_MISSING)
        letter.status = QueryLetterStatus.AWAITING_REVIEW
    else:
        letter.status = _STATUS_FOR_KIND[kind]
    letter.review_reasons = reasons

    db.flush()
    if status == MessageStatus.READY:
        # Sending follows automatically, in the same transaction: no send button.
        from app.services.delivery import queue_dispatch

        queue_dispatch(db, message)
    record_audit(
        db,
        action=AuditAction.MESSAGE_PREPARED,
        entity_type="patient_message",
        entity_id=message.id,
        patient_id=letter.patient_id,
        details={
            "query_letter_id": str(letter.id),
            "kind": kind.value,
            "status": status.value,
            "missing_values": rendered.missing,
            "items": len(point_ids),
            "template_version": TEMPLATE_VERSION,
            "due_date_source": values["due_date_source"],
            "content_hash": content_hash[:16],
        },
    )
    db.commit()


def compose_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is not None:
        letter.review_reasons = sorted({*letter.review_reasons, "message_preparation_failed"})
        letter.status = QueryLetterStatus.AWAITING_REVIEW


_EDITABLE = ("insurer_name", "claim_reference", "response_days")


def update_letter_details(
    db: Session, *, actor: User, letter: QueryLetter, changes: dict[str, Any], request: Request
) -> QueryLetter:
    """Staff correct values used in the message; the message is prepared again."""
    changed = sorted(
        name for name in _EDITABLE if name in changes and getattr(letter, name) != changes[name]
    )
    for name in changed:
        setattr(letter, name, changes[name])
    if changed:
        queue_compose(db, letter)
        record_audit(
            db,
            action=AuditAction.QUERY_LETTER_UPDATED,
            actor_id=actor.id,
            request=request,
            entity_type="query_letter",
            entity_id=letter.id,
            patient_id=letter.patient_id,
            details={"changed": changed},
        )
    db.commit()
    return letter
