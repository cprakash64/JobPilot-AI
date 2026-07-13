"""Application-session orchestration.

Creating a session: validate the official URL, snapshot job + profile, reuse or
generate the tailored resume and cover letter, compute the safe answer set, link
the tracker, mint a one-time launch token, and write the audit trail. Also
handles the launch->session token exchange, status transitions, completion, and
cancellation. Ownership and expiry are enforced here and again at the route.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.applications.answer_vault_service import build_safe_answers
from app.applications.ats import detect_ats_from_url
from app.applications.preparation import (
    PreparationError,
    database_unavailable,
    invalid_application_url,
    profile_incomplete,
)
from app.applications.url_validation import InvalidApplicationURL, validate_official_url
from app.core.session_tokens import (
    create_launch_token,
    create_session_token,
    decode_scoped_token,
    hash_token,
)
from app.documents.cover_letter_generation_service import generate_cover_letter
from app.documents.resume_generation_service import generate_resume
from app.documents.store import persist_document
from app.models.entities import (
    ApplicationActionType,
    ApplicationAuditLog,
    ApplicationSession,
    ApplicationSessionStatus,
    ApplicationStatus,
    ApplicationTracker,
    DocumentType,
    Experience,
    GeneratedDocument,
    JobPosting,
    User,
    UserProfile,
)
from app.services.documents import public_dict

logger = logging.getLogger("jobpilot.applications")

SESSION_TTL_MINUTES = 30

# Statuses a client may set via PATCH. Terminal states go through dedicated,
# audited endpoints (/complete, /cancel) and expiry is server-managed.
PATCHABLE_STATUSES = {
    ApplicationSessionStatus.opened,
    ApplicationSessionStatus.filling,
    ApplicationSessionStatus.review_required,
    ApplicationSessionStatus.ready_for_review,
    ApplicationSessionStatus.failed,
}

# A session in one of these non-terminal states is still usable: a repeated
# "Prepare application" click reuses it rather than creating a duplicate.
REUSABLE_STATUSES = {
    ApplicationSessionStatus.ready,
    ApplicationSessionStatus.opened,
    ApplicationSessionStatus.filling,
    ApplicationSessionStatus.review_required,
    ApplicationSessionStatus.ready_for_review,
}


class SessionError(ValueError):
    """Domain error for invalid session operations (mapped to 4xx at the route)."""


def log_action(
    db: Session,
    session_id: int,
    action_type: str,
    *,
    field_key: str | None = None,
    source: str | None = None,
    status: str | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        ApplicationAuditLog(
            session_id=session_id,
            action_type=str(action_type),
            field_key=field_key,
            source=source,
            status=status,
            confidence=confidence,
            metadata_json=metadata or {},
        )
    )


async def create_application_session(db: Session, user: User, job: JobPosting) -> tuple[ApplicationSession, str]:
    """Prepare an assisted-application session for ``job``.

    Returns ``(session, raw_launch_token)``. The raw launch token is returned
    exactly once; only its hash is stored.

    The work is a small, independently-traceable pipeline:
      load_job -> load_candidate_profile -> select/generate tailored resume ->
      generate cover letter -> generate application answers ->
      persist_application_package.

    Known, user-actionable failures (unusable URL, incomplete profile, database
    unavailable) raise ``PreparationError`` with a stable code, the failing stage,
    an HTTP status, and a retry hint. Optional artifacts (resume, cover letter)
    degrade to warnings and never fail the package. Repeated preparation is
    idempotent — an existing usable session for the same (user, job) is reused
    rather than duplicated.
    """
    from app.applications.preparation import PreparationStage

    # --- load_job: an unsafe/missing official URL is fatal and non-retryable. ---
    try:
        source_url = validate_official_url(job.application_url or job.source_url)
    except InvalidApplicationURL as exc:
        logger.info("apply.session.stage_failed stage=%s user=%s job=%s reason=invalid_url",
                    PreparationStage.load_job.value, user.id, job.id)
        raise invalid_application_url(str(exc)) from exc

    logger.info("apply.session.create start user=%s job=%s ats=%s", user.id, job.id, detect_ats_from_url(source_url))

    # --- load_candidate_profile: require the minimum to build a real application. ---
    _require_complete_profile(db, user)

    # --- idempotency: reuse an existing usable session instead of duplicating. ---
    reused = _reuse_active_session(db, user, job)
    if reused is not None:
        raw_launch_token = create_launch_token(reused.id, user.id)
        reused.launch_token_hash = hash_token(raw_launch_token)
        reused.launch_token_used = False
        db.commit()
        db.refresh(reused)
        logger.info("apply.session.create reused user=%s job=%s session=%s", user.id, job.id, reused.id)
        return reused, raw_launch_token

    # --- generate tailored resume + cover letter (optional; degrade to warnings). ---
    resume_doc, resume_error = await _safe_generate(db, user, job, DocumentType.resume)
    cover_doc, cover_error = await _safe_generate(db, user, job, DocumentType.cover_letter)

    # --- generate_application_answers. ---
    safe_answers, unresolved = build_safe_answers(db, user)
    warnings = _warnings(resume_error, cover_error, unresolved)

    # --- persist_application_package: the only place we touch the DB for writes;
    # classify DB failures instead of leaking a raw driver error to the client. ---
    session = ApplicationSession(
        user_id=user.id,
        job_id=job.id,
        status=ApplicationSessionStatus.ready,
        source_url=source_url,
        ats_type=detect_ats_from_url(source_url),
        profile_snapshot=_json_safe(_profile_snapshot(db, user)),
        job_snapshot=_json_safe(public_dict(job)),
        tailored_resume_id=resume_doc.id if resume_doc else None,
        tailored_cover_letter_id=cover_doc.id if cover_doc else None,
        generated_answers=safe_answers,
        unresolved_questions=unresolved,
        warnings=warnings,
        launch_token_hash=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=SESSION_TTL_MINUTES),
    )
    try:
        db.add(session)
        db.flush()

        raw_launch_token = create_launch_token(session.id, user.id)
        session.launch_token_hash = hash_token(raw_launch_token)

        tracker = _link_tracker(db, user, job)
        session.tracker_id = tracker.id

        log_action(db, session.id, ApplicationActionType.session_created, metadata={"job_id": job.id})
        if resume_doc:
            log_action(db, session.id, ApplicationActionType.resume_generated, source="jobpilot",
                       metadata={"document_id": resume_doc.id})
        if cover_doc:
            log_action(db, session.id, ApplicationActionType.cover_letter_generated, source="jobpilot",
                       metadata={"document_id": cover_doc.id})
        db.commit()
    except OperationalError as exc:
        # Connection lost / DB down: genuinely transient and retryable.
        db.rollback()
        logger.error("apply.session.stage_failed stage=%s user=%s job=%s err=%s",
                     PreparationStage.persist_application_package.value, user.id, job.id, type(exc).__name__)
        raise database_unavailable() from exc

    db.refresh(session)
    logger.info(
        "apply.session.create ok user=%s job=%s session=%s status=%s resume=%s cover=%s warnings=%d",
        user.id, job.id, session.id, session.status.value,
        resume_doc is not None, cover_doc is not None, len(warnings),
    )
    return session, raw_launch_token


def exchange_launch_token(db: Session, raw_token: str) -> tuple[ApplicationSession, str]:
    """One-time exchange of a launch token for a session-scoped token."""
    claims = decode_scoped_token(raw_token, "launch")
    if claims is None:
        raise SessionError("Invalid or expired launch token.")
    session = db.get(ApplicationSession, int(claims["sid"]))
    if session is None or session.user_id != int(claims["uid"]):
        raise SessionError("Launch token does not match a session.")
    if session.launch_token_used or session.launch_token_hash != hash_token(raw_token):
        raise SessionError("Launch token already used.")
    if is_expired(session):
        expire(db, session)
        raise SessionError("Application session has expired.")

    session.launch_token_used = True
    log_action(db, session.id, ApplicationActionType.token_exchanged, source="extension")
    db.commit()
    return session, create_session_token(session.id, session.user_id)


def apply_status(db: Session, session: ApplicationSession, new_status: ApplicationSessionStatus, *, source: str) -> None:
    if new_status not in PATCHABLE_STATUSES:
        raise SessionError(f"Status '{new_status.value}' cannot be set directly.")
    session.status = new_status
    log_action(db, session.id, ApplicationActionType.status_changed, status=new_status.value, source=source)
    db.commit()


def complete_session(db: Session, session: ApplicationSession, *, confirmed: bool, source: str) -> None:
    """Mark an application complete. Only ever on explicit user confirmation —
    JobPilot never infers submission on its own."""
    if not confirmed:
        raise SessionError("Completion requires explicit user confirmation.")
    session.status = ApplicationSessionStatus.completed
    session.completed_at = datetime.now(UTC)
    if session.tracker_id:
        tracker = db.get(ApplicationTracker, session.tracker_id)
        if tracker and tracker.status != ApplicationStatus.applied:
            tracker.status = ApplicationStatus.applied
            tracker.applied_at = datetime.now(UTC)
    log_action(db, session.id, ApplicationActionType.application_completed, source=source,
               metadata={"job_snapshot": session.job_snapshot.get("title") if session.job_snapshot else None})
    db.commit()


def cancel_session(db: Session, session: ApplicationSession, *, source: str) -> None:
    session.status = ApplicationSessionStatus.cancelled
    log_action(db, session.id, ApplicationActionType.application_cancelled, source=source)
    db.commit()


def is_expired(session: ApplicationSession) -> bool:
    if session.expires_at is None:
        return False
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return datetime.now(UTC) >= expires


def expire(db: Session, session: ApplicationSession) -> None:
    if session.status not in {ApplicationSessionStatus.completed, ApplicationSessionStatus.cancelled}:
        session.status = ApplicationSessionStatus.expired
        db.commit()


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
async def _safe_generate(
    db: Session, user: User, job: JobPosting, doc_type: DocumentType
) -> tuple[GeneratedDocument | None, str | None]:
    """Reuse an existing tailored document or generate a new one. Returns
    ``(document, error_message)`` — a generation failure yields ``(None, msg)``
    so it becomes a warning instead of failing the whole session."""
    kind = "resume" if doc_type == DocumentType.resume else "cover letter"
    try:
        existing = db.scalar(
            select(GeneratedDocument)
            .where(
                (GeneratedDocument.user_id == user.id)
                & (GeneratedDocument.job_id == job.id)
                & (GeneratedDocument.type == doc_type)
            )
            .order_by(GeneratedDocument.created_at.desc())
        )
        if existing is not None:
            logger.info("apply.session.doc reused kind=%s user=%s job=%s", kind, user.id, job.id)
            return existing, None

        logger.info("apply.session.doc generate start kind=%s user=%s job=%s", kind, user.id, job.id)
        if doc_type == DocumentType.resume:
            result = await generate_resume(db, user.id, job)
        else:
            result = await generate_cover_letter(db, user.id, job)
        record = persist_document(
            db, user.id, job, doc_type,
            title=result.title, content=result.content, markdown=result.markdown,
            plain_text=result.plain_text, quality=result.quality, model_used=result.model_used,
        )
        logger.info("apply.session.doc generate ok kind=%s user=%s job=%s doc=%s", kind, user.id, job.id, record.id)
        return record, None
    except Exception as exc:  # noqa: BLE001 - degrade gracefully; never fail the session
        logger.warning("apply.session.doc generate failed kind=%s user=%s job=%s err=%s", kind, user.id, job.id, type(exc).__name__)
        db.rollback()
        return None, f"Tailored {kind} could not be prepared automatically ({type(exc).__name__}). You can retry it."


def _require_complete_profile(db: Session, user: User) -> UserProfile:
    """Raise ``PROFILE_INCOMPLETE`` (422) when the profile is missing the minimum
    needed to prepare a truthful application. We never fabricate identity, so a
    name is mandatory; skills/experience are needed for a meaningful resume."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    missing: list[str] = []
    if profile is None or not (profile.full_name or "").strip():
        missing.append("basic_info")
    has_experience = bool(
        db.scalar(select(Experience.id).where(Experience.user_id == user.id).limit(1))
    )
    if profile is None or (not (profile.skills or []) and not has_experience):
        # Need at least skills or experience to tailor a resume without inventing.
        missing.append("skills")
    if missing:
        raise profile_incomplete(missing)
    return profile


