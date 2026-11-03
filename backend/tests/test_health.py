import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db import schema
from app.db.session import get_db
from app.main import create_app


def test_health_reports_database_ok():
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_is_down():
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_health_returns_503_when_migrations_are_pending(monkeypatch):
    # The test database is at the latest migration; pretend a newer one exists.
    monkeypatch.setattr(schema, "latest_migration", lambda: "not-yet-applied")
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "migrations_pending"}


def test_root_points_to_health():
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert response.json()["health"] == "/api/v1/health"


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@db.example:5432/pl",
        "postgresql://u:p@db.example:5432/pl",
        "postgresql+psycopg://u:p@db.example:5432/pl",
    ],
)
def test_database_url_uses_psycopg_driver(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)

    assert Settings().database_url == "postgresql+psycopg://u:p@db.example:5432/pl"
