from contextlib import nullcontext
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import LLMResult
from app.core.security import CSRF_HEADER
from app.models import AuditLog
from app.pipeline.worker import run_one

DEFAULT_PASSWORD = "correct-horse-42"
SAMPLES = Path(__file__).parent / "fixtures" / "samples"


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def run_jobs(db: Session, limit: int = 20) -> int:
    """Run queued jobs in the test's own session until none are due. Returns how many ran."""
    ran = 0
    while ran < limit and run_one(lambda: nullcontext(db), "test-worker"):
        ran += 1
    return ran


class FakeLLM:
    """Stands in for the model.

    `answers` maps a schema class name to an answer, or to a list of answers given in turn
    (an Exception in the list is raised instead). `error` is raised for every call.
    """

    model_name = "fake:test"

    def __init__(self, answers: dict | None = None, error: Exception | None = None):
        answers = answers or {}
        self.answers = {k: list(v) if isinstance(v, list) else v for k, v in answers.items()}
        self.error = error
        self.calls: list[dict] = []

    def generate_json(self, *, system, prompt, schema: type[BaseModel], images=None):
        self.calls.append(
            {"schema": schema.__name__, "system": system, "prompt": prompt, "images": images or []}
        )
        if self.error:
            raise self.error
        answer = self.answers[schema.__name__]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return LLMResult(value=schema.model_validate(answer), model=self.model_name, duration_ms=7)


def login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    """Sign in and set the CSRF header on the client for later unsafe requests."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    csrf_token = response.json()["csrf_token"]
    client.headers[CSRF_HEADER] = csrf_token
    return csrf_token


def audit_entries(db: Session, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)))