def _reuse_active_session(db: Session, user: User, job: JobPosting) -> ApplicationSession | None:
    """Return the most recent still-usable session for this (user, job), or None.

    Makes repeated "Prepare application" clicks idempotent: they reuse the live
    session instead of creating duplicate sessions, resumes, cover letters, and
    answer sets. Expired-but-not-yet-marked sessions are lazily expired and skipped
    so a fresh package is prepared."""
    existing = db.scalar(
        select(ApplicationSession)
        .where(
            (ApplicationSession.user_id == user.id)
            & (ApplicationSession.job_id == job.id)
            & (ApplicationSession.status.in_(REUSABLE_STATUSES))
        )
        .order_by(ApplicationSession.created_at.desc())
    )
    if existing is None:
        return None
    if is_expired(existing):
        expire(db, existing)
        return None
    return existing


def _link_tracker(db: Session, user: User, job: JobPosting) -> ApplicationTracker:
    tracker = db.scalar(
        select(ApplicationTracker).where(
            (ApplicationTracker.user_id == user.id) & (ApplicationTracker.job_id == job.id)
        )
    )
    if tracker is None:
        tracker = ApplicationTracker(user_id=user.id, job_id=job.id, status=ApplicationStatus.applying)
        db.add(tracker)
        db.flush()
    elif tracker.status in {ApplicationStatus.saved, ApplicationStatus.ready_to_apply}:
        tracker.status = ApplicationStatus.applying
    return tracker


def _profile_snapshot(db: Session, user: User) -> dict[str, Any]:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        return {"email": user.email}
    return {
        "email": user.email,
        "full_name": profile.full_name,
        "location": ", ".join(
            filter(None, [profile.location_city, profile.location_state, profile.location_country])
        ),
        "skills": profile.skills or [],
        "target_roles": profile.target_roles or [],
    }


def _json_safe(value: Any) -> Any:
    """Recursively convert datetimes/dates to strings for JSON columns."""
    return json.loads(json.dumps(value, default=str))


def _warnings(resume_error: str | None, cover_error: str | None, unresolved: list[dict]) -> list[str]:
    warnings: list[str] = []
    if resume_error:
        warnings.append(resume_error)
    if cover_error:
        warnings.append(cover_error)
    if unresolved:
        warnings.append(
            f"{len(unresolved)} sensitive question(s) must be answered by you on the employer page."
        )
    return warnings
