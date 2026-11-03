"""Background worker: runs queued jobs until stopped.

    python -m app.pipeline.worker        (or `make worker`)
"""

import logging
import os
import signal
import socket
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_sessionmaker
from app.models import Job
from app.pipeline.jobs import claim, mark_failed, mark_succeeded

logger = logging.getLogger("app.worker")

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class Handler:
    run: Callable[[Session, dict[str, Any]], None]
    # Called once when the job has failed for the last time, to record the outcome.
    on_dead: Callable[[Session, dict[str, Any], str], None] | None = None


def _handlers() -> dict[str, Handler]:
    from app.services import delivery, matching, messages, processing, query_letters

    return {
        delivery.DISPATCH_JOB: Handler(delivery.run_dispatch, delivery.dispatch_dead),
        matching.MATCH_JOB: Handler(matching.run_match, matching.match_dead),
        messages.COMPOSE_JOB: Handler(messages.run_compose, messages.compose_dead),
        processing.EXTRACT_JOB: Handler(processing.run_extract, processing.extract_dead),
        processing.PROFILE_JOB: Handler(processing.run_profile, processing.profile_dead),
        query_letters.EXTRACT_QUERY_JOB: Handler(
            query_letters.run_query_extract, query_letters.query_extract_dead
        ),
    }


def run_one(
    session_factory: SessionFactory, worker_id: str, handlers: dict[str, Handler] | None = None
) -> bool:
    """Claim and run a single job. Returns False when there was nothing to do."""
    handlers = handlers if handlers is not None else _handlers()
    lease = timedelta(minutes=get_settings().job_lease_minutes)

    with session_factory() as db:
        job = claim(db, worker_id=worker_id, lease=lease)
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload)

    handler = handlers.get(kind)
    started = time.perf_counter()
    with session_factory() as db:
        try:
            if handler is None:
                raise LookupError(f"no handler for job kind {kind!r}")
            handler.run(db, payload)
            job = db.get(Job, job_id)
            mark_succeeded(job)
            db.commit()
            logger.info(
                "job %s %s succeeded in %.1fs", job_id, kind, time.perf_counter() - started
            )
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            dead = mark_failed(job, exc)
            if dead and handler and handler.on_dead:
                handler.on_dead(db, payload, job.last_error or "")
            db.commit()
            logger.warning(
                "job %s %s attempt %d failed (%s)%s",
                job_id,
                kind,
                job.attempts,
                type(exc).__name__,
                "; giving up" if dead else "; will retry",
            )
    return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False

    def stop(signum, frame):  # noqa: ARG001
        nonlocal stopping
        stopping = True
        logger.info("worker %s stopping after the current job", worker_id)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    sessions = get_sessionmaker()
    handlers = _handlers()
    logger.info("worker %s started", worker_id)
    while not stopping:
        try:
            worked = run_one(sessions, worker_id, handlers)
        except Exception:
            logger.exception("worker loop error; pausing before retrying")
            worked = False
            time.sleep(5)
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
