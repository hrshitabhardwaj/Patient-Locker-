#!/usr/bin/env sh
# Production start command (Render): migrate, run the job worker, then serve the API.
# The worker shares this service because uploaded files live on this service's disk.
set -e

alembic upgrade head

python -m app.pipeline.worker &

# Render and Vercel sit in front of the app; trust their X-Forwarded-For so sign-in
# throttling and the audit log see each visitor's address, not the proxy's.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips '*' --no-access-log
