"""Route registry. Anything not explicitly public requires a signed-in user."""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_password_current
from app.api.v1 import (
    audit_logs,
    auth,
    dashboard,
    documents,
    health,
    patients,
    query_letters,
    users,
    webhooks,
)

api_router = APIRouter()

# Public: no session needed.
api_router.include_router(health.router)
api_router.include_router(auth.public_router)
# Called by the messaging providers; each request is verified by the provider's signature.
api_router.include_router(webhooks.router)

# Signed in, reachable while a temporary password is still pending.
api_router.include_router(auth.router, dependencies=[Depends(get_current_user)])

# Everything else: signed in with a current password. New routers go here.
protected = APIRouter(dependencies=[Depends(require_password_current)])
protected.include_router(users.router)
protected.include_router(users.directory_router)
protected.include_router(audit_logs.router)
protected.include_router(patients.router)
protected.include_router(documents.router)
protected.include_router(query_letters.router)
protected.include_router(dashboard.router)
api_router.include_router(protected)
