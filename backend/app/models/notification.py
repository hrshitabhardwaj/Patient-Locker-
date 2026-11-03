import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, str_enum


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    # Handed to the provider; if the worker stops here the outcome is unknown.
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class Notification(Base):
    """One delivery of a patient message on one channel.

    The idempotency key (letter + channel + message content) is unique, so the same content is
    never sent twice on a channel, whatever retries or re-runs happen.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_messages.id", ondelete="CASCADE"), index=True
    )
    query_letter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_letters.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"))
    provider: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(320))
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum(NotificationStatus, "notification_status")
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]

    events: Mapped[list["NotificationEvent"]] = relationship(
        order_by="NotificationEvent.id", cascade="all, delete-orphan"
    )


class NotificationEvent(Base):
    """Every status change of a notification, as reported by us or the provider."""

    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum(NotificationStatus, "notification_event_status")
    )
    detail: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
