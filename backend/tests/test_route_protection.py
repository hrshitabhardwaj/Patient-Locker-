"""Every API route must refuse anonymous requests unless it is on the public list.

This guards future routes too: a new endpoint that forgets authentication fails here.
"""

import re
import uuid

from fastapi.testclient import TestClient

from app.main import create_app

PUBLIC_ROUTES = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/login"),
}
# Called by messaging providers: no session, but every request must carry a valid signature
# (see test_webhooks.py, which checks each of these refuses unsigned requests).
WEBHOOK_ROUTES = {
    ("POST", "/api/v1/webhooks/twilio/status"),
    ("GET", "/api/v1/webhooks/meta/whatsapp"),
    ("POST", "/api/v1/webhooks/meta/whatsapp"),
    ("POST", "/api/v1/webhooks/resend"),
}


def _api_routes():
    # The OpenAPI schema is the public, stable list of routes. Routes hidden from the
    # schema (include_in_schema=False) must get their own explicit test.
    for path, operations in create_app().openapi()["paths"].items():
        if path.startswith("/api/v1"):
            for method in operations:
                yield method.upper(), path


def test_public_list_matches_real_routes():
    assert PUBLIC_ROUTES | WEBHOOK_ROUTES <= set(_api_routes())


def test_every_non_public_route_requires_sign_in(client: TestClient):
    checked = 0
    for method, path in _api_routes():
        if (method, path) in PUBLIC_ROUTES | WEBHOOK_ROUTES:
            continue
        concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        response = client.request(method, concrete, json={})
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"
        checked += 1
    assert checked > 0
