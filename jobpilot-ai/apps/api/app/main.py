import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.ai.provider import ai_provider
from app.core.config import settings
from app.core.config_validation import enforce as enforce_production_config
from app.core.log_redaction import install as install_log_redaction
from app.db.session import SessionLocal
from app.people.feature_flags import configuration_summary as people_configuration_summary
from app.routes import applications, auth, debug, jobs, people, privacy, profile
from app.services.readiness import check_readiness

logger = logging.getLogger("jobpilot")
# Uvicorn configures its own named loggers but leaves application loggers at
# the process default (WARNING). Keep JobPilot's secret-free startup and
# operational diagnostics observable without changing third-party log levels.
logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Safety net first, so anything logged during startup is already filtered.
    install_log_redaction()

    # Refuse to serve production traffic with development defaults. Raising here
    # aborts startup, so an orchestrator's rollout halts instead of promoting a
    # replica that signs JWTs with the publicly-known default key.
    enforce_production_config(settings)

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
    people_status = people_configuration_summary(settings)
    logger.info(
        "People config: recommendations_enabled=%s email_discovery_enabled=%s "
        "primary_provider_configured=%s encryption_key_configured=%s "
        "global_budget_configured=%s per_user_budget_configured=%s "
        "rollout_mode=%s environment=%s",
        people_status["recommendations_enabled"],
        people_status["email_discovery_enabled"],
        people_status["primary_provider_configured"],
        people_status["encryption_key_configured"],
        people_status["global_budget_configured"],
        people_status["per_user_budget_configured"],
        people_status["rollout_mode"],
        people_status["environment"],
    )
    yield


# The OpenAPI schema enumerates every endpoint, parameter and payload shape, so
# it is served outside production only (override with DOCS_ENABLED).
_docs_on = settings.docs_are_enabled()

app = FastAPI(
    title="JobPilot AI API",
    version="0.1.0",
    description="Compliant, user-controlled AI job-search and application copilot.",
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
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
    # Unpacked and store-installed MV3 builds have different extension IDs.
    # CORS is not authorization: every assisted-apply endpoint still requires a
    # one-time launch token or session-scoped Bearer token.
    allow_origin_regex=r"chrome-extension://[a-p]{32}",
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(people.router)
app.include_router(privacy.router)
app.include_router(debug.router)
app.include_router(applications.router)
app.include_router(applications.answers_router)


@app.get("/healthz")
@app.get("/health")
def healthz() -> dict:
    """Liveness. Proves only that this process is up and serving.

    Deliberately touches NOTHING external — no database, Redis, filesystem or
    OpenAI. A liveness probe wired to a dependency restarts healthy replicas
    during a database blip and escalates a partial outage into a full one; use
    /readyz for dependency state. Exposes no version or configuration detail.

    ``/health`` is kept as an alias for existing callers.
    """
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness. 200 only when this replica can actually serve requests.

    Gated on PostgreSQL connectivity AND the schema being migrated to head.
    Redis is probed and reported but is not gating — ingestion degrades to
    inline scoring without it. Body carries booleans, revisions and exception
    type names only (never driver text, which embeds the DSN).
    """
    db = SessionLocal()
    try:
        ready, checks = check_readiness(db)
    finally:
        db.close()
    status_code = 200 if ready else 503
    status_text = "ready" if ready else "not_ready"
    return JSONResponse(status_code=status_code, content={"status": status_text, "checks": checks})
