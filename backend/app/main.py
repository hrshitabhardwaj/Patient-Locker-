import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import ServiceError
from app.core.logging import configure_logging

logger = logging.getLogger("app.request")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Responses carry patient data; browsers and proxies must not keep copies.
    "Cache-Control": "no-store",
}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Patient Locker API",
        version="0.1.0",
        # Interactive docs only outside production.
        docs_url=None if settings.app_env == "production" else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "production" else "/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    # Uploads plus room for the multipart envelope. Larger requests are refused before
    # the body is read. (In production the reverse proxy enforces the same limit.)
    max_request_bytes = settings.max_upload_bytes + 1024 * 1024

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        started = time.perf_counter()
        content_length = request.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > max_request_bytes:
            response = JSONResponse(
                {"detail": f"Request too large; files may be at most {settings.max_upload_mb} MB."},
                status_code=413,
            )
        else:
            response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request.state.request_id
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        # Path only: query strings can carry patient names from search boxes.
        logger.info(
            "%s %s %s %.0fms user=%s request=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            getattr(request.state, "user_id", "-"),
            request.state.request_id,
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # So visiting the bare API address explains itself instead of returning 404.
    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "Patient Locker API", "health": "/api/v1/health"}

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
