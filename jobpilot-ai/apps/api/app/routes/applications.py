"""Assisted auto-apply API.

Web endpoints authenticate with the user's main token; extension endpoints
accept a session-scoped token (or the owner's main token). Every route enforces
ownership server-side, independent of what the frontend hides.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.applications import answer_vault_service as vault
from app.applications import preparation
from app.applications.canonical import CANONICAL_KEYS, is_sensitive_key
from app.applications.preparation import PreparationError
from app.applications.session_service import (
    SessionError,
    apply_status,
    cancel_session,
    complete_session,
    create_application_session,
    exchange_launch_token,
    expire,
    is_expired,
    log_action,
)
from app.core.security import decode_access_token
from app.core.session_tokens import decode_scoped_token
from app.db.session import get_db
from app.documents.store import export_document as render_document_file
from app.models.entities import (
    ApplicationActionType,
    ApplicationSession,
    ApplicationSessionStatus,
    DocumentFormat,
    GeneratedDocument,
    JobPosting,
    User,
)
from app.schemas.applications import (
    AnswerUpsertIn,
    AutofillResultIn,
    CompleteSessionIn,
    CreateSessionIn,
    ExchangeTokenIn,
    SessionEventIn,
    StatusPatchIn,
)

logger = logging.getLogger("jobpilot.applications")

router = APIRouter(prefix="/application-sessions", tags=["applications"])
answers_router = APIRouter(prefix="/application-answers", tags=["applications"])

_bearer = HTTPBearer(auto_error=False)

# --- lightweight in-process rate limiter (best-effort; generous limits) ------ #
_RATE_BUCKETS: dict[str, deque[float]] = {}


def _rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _RATE_BUCKETS.setdefault(key, deque())
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Slow down.")
    bucket.append(now)


# --------------------------------------------------------------------------- #
# Access resolution: session-scoped token OR the owning user's main token.
# --------------------------------------------------------------------------- #
def _resolve_session(
    session_id: int,
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> ApplicationSession:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = credentials.credentials
    session = db.get(ApplicationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application session not found")

    scoped = decode_scoped_token(token, "session")
    if scoped is not None:
        if int(scoped["sid"]) != session_id or int(scoped["uid"]) != session.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token not valid for this session")
    else:
        subject = decode_access_token(token)
        if subject is None or int(subject) != session.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this session")

    if is_expired(session) and session.status not in {
        ApplicationSessionStatus.completed,
        ApplicationSessionStatus.cancelled,
        ApplicationSessionStatus.expired,
    }:
        expire(db, session)
    return session


def session_access(
    session_id: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> ApplicationSession:
    return _resolve_session(session_id, credentials, db)


# --------------------------------------------------------------------------- #
# Create + token exchange
# --------------------------------------------------------------------------- #
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Prepare an assisted-application package for a job.

    Failures return a consistent structured envelope so the frontend can show a
    specific, actionable message and decide whether to offer retry::

        {"error": {"code", "message", "stage", "retryable", "request_id"}}
    """
    request_id = getattr(request.state, "request_id", None)
    _rate_limit(f"create:{user.id}", limit=20, window_seconds=60)
    logger.info("apply.session.request user=%s job=%s rid=%s", user.id, payload.job_id, request_id)
    try:
        job = db.get(JobPosting, payload.job_id)
    except OperationalError as exc:
        db.rollback()
        return _preparation_error_response(preparation.database_unavailable(), request_id)
    if job is None:
        logger.info("apply.session.request job_not_found user=%s job=%s rid=%s", user.id, payload.job_id, request_id)
        return _preparation_error_response(preparation.job_not_found(), request_id)
    try:
        session, launch_token = await create_application_session(db, user, job)
    except PreparationError as exc:
        logger.info(
            "apply.session.request prep_failed user=%s job=%s stage=%s code=%s rid=%s",
            user.id, payload.job_id, exc.stage.value, exc.code, request_id,
        )
        return _preparation_error_response(exc, request_id)
    except OperationalError as exc:
        db.rollback()
        return _preparation_error_response(preparation.database_unavailable(), request_id)
    body = _serialize_session(db, session)
    body["extension_launch_token"] = launch_token
    return body


def _preparation_error_response(exc: PreparationError, request_id: str | None) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload(request_id))


@router.post("/token")
def token_exchange(payload: ExchangeTokenIn, db: Session = Depends(get_db)) -> dict:
    """Extension exchanges a one-time launch token for a session-scoped token.
    No user auth header — the launch token itself is the (single-use) credential."""
    _rate_limit("exchange", limit=120, window_seconds=60)
    try:
        session, session_token = exchange_launch_token(db, payload.launch_token)
    except SessionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {
        "session_token": session_token,
        "session": _serialize_session(db, session),
    }


