from contextlib import nullcontext
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Job, JobStatus
from app.pipeline.jobs import PermanentJobError, claim, enqueue
from app.pipeline.worker import Handler, run_one

LEASE = timedelta(minutes=20)


def _jobs(db):
    return list(db.scalars(select(Job).order_by(Job.id)))


def _run(db, handlers):
    return run_one(lambda: nullcontext(db), "w1", handlers)


def test_dedupe_key_allows_one_pending_job(db):
    assert enqueue(db, "k", {"n": 1}, dedupe_key="same")
    assert not enqueue(db, "k", {"n": 2}, dedupe_key="same")
    db.commit()

    assert len(_jobs(db)) == 1


def test_finished_job_does_not_block_a_new_one(db):
    calls = []
    enqueue(db, "k", {}, dedupe_key="same")
    db.commit()
    _run(db, {"k": Handler(lambda s, p: calls.append(p))})

    assert enqueue(db, "k", {}, dedupe_key="same")


def test_successful_job_runs_once(db):
    calls = []
    enqueue(db, "k", {"x": 1})
    db.commit()

    assert _run(db, {"k": Handler(lambda s, p: calls.append(p))})
    assert not _run(db, {"k": Handler(lambda s, p: calls.append(p))})
    assert calls == [{"x": 1}]
    assert _jobs(db)[0].status == JobStatus.SUCCEEDED


def test_failed_job_is_retried_later_with_backoff(db):
    def boom(session, payload):
        raise RuntimeError("temporary")

    enqueue(db, "k", {})
    db.commit()
    _run(db, {"k": Handler(boom)})

    [job] = _jobs(db)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.run_after > utcnow() + timedelta(seconds=20)
    assert job.last_error == "RuntimeError: temporary"
    # Not due yet, so nothing runs.
    assert not _run(db, {"k": Handler(boom)})


def test_job_dies_after_max_attempts_and_reports_once(db):
    dead_calls = []

    def boom(session, payload):
        raise RuntimeError("still broken")

    handler = Handler(boom, on_dead=lambda s, p, e: dead_calls.append(e))
    enqueue(db, "k", {"id": 7}, max_attempts=2)
    db.commit()
    for _ in range(2):
        _run(db, {"k": handler})
        db.execute(Job.__table__.update().values(run_after=utcnow()))

    [job] = _jobs(db)
    assert job.status == JobStatus.DEAD
    assert dead_calls == ["RuntimeError: still broken"]


def test_permanent_error_fails_immediately(db):
    def bad_input(session, payload):
        raise PermanentJobError("cannot ever work")

    enqueue(db, "k", {})
    db.commit()
    _run(db, {"k": Handler(bad_input)})

    assert _jobs(db)[0].status == JobStatus.DEAD


def test_unknown_job_kind_is_not_lost_silently(db):
    enqueue(db, "mystery", {}, max_attempts=1)
    db.commit()

    _run(db, {})

    [job] = _jobs(db)
    assert job.status == JobStatus.DEAD
    assert "no handler" in job.last_error


def test_job_abandoned_by_a_crashed_worker_is_reclaimed(db):
    enqueue(db, "k", {})
    db.commit()
    job = claim(db, worker_id="crashed", lease=LEASE)
    assert claim(db, worker_id="w2", lease=LEASE) is None

    job.locked_at = utcnow() - timedelta(minutes=21)
    db.commit()

    reclaimed = claim(db, worker_id="w2", lease=LEASE)
    assert reclaimed.id == job.id and reclaimed.locked_by == "w2" and reclaimed.attempts == 2
