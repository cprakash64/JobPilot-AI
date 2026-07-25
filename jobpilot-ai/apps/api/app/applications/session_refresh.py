"""Keep a prepared application session's answers in step with the profile.

The live failure: the user corrected their structured name and set an
application email, but the session prepared BEFORE those edits kept serving its
original answer snapshot. The extension dutifully filled from that snapshot, so
the employer form still received nothing, and no layer could tell the snapshot
had gone stale.

A session now records the profile revision its answers were built from. Before
the extension is allowed to use a session, the revision is compared with the
profile's current one; a mismatch rebuilds the answers atomically.

Completed and cancelled sessions are historical records of what was actually
sent, so they are never rewritten.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    ApplicationAnswer,
    ApplicationSession,
    ApplicationSessionStatus,
    Education,
    Experience,
    SensitiveDemographics,
    User,
    UserProfile,
)
from app.profile.revision import compute_profile_revision

# Sessions past this point describe something that already happened; rebuilding
# their answers would rewrite history.
TERMINAL_STATUSES = {
    ApplicationSessionStatus.completed,
    ApplicationSessionStatus.cancelled,
    ApplicationSessionStatus.expired,
}


def current_profile_revision(db: Session, user_id: int) -> str:
    """The revision of the user's profile as it stands right now."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    answers = list(
        db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user_id)).all()
    )
    demographics = db.scalar(
        select(SensitiveDemographics).where(SensitiveDemographics.user_id == user_id)
    )
    revision_answers = [
        {
            "canonical_key": a.canonical_key,
            "value": a.value,
            "is_user_verified": a.is_user_verified,
            "scope": a.scope,
            "company_key": a.company_key,
        }
        for a in answers
    ]
    if demographics is not None:
        for field in (
            "gender_identity",
            "veteran_status",
            "disability_status",
            "hispanic_or_latino",
            "race_ethnicity",
            "consent_to_store",
            "needs_review",
        ):
            revision_answers.append(
                {
                    "canonical_key": f"eeo:{field}",
                    "value": getattr(demographics, field),
                    "is_user_verified": bool(demographics.consent_to_store),
                    "scope": "sensitive",
                    "company_key": "",
                }
            )
    # Career records live in separate tables. Fold them into the revision so a
    # newly added job or degree invalidates an already-prepared session too.
    for item in db.scalars(select(Experience).where(Experience.user_id == user_id)).all():
        revision_answers.append(
            {
                "canonical_key": f"experience:{item.id}",
                "value": {
                    "company": item.company,
                    "title": item.title,
                    "location": item.location,
                    "start_date": item.start_date,
                    "end_date": item.end_date,
                    "currently_working": item.currently_working,
                },
                "is_user_verified": True,
                "scope": "global",
                "company_key": "",
            }
        )
    for item in db.scalars(select(Education).where(Education.user_id == user_id)).all():
        revision_answers.append(
            {
                "canonical_key": f"education:{item.id}",
                "value": {
                    "school": item.school,
                    "degree": item.degree,
                    "major": item.major,
                    "minor": item.minor,
                    "start_date": item.start_date,
                    "end_date": item.end_date,
                    "gpa": item.gpa,
                    "gpa_scale": item.gpa_scale,
                },
                "is_user_verified": True,
                "scope": "global",
                "company_key": "",
            }
        )
    return compute_profile_revision(
        profile=_profile_columns(profile),
        vault_answers=revision_answers,
        document_ids=_document_ids(profile),
    )


def _profile_columns(profile: UserProfile | None) -> dict:
    if profile is None:
        return {}
    return {
        column.name: getattr(profile, column.name) for column in profile.__table__.columns
    }


def _document_ids(profile: UserProfile | None) -> dict:
    # Documents are selected per session, not on the profile; the session-level
    # ids are folded in by refresh_session_answers below.
    return {}


def _answer_keys(session: ApplicationSession) -> list[str]:
    return sorted({a.get("canonical_key", "") for a in (session.generated_answers or [])})


def is_stale(db: Session, session: ApplicationSession) -> bool:
    """True when this session's answers predate the profile as it is now."""
    if session.status in TERMINAL_STATUSES:
        return False
    if not session.profile_revision:
        # Prepared before revisions existed — treat as stale exactly once.
        return True
    return session.profile_revision != current_profile_revision(db, session.user_id)


def refresh_session_answers(
    db: Session, session: ApplicationSession, user: User, *, force: bool = False
) -> dict:
    """Rebuild a session's safe answers from the CURRENT profile when stale.

    Returns sanitized metadata: whether a refresh happened, the revision now
    stored, and the canonical keys the session carries — never any answer value.
    Commit is left to the caller so the rebuild and the revision update land in
    one transaction.
    """
    from app.applications.answer_vault_service import build_safe_answers

    if session.status in TERMINAL_STATUSES:
        return {
            "refreshed": False,
            "reason": "session_is_terminal",
            "profile_revision": session.profile_revision,
            "answer_keys": _answer_keys(session),
        }

    revision = current_profile_revision(db, session.user_id)
    if not force and session.profile_revision == revision:
        return {
            "refreshed": False,
            "reason": "already_current",
            "profile_revision": revision,
            "answer_keys": _answer_keys(session),
        }

    company = (session.job_snapshot or {}).get("company")
    safe_answers, unresolved = build_safe_answers(db, user, company=company)
    # Profile refresh is synchronous, while prose generation uses the async GPT
    # provider. Preserve the latest company-specific draft prepared for this
    # session instead of silently deleting it during a scalar-answer refresh.
    written_answers = [
        answer
        for answer in (session.generated_answers or [])
        if answer.get("canonical_key") in {"custom_motivation", "custom_experience"}
    ]
    written_keys = {answer.get("canonical_key") for answer in written_answers}
    safe_answers = [
        answer
        for answer in safe_answers
        if answer.get("canonical_key") not in written_keys
    ] + written_answers

    session.generated_answers = safe_answers
    session.unresolved_questions = unresolved
    # Structured repeaters are consumed from the snapshot by the extension;
    # refresh them atomically with scalar answers.
    from app.applications.session_service import _profile_snapshot

    session.profile_snapshot = _json_safe_snapshot(_profile_snapshot(db, user))
    session.profile_revision = revision
    session.answers_refreshed_at = datetime.now(UTC)

    return {
        "refreshed": True,
        "reason": "rebuilt_from_profile",
        "profile_revision": revision,
        "answer_keys": sorted({a["canonical_key"] for a in safe_answers}),
        "unresolved_keys": sorted({u.get("canonical_key", "") for u in unresolved}),
    }


def refresh_if_stale(db: Session, session: ApplicationSession, user: User) -> dict:
    """Refresh only when needed. Safe to call before every open/reopen/retry."""
    if not is_stale(db, session):
        return {
            "refreshed": False,
            "reason": "already_current",
            "profile_revision": session.profile_revision,
            "answer_keys": _answer_keys(session),
        }
    return refresh_session_answers(db, session, user)


def _json_safe_snapshot(value: dict) -> dict:
    import json

    return json.loads(json.dumps(value, default=str))
