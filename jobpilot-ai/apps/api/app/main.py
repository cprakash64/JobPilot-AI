import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.ai.provider import ai_provider
from app.core.config import settings
from app.db.session import SessionLocal
from app.routes import applications, auth, debug, jobs, privacy, profile
from app.services.readiness import check_database_readiness

logger = logging.getLogger("jobpilot")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Log a secret-free summary of the OpenAI configuration at startup so it is
    # obvious from the API logs whether AI generation is enabled.
    status = ai_provider.status()
    logger.info(
        "OpenAI config: api_key_present=%s smart_model=%s fast_model=%s provider_enabled=%s",
        status["api_key_present"],
        status["smart_model"],
        status["fast_model"],
        status["provider_enabled"],
    )
    yield


app = FastAPI(
    title="JobPilot AI API",
    version="0.1.0",
    description="Compliant, user-controlled AI job-search and application copilot.",
    lifespan=lifespan,
)

# IMPORTANT: middleware order. `add_middleware` makes the LAST-added middleware
# the OUTERMOST. We add the unhandled-error catcher FIRST and CORS LAST so CORS
# is outermost and wraps the catcher's 500 response.
#
# Why this matters: an unhandled exception in a route otherwise propagates out to
# Starlette's ServerErrorMiddleware, which sits OUTSIDE CORSMiddleware — so its
# 500 has no `Access-Control-Allow-Origin` header. A cross-origin browser then
# blocks that response and the frontend `fetch` rejects with an opaque
# "Failed to fetch" instead of the real error. Catching the exception in an
# inner middleware turns it into a normal response that flows back out THROUGH
# CORSMiddleware, so the browser receives a readable, CORS-enabled JSON error.
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    # A correlation id ties a user-visible error back to the exact server log line
    # without exposing any internals. Honour an inbound X-Request-ID if present.
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - last-resort catch so CORS headers are attached
        logger.exception("Unhandled error on %s %s rid=%s", request.method, request.url.path, request_id)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Something went wrong. Please try again.",
                    "retryable": True,
                    "request_id": request_id,
                },
                # Kept for backward compatibility with older clients that read `detail`.
                "detail": "Something went wrong. Please try again.",
            },
            headers={"X-Request-ID": request_id},
        )
    response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(privacy.router)
app.include_router(debug.router)
app.include_router(applications.router)
app.include_router(applications.answers_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    db = SessionLocal()
    try:
        ready, checks = check_database_readiness(db)
    finally:
        db.close()
    status_code = 200 if ready else 503
    status_text = "ready" if ready else "not_ready"
    return JSONResponse(status_code=status_code, content={"status": status_text, "checks": checks})
