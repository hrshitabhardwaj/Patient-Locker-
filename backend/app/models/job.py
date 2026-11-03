from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Identity, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, str_enum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    # Gave up after the final attempt.
    DEAD = "dead"


class Job(Base):
    """A unit of background work, queued in the same transaction as the change that needs it."""

    __tablename__ = "jobs"
    __table_args__ = (
        # At most one queued or running job per dedupe key.
        Index(
            "uq_jobs_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"),
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(default=5, server_default="5")
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_at: Mapped[datetime | None]
    locked_by: Mapped[str | None] = mapped_column(String(64))
    # Error class and our own message only; never document content.
    last_error: Mapped[str | None] = mapped_column(String(500))
    dedupe_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
