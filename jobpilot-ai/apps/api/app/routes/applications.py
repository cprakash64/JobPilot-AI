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
from app.applications.session_refresh import refresh_if_stale, refresh_session_answers
from app.applications import option_mapping_service
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
from app.documents.filenames import build_document_filename
from app.documents.store import export_document as render_document_file
from app.models.entities import (
    ApplicationActionType,
    ApplicationSession,
    ApplicationSessionStatus,
    DocumentFormat,
    GeneratedDocument,
    JobPosting,
    User,
    UserProfile,
)
from app.schemas.applications import (
    MapOptionIn,
    AnswerUpsertIn,
    AutofillResultIn,
    CompleteSessionIn,
    CreateSessionIn,
    ExchangeTokenIn,
    SessionAnswerUpsertIn,
    SessionEventIn,
    SessionNameConfirmIn,
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
    # The structured profile snapshot (employment/education) and scalar answers
    # are one application package. Refresh them before serializing either one;
    # otherwise the extension can fetch an old profile snapshot immediately
    # before /answers notices the revision change, leaving repeated sections
    # stale for the entire browser run.
    user = db.get(User, session.user_id)
    meta = refresh_if_stale(db, session, user) if user else {"refreshed": False}
    if meta.get("refreshed"):
        db.commit()
        db.refresh(session)
    return _serialize_session(db, session)


@router.get("/{session_id}/answers")
def get_session_answers(
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Safe, auto-fillable answers for the extension. Sensitive/unverified items
    are never included here — they live in ``unresolved_questions``.

    Refreshes from the profile first when the snapshot is stale. Without this a
    session prepared BEFORE the user fixed their name/email kept serving the old
    answers forever, which is what left the employer form empty.
    """
    user = db.get(User, session.user_id)
    meta = refresh_if_stale(db, session, user) if user else {"refreshed": False}
    if meta.get("refreshed"):
        db.commit()
        db.refresh(session)
    answers = list(session.generated_answers or [])
    # Workday account credentials are intentionally NOT persisted in the
    # session JSON or answer vault. Decrypt only for this authenticated,
    # session-scoped response and only when this is actually a Workday launch.
    if session.ats_type == "workday":
        from app.profile.credentials import decrypt_workday_password

        profile = db.scalar(select(UserProfile).where(UserProfile.user_id == session.user_id))
        password = decrypt_workday_password(
            profile.workday_password_ciphertext if profile else None
        )
        if password:
            for key in ("application_account_password", "application_account_password_confirm"):
                answers.append(
                    {
                        "canonical_key": key,
                        "value": password,
                        "display_value": "••••••••",
                        "source": "encrypted_profile_credential",
                        "confidence": 1.0,
                        "sensitive": True,
                        "requires_review": False,
                        "verified": True,
                    }
                )
    return {
        "answers": answers,
        "unresolved_questions": session.unresolved_questions or [],
        "refreshed": bool(meta.get("refreshed")),
        "profile_revision": session.profile_revision,
    }


@router.post("/{session_id}/refresh-from-profile")
def refresh_session_from_profile(
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Explicitly rebuild this session's answers from the current profile.

    Ownership is enforced by ``session_access``. The response is sanitized: it
    reports WHICH canonical keys the session now carries, never their values.
    """
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session owner not found")
    meta = refresh_session_answers(db, session, user, force=True)
    db.commit()
    db.refresh(session)
    return {
        "ok": True,
        "refreshed": meta["refreshed"],
        "reason": meta["reason"],
        "profile_revision": session.profile_revision,
        "answer_keys": meta.get("answer_keys", []),
        "unresolved_keys": meta.get("unresolved_keys", []),
    }


@router.put("/{session_id}/answers/{canonical_key}")
def save_session_answer(
    canonical_key: str,
    payload: SessionAnswerUpsertIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Extension "Save for future applications" — the ONLY answer-vault write
    path the extension can reach (it holds a session-scoped token, never the
    user's main token, so the owner-only /application-answers routes below are
    not callable from the extension). Always an explicit, user-initiated
    confirmation from the review widget: recorded verified, source=user_confirmed.
    """
    if canonical_key not in CANONICAL_KEYS and not is_sensitive_key(canonical_key):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown canonical key")
    row = vault.upsert_answer(
        db,
        session.user_id,
        canonical_key,
        {
            "value": payload.value,
            "display_value": payload.display_value or payload.value,
            "source": "user_confirmed",
            "is_user_verified": True,
            "allow_auto_fill": True,
            "scope": payload.scope,
            "company_key": payload.company_key,
        },
    )
    db.commit()
    db.refresh(row)
    log_action(db, session.id, ApplicationActionType.status_changed, field_key=canonical_key,
               source="extension", status="answer_saved")
    db.commit()
    return {"ok": True, "answer": _serialize_answer(row)}


@router.put("/{session_id}/profile/name")
def confirm_session_profile_name(
    payload: SessionNameConfirmIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Extension-facing structured-name confirmation (session-scoped token —
    mirrors PUT /profile/name, which requires the user's main token)."""
    from app.applications.answer_vault_service import confirm_name

    profile = confirm_name(
        db,
        session.user_id,
        payload.first_name,
        payload.last_name,
        middle_name=payload.middle_name,
        preferred_first_name=payload.preferred_first_name,
        preferred_last_name=payload.preferred_last_name,
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    db.commit()
    return {
        "ok": True,
        "first_name": profile.first_name,
        "middle_name": profile.middle_name,
        "last_name": profile.last_name,
        "preferred_first_name": profile.preferred_first_name,
        "preferred_last_name": profile.preferred_last_name,
        "full_name": profile.full_name,
        # Legacy keys for older extension builds.
        "given_name": profile.first_name,
        "family_name": profile.last_name,
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


@router.post("/{session_id}/map-option")
async def map_dropdown_option(
    payload: MapOptionIn,
    session: ApplicationSession = Depends(session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Translate a CONFIRMED canonical answer into one of the exact option labels
    an employer's dropdown offers (section E/G).

    The extension calls this only after deterministic exact/alias matching has
    already failed, and only for a field scoped inside the verified application
    form. The service enforces — in code, not prompt text — that the model may
    never originate a consequential answer and may never return a label we did
    not supply. The OpenAI key never leaves the server.
    """
    confirmed = (payload.confirmed_answer or "").strip()
    if not confirmed:
        # Look for a verified vault answer for this key before giving up, so the
        # extension does not have to send profile data it may not hold.
        row = next(
            (r for r in vault.list_answers(db, session.user_id)
             if r.canonical_key == payload.canonical_key and r.is_user_verified),
            None,
        )
        confirmed = (row.value or "").strip() if row else ""

    mapping = await option_mapping_service.map_option(
        question_label=payload.question_label,
        options=payload.options,
        canonical_key=payload.canonical_key,
        confirmed_answer=confirmed or None,
        help_text=payload.help_text or "",
    )
    log_action(
        db, session.id, "option_mapping", field_key=payload.canonical_key,
        source="extension",
        status="mapped" if mapping.usable else "needs_user",
        confidence=mapping.confidence,
    )
    db.commit()
    return {
        "selected_option_label": mapping.selected_option_label,
        "confidence": mapping.confidence,
        "requires_user_confirmation": mapping.requires_user_confirmation,
        "usable": mapping.usable,
        "reason": mapping.reason,
    }


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
    profile = session.profile_snapshot or {}
    filename = build_document_filename(
        kind=kind,
        fmt=fmt.value,
        full_name=profile.get("full_name"),
        first_name=profile.get("first_name") or profile.get("given_name"),
        last_name=profile.get("last_name") or profile.get("family_name"),
        company=(session.job_snapshot or {}).get("company"),
    )
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
        "profile": session.profile_snapshot or {},
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
        "scope": row.scope,
        "company_key": row.company_key,
        "source": row.source,
        "is_user_verified": row.is_user_verified,
        "verification_required": row.verification_required,
        "allow_auto_fill": row.allow_auto_fill,
        "sensitive": is_sensitive_key(row.canonical_key),
        "confidence": row.confidence,
        "last_verified_at": row.last_verified_at,
    }
