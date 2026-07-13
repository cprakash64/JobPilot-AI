"""Application Answer Vault: verified, reusable answers to common questions.

Derives high-confidence, non-sensitive facts from the user's saved profile and
merges them with explicitly-saved vault answers. Produces the *safe* answer set
the extension is allowed to auto-fill, plus the list of questions that must be
resolved by the user (sensitive categories and unverified consequential facts).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.canonical import (
    is_sensitive_key,
    is_verification_required,
    sensitive_reason,
)
from app.models.entities import (
    ApplicationAnswer,
    Education,
    Experience,
    User,
    UserProfile,
)


def derive_profile_answers(user_email: str, profile: dict[str, Any], experiences: list[dict]) -> list[dict]:
    """Non-sensitive facts derivable from the profile. Pure + unit-testable.

    Consequential-but-fillable facts (work authorization, sponsorship) are
    flagged ``requires_review`` so the extension fills-then-flags rather than
    silently committing them.
    """
    full_name = (profile.get("full_name") or "").strip()
    first, last = _split_name(full_name)
    current = experiences[0] if experiences else {}

    candidates: list[tuple[str, Any, bool]] = [
        # (canonical_key, value, requires_review)
        ("full_name", full_name, False),
        ("first_name", first, False),
        ("last_name", last, False),
        ("email", (user_email or "").strip(), False),
        ("phone", profile.get("phone"), False),
        ("city", profile.get("location_city"), False),
        ("state", profile.get("location_state"), False),
        ("country", profile.get("location_country"), False),
        ("linkedin_url", profile.get("linkedin_url"), False),
        ("github_url", profile.get("github_url"), False),
        ("portfolio_url", profile.get("portfolio_url"), False),
        ("current_company", current.get("company"), False),
        ("current_title", current.get("title"), False),
        ("preferred_workplace", profile.get("remote_preference"), False),
        ("work_authorization_us", profile.get("work_authorization"), True),
        ("sponsorship_required_now", _bool_str(profile.get("requires_sponsorship")), True),
        ("sponsorship_required_future", _bool_str(profile.get("requires_sponsorship")), True),
        ("willing_to_relocate", _bool_str(profile.get("open_to_relocation")), False),
    ]

    answers: list[dict] = []
    for key, value, requires_review in candidates:
        text = _clean(value)
        if not text:
            continue
        answers.append(
            {
                "canonical_key": key,
                "value": text,
                "display_value": text,
                "source": "profile",
                "confidence": 0.9 if requires_review else 0.97,
                "sensitive": False,
                "requires_review": requires_review,
                "verified": False,
            }
        )
    return answers


def build_safe_answers(db: Session, user: User) -> tuple[list[dict], list[dict]]:
    """Return ``(safe_answers, unresolved_questions)`` for a session.

    ``safe_answers`` are non-sensitive and auto-fillable (verified vault entries
    override derived profile facts). ``unresolved_questions`` are items the user
    must handle directly: sensitive categories and consequential facts not yet
    verified.
    """
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    experiences = _recent_experiences(db, user.id)
    education = _education(db, user.id)
    profile_dict = _profile_dict(profile)

    derived = {a["canonical_key"]: a for a in derive_profile_answers(user.email, profile_dict, experiences)}
    saved = {a.canonical_key: a for a in db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id))}

    safe: dict[str, dict] = dict(derived)
    unresolved: list[dict] = []

    for key, row in saved.items():
        if is_sensitive_key(key):
            # A sensitive answer is only auto-fillable when explicitly verified
            # AND enabled; otherwise the user resolves it on the employer page.
            if row.is_user_verified and row.allow_auto_fill and _clean(row.value):
                safe[key] = _answer_from_row(row, sensitive=True)
            else:
                unresolved.append(_unresolved_from_key(key, saved=row))
            continue
        if not row.allow_auto_fill or not _clean(row.value):
            safe.pop(key, None)
            continue
        merged = _answer_from_row(row, sensitive=False)
        # A verified vault answer clears the review flag for consequential facts.
        if row.is_user_verified:
            merged["requires_review"] = False
            merged["verified"] = True
        safe[key] = merged

    # Consequential facts that were derived but never verified stay in safe (so
    # the extension fills-then-flags) — no extra unresolved entry needed.
    if _has_education(education):
        safe.setdefault(
            "education",
            {
                "canonical_key": "education",
                "value": _education_summary(education),
                "display_value": _education_summary(education),
                "source": "profile",
                "confidence": 0.85,
                "sensitive": False,
                "requires_review": True,
                "verified": False,
            },
        )

    safe_list = [a for a in safe.values() if _clean(a.get("value"))]
    return safe_list, unresolved


# --------------------------------------------------------------------------- #
# CRUD helpers used by the routes
# --------------------------------------------------------------------------- #
def list_answers(db: Session, user_id: int) -> list[ApplicationAnswer]:
    return list(db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user_id)).all())


def upsert_answer(db: Session, user_id: int, canonical_key: str, values: dict[str, Any]) -> ApplicationAnswer:
    row = db.scalar(
        select(ApplicationAnswer).where(
            (ApplicationAnswer.user_id == user_id) & (ApplicationAnswer.canonical_key == canonical_key)
        )
    )
    if row is None:
        row = ApplicationAnswer(
            user_id=user_id,
            canonical_key=canonical_key,
            verification_required=is_sensitive_key(canonical_key) or is_verification_required(canonical_key),
        )
        db.add(row)
    for field in ("value", "display_value", "source", "is_user_verified", "allow_auto_fill", "confidence"):
        if field in values and values[field] is not None:
            setattr(row, field, values[field])
    # A sensitive answer defaults to auto-fill OFF until the user opts in.
    if is_sensitive_key(canonical_key) and "allow_auto_fill" not in values:
        row.allow_auto_fill = bool(row.is_user_verified) and bool(row.allow_auto_fill)
    return row


def mark_verified(db: Session, user_id: int, canonical_key: str) -> ApplicationAnswer | None:
    from datetime import UTC, datetime

    row = db.scalar(
        select(ApplicationAnswer).where(
            (ApplicationAnswer.user_id == user_id) & (ApplicationAnswer.canonical_key == canonical_key)
        )
    )
    if row is None:
        return None
    row.is_user_verified = True
    row.last_verified_at = datetime.now(UTC)
    return row


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _answer_from_row(row: ApplicationAnswer, *, sensitive: bool) -> dict:
    return {
        "canonical_key": row.canonical_key,
        "value": _clean(row.value),
        "display_value": _clean(row.display_value) or _clean(row.value),
        "source": "vault",
        "confidence": float(row.confidence or 0.9),
        "sensitive": sensitive,
        "requires_review": not row.is_user_verified,
        "verified": bool(row.is_user_verified),
    }


def _unresolved_from_key(key: str, *, saved: ApplicationAnswer | None) -> dict:
    return {
        "canonical_key": key,
        "reason": sensitive_reason(key),
        "sensitive": True,
        "has_saved_value": bool(saved and _clean(saved.value)),
        "action": "answer_on_employer_page",
    }


def _profile_dict(profile: UserProfile | None) -> dict[str, Any]:
    if profile is None:
        return {}
    return {
        "full_name": profile.full_name,
        "phone": profile.phone,
        "location_city": profile.location_city,
        "location_state": profile.location_state,
        "location_country": profile.location_country,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "work_authorization": profile.work_authorization,
        "requires_sponsorship": profile.requires_sponsorship,
        "open_to_relocation": profile.open_to_relocation,
        "remote_preference": profile.remote_preference,
    }


def _normalize_experience_date(value: Any) -> date:
    """Coerce any experience date value to a ``datetime.date`` so sort keys stay
    type-stable. Missing/blank/malformed values map to ``date.min`` (sorted last,
    never dropped). Never mutates the source record."""
    if value is None or value == "":
        return date.min
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return date.min
    return date.min


def _recent_experiences(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(select(Experience).where(Experience.user_id == user_id)).all()
    # Sort key must be type-stable: currently-working first, then most-recent
    # date. Dates may be datetime.date, ISO strings (legacy), or missing — always
    # normalize to a real date so Python never compares str with date.
    ordered = sorted(
        rows,
        key=lambda experience: (
            bool(experience.currently_working),
            _normalize_experience_date(experience.end_date or experience.start_date),
        ),
        reverse=True,
    )
    return [{"company": e.company, "title": e.title} for e in ordered]


def _education(db: Session, user_id: int) -> list[Education]:
    return list(db.scalars(select(Education).where(Education.user_id == user_id)).all())


def _has_education(education: list[Education]) -> bool:
    return any((e.school or "").strip() for e in education)


def _education_summary(education: list[Education]) -> str:
    parts = []
    for e in education:
        if not (e.school or "").strip():
            continue
        parts.append(", ".join(filter(None, [e.school, e.degree, e.major])))
    return "; ".join(parts)


def _split_name(full_name: str) -> tuple[str, str]:
    tokens = full_name.split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    return tokens[0], " ".join(tokens[1:])


def _bool_str(value: Any) -> str:
    if value is None:
        return ""
    return "Yes" if bool(value) else "No"


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""
