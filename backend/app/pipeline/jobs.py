"""A durable job queue in PostgreSQL.

Jobs are inserted in the same transaction as the change that needs them, so work is never
lost: if the upload commits, its processing job exists. Workers claim jobs with
FOR UPDATE SKIP LOCKED, so several workers never take the same job.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Job, JobStatus

# Wait before retry n: 30 s, 2 min, 8 min, 30 min (capped).
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_FACTOR = 4
_BACKOFF_MAX_SECONDS = 30 * 60


class PermanentJobError(Exception):
    """Retrying will not help (e.g. the input itself is invalid). The job fails at once."""


class DeferJob(Exception):
    """Not ready yet (e.g. waiting for other work). Run again after `seconds`, no backoff."""

    def __init__(self, reason: str, seconds: int = 15):
        super().__init__(reason)
        self.seconds = seconds


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> bool:
    """Queue a job. Returns False if an identical job (same dedupe key) is already pending."""
    statement = insert(Job).values(
        kind=kind, payload=payload, dedupe_key=dedupe_key, max_attempts=max_attempts
    )
    if dedupe_key is not None:
        statement = statement.on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    return db.execute(statement.returning(Job.id)).scalar() is not None


def claim(db: Session, *, worker_id: str, lease: timedelta) -> Job | None:
    """Take the next due job, including any whose worker vanished mid-run. Commits."""
    now = utcnow()
    job = db.scalar(
        select(Job)
        .where(
            or_(
                and_(Job.status == JobStatus.QUEUED, Job.run_after <= now),
                and_(Job.status == JobStatus.RUNNING, Job.locked_at < now - lease),
            )
        )
        .order_by(Job.run_after, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    db.commit()
    return job


def mark_succeeded(job: Job) -> None:
    job.status = JobStatus.SUCCEEDED
    job.finished_at = utcnow()
    job.locked_at = job.locked_by = None


def mark_failed(job: Job, error: BaseException) -> bool:
    """Record a failed attempt. Returns True if the job is now dead (no more retries)."""
    job.last_error = f"{type(error).__name__}: {error}"[:500]
    job.locked_at = job.locked_by = None
    if isinstance(error, PermanentJobError) or job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.finished_at = utcnow()
        return True
    if isinstance(error, DeferJob):
        job.status = JobStatus.QUEUED
        job.run_after = utcnow() + timedelta(seconds=error.seconds)
        return False
    delay = min(
        _BACKOFF_BASE_SECONDS * _BACKOFF_FACTOR ** (job.attempts - 1), _BACKOFF_MAX_SECONDS
    )
    job.status = JobStatus.QUEUED
    job.run_after = utcnow() + timedelta(seconds=delay)
    return False