# --------------------------------------------------------------------------- #
# Read + status
# --------------------------------------------------------------------------- #
@router.get("/{session_id}")
def get_session(session: ApplicationSession = Depends(session_access), db: Session = Depends(get_db)) -> dict:
    return _serialize_session(db, session)


@router.get("/{session_id}/answers")
def get_session_answers(session: ApplicationSession = Depends(session_access)) -> dict:
    """Safe, auto-fillable answers for the extension. Sensitive/unverified items
    are never included here — they live in ``unresolved_questions``."""
    return {
        "answers": session.generated_answers or [],
        "unresolved_questions": session.unresolved_questions or [],
    }


@router.patch("/{session_id}/status")
def patch_status(
    payload: StatusPatchIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    try:
        new_status = ApplicationSessionStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown status") from exc
    try:
        apply_status(db, session, new_status, source="client")
    except SessionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _serialize_session(db, session)


# --------------------------------------------------------------------------- #
# Documents (authenticated; never expose storage paths)
# --------------------------------------------------------------------------- #
@router.get("/{session_id}/resume")
def download_resume(
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
    fmt: DocumentFormat = Query(default=DocumentFormat.pdf),
) -> FileResponse:
    return _document_response(db, session, session.tailored_resume_id, fmt, "resume")


@router.get("/{session_id}/cover-letter")
def download_cover_letter(
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
    fmt: DocumentFormat = Query(default=DocumentFormat.pdf),
) -> FileResponse:
    return _document_response(db, session, session.tailored_cover_letter_id, fmt, "cover-letter")


# --------------------------------------------------------------------------- #
# Regenerate (owner only)
# --------------------------------------------------------------------------- #
@router.post("/{session_id}/regenerate-resume")
async def regenerate_resume(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = _owned_session(db, session_id, user)
    from app.documents.resume_generation_service import generate_resume
    from app.documents.store import persist_document
    from app.models.entities import DocumentType

    job = db.get(JobPosting, session.job_id)
    result = await generate_resume(db, user.id, job)
    record = persist_document(db, user.id, job, DocumentType.resume, title=result.title, content=result.content,
                              markdown=result.markdown, plain_text=result.plain_text, quality=result.quality,
                              model_used=result.model_used)
    session.tailored_resume_id = record.id
    log_action(db, session.id, ApplicationActionType.resume_generated, source="user", metadata={"document_id": record.id})
    db.commit()
    return _serialize_session(db, session)


@router.post("/{session_id}/regenerate-cover-letter")
async def regenerate_cover_letter(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = _owned_session(db, session_id, user)
    from app.documents.cover_letter_generation_service import generate_cover_letter
    from app.documents.store import persist_document
    from app.models.entities import DocumentType

    job = db.get(JobPosting, session.job_id)
    result = await generate_cover_letter(db, user.id, job)
    record = persist_document(db, user.id, job, DocumentType.cover_letter, title=result.title, content=result.content,
                              markdown=result.markdown, plain_text=result.plain_text, quality=result.quality,
                              model_used=result.model_used)
    session.tailored_cover_letter_id = record.id
    log_action(db, session.id, ApplicationActionType.cover_letter_generated, source="user",
               metadata={"document_id": record.id})
    db.commit()
    return _serialize_session(db, session)


# --------------------------------------------------------------------------- #
# Events, complete, cancel
# --------------------------------------------------------------------------- #
@router.post("/{session_id}/events")
def post_event(
    payload: SessionEventIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    log_action(
        db, session.id, payload.action_type, field_key=payload.field_key, source=payload.source or "extension",
        status=payload.status, confidence=payload.confidence, metadata=payload.metadata or {},
    )
    db.commit()
    return {"ok": True}


_ALLOWED_AUTOFILL_STATUS = {
    "completed",
    "completed_with_review",
    "partial",
    "no_fields",
    "failed",
    "cancelled",
}
_ALLOWED_UPLOAD_KINDS = {"resume", "cover_letter"}


@router.post("/{session_id}/autofill-results")
def record_autofill_results(
    payload: AutofillResultIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Record a safe, PII-free summary of what the extension autofilled.

    Authenticated with the session-scoped token (or the owner's main token) via
    ``session_access`` — the same credential the extension already holds. Stores
    only counts + machine codes in the audit trail; never field values or HTML.
    """
    status_value = payload.status if payload.status in _ALLOWED_AUTOFILL_STATUS else "unknown"
    uploaded = [k for k in payload.documents_uploaded if k in _ALLOWED_UPLOAD_KINDS][:10]
    failures = [
        {"field_key": f.field_key[:120], "reason_code": f.reason_code[:60]} for f in payload.failures[:100]
    ]
    summary = {
        "status": status_value,
        "ats": (payload.ats or None),
        "fields_discovered": payload.fields_discovered,
        "fields_filled": payload.fields_filled,
        "documents_uploaded": uploaded,
        "review_items": payload.review_items,
        "failures": failures,
    }
    logger.info(
        "apply.session.autofill_results session=%s user=%s ats=%s status=%s discovered=%s filled=%s uploaded=%s review=%s failures=%s",
        session.id, session.user_id, summary["ats"], status_value,
        payload.fields_discovered, payload.fields_filled, len(uploaded), payload.review_items, len(failures),
    )
    log_action(
        db, session.id, "autofill_summary", source="extension", status=status_value, metadata=summary,
    )
    db.commit()
    return {"ok": True, "summary": summary}


@router.post("/{session_id}/complete")
def complete(
    payload: CompleteSessionIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    try:
        complete_session(db, session, confirmed=payload.confirmed, source="user")
    except SessionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _serialize_session(db, session)


@router.post("/{session_id}/cancel")
def cancel(
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    cancel_session(db, session, source="user")
    return _serialize_session(db, session)


# --------------------------------------------------------------------------- #
# Answer vault CRUD (owner only)
# --------------------------------------------------------------------------- #
@answers_router.get("")
def list_answers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = vault.list_answers(db, user.id)
    return {"answers": [_serialize_answer(row) for row in rows]}


@answers_router.put("/{canonical_key}")
def upsert_answer(
    canonical_key: str,
    payload: AnswerUpsertIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if canonical_key not in CANONICAL_KEYS and not is_sensitive_key(canonical_key):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown canonical key")
    row = vault.upsert_answer(db, user.id, canonical_key, payload.model_dump(exclude_none=True))
    db.commit()
    db.refresh(row)
    return {"answer": _serialize_answer(row)}


@answers_router.post("/{canonical_key}/verify")
def verify_answer(canonical_key: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = vault.mark_verified(db, user.id, canonical_key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")
    db.commit()
    db.refresh(row)
    return {"answer": _serialize_answer(row)}


@answers_router.post("/{canonical_key}/disable-autofill")
def disable_autofill(canonical_key: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = vault.upsert_answer(db, user.id, canonical_key, {"allow_auto_fill": False})
    db.commit()
    db.refresh(row)
    return {"answer": _serialize_answer(row)}


@answers_router.delete("/{canonical_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_answer(canonical_key: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.entities import ApplicationAnswer

    row = db.scalar(
        select(ApplicationAnswer).where(
            (ApplicationAnswer.user_id == user.id) & (ApplicationAnswer.canonical_key == canonical_key)
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #
def _owned_session(db: Session, session_id: int, user: User) -> ApplicationSession:
    session = db.get(ApplicationSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application session not found")
    return session


def _document_response(
    db: Session, session: ApplicationSession, doc_id: int | None, fmt: DocumentFormat, kind: str
) -> FileResponse:
    if doc_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No {kind} prepared for this session")
    record = db.get(GeneratedDocument, doc_id)
    if record is None or record.user_id != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    path = render_document_file(record, fmt)
    db.commit()
    filename = f"{(record.title or kind).replace(' ', '_')}.{fmt.value}"
    return FileResponse(path, filename=filename)


def _serialize_session(db: Session, session: ApplicationSession) -> dict[str, Any]:
    answers = session.generated_answers or []
    review_count = sum(1 for a in answers if a.get("requires_review")) + len(session.unresolved_questions or [])
    job = session.job_snapshot or {}
    return {
        "session_id": session.id,
        "status": session.status.value,
        "official_application_url": session.source_url,
        "ats_type": session.ats_type,
        "job": {
            "id": session.job_id,
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
        },
        "resume": _doc_status(db, session.id, session.tailored_resume_id, "resume"),
        "cover_letter": _doc_status(db, session.id, session.tailored_cover_letter_id, "cover-letter"),
        "answers_available": sum(1 for a in answers if not a.get("requires_review")),
        "review_required_count": review_count,
        "unresolved_questions": session.unresolved_questions or [],
        "warnings": session.warnings or [],
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "completed_at": session.completed_at,
    }


def _doc_status(db: Session, session_id: int, doc_id: int | None, kind: str) -> dict[str, Any]:
    if doc_id is None:
        return {"status": "missing", "document_id": None, "download_url": None}
    return {
        "status": "ready",
        "document_id": doc_id,
        "download_url": f"/application-sessions/{session_id}/{kind}",
    }


def _serialize_answer(row) -> dict[str, Any]:
    return {
        "canonical_key": row.canonical_key,
        "value": row.value,
        "display_value": row.display_value,
        "source": row.source,
        "is_user_verified": row.is_user_verified,
        "verification_required": row.verification_required,
        "allow_auto_fill": row.allow_auto_fill,
        "sensitive": is_sensitive_key(row.canonical_key),
        "confidence": row.confidence,
        "last_verified_at": row.last_verified_at,
    }
