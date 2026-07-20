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
    default_scope_for_key,
    is_sensitive_key,
    is_verification_required,
    normalize_company_key,
    sensitive_reason,
)
from app.applications.fixture_guard import (
    DEMO_EMAIL_REPLACEMENT_REASON,
    is_known_fixture_email,
)
from app.models.entities import (
    ApplicationAnswer,
    Education,
    Experience,
    SensitiveDemographics,
    User,
    UserProfile,
)
from app.profile.eeo import display_label
from app.profile.emails import ResolvedEmail, resolve_application_email
from app.profile.names import compose_full_name, resolve_preferred_names, suggest_name_parts
from app.profile.phone import parts_from_stored


def derive_profile_answers(
    user_email: str,
    profile: dict[str, Any],
    experiences: list[dict],
    *,
    allow_dev_fixtures: bool = False,
) -> list[dict]:
    """Non-sensitive facts derivable from the profile. Pure + unit-testable.

    Consequential-but-fillable facts (work authorization, sponsorship) are
    flagged ``requires_review`` so the extension fills-then-flags rather than
    silently committing them.

    NOTE: first_name/last_name are deliberately NOT produced here — see
    ``_name_answers_and_unresolved``. Splitting ``full_name`` on whitespace is
    not a safe way to derive a structured name (a multi-token given name like
    "Chandra Prakash" is not "first token = given name").
    """
    full_name = (profile.get("full_name") or "").strip()
    current = experiences[0] if experiences else {}

    phone = parts_from_stored(
        country_code=profile.get("phone_country_code"),
        country_iso2=profile.get("phone_country_iso2"),
        national_number=profile.get("phone_national_number"),
        e164=profile.get("phone_e164"),
        legacy_phone=profile.get("phone"),
    )

    candidates: list[tuple[str, Any, bool]] = [
        # (canonical_key, value, requires_review)
        ("full_name", full_name, False),
        ("preferred_name", profile.get("preferred_name"), False),
        # The APPLICATION email, not the login identity — see profile/emails.py.
        # A fixture login never reaches an employer, and a confirmed
        # application_email always wins.
        ("email", _application_email(user_email, profile, allow_dev_fixtures).value, False),
        # Phone is published in every form a target control might ask for, so
        # the extension selects rather than concatenates. E.164 is the default
        # single-input answer; the country/national pair is used when the site
        # splits them (which is what prevents a duplicated "+1").
        ("phone", phone.e164 or profile.get("phone"), False),
        ("phone_country", phone.country_code, False),
        ("phone_country_iso2", phone.country_iso2, False),
        ("phone_national", phone.national_number, False),
        ("city", profile.get("location_city"), False),
        ("postal_code", profile.get("location_postal_code"), False),
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


def _application_email(
    user_email: str, profile: dict[str, Any], allow_dev_fixtures: bool = False
) -> ResolvedEmail:
    # The automated-fixture escape hatch is honoured here too: a seeded demo
    # session deliberately opts in to demo data, and refusing its email would
    # break that opt-in rather than protect anyone.
    if allow_dev_fixtures and not (profile.get("application_email") or "").strip():
        return ResolvedEmail((user_email or "").strip(), "account_email", False)
    return resolve_application_email(
        application_email=profile.get("application_email"),
        application_email_confirmed=bool(profile.get("application_email_confirmed")),
        account_email=user_email,
    )


def _name_answers_and_unresolved(profile: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Structured-name resolution, kept separate from ``derive_profile_answers``
    because it can produce EITHER safe answers OR unresolved questions,
    never both, and never a silent guess.

    - Confirmed structured name (``name_confirmed`` True, first+last set):
      use verbatim as high-confidence, non-review answers. ``full_name`` is
      recomposed from the parts (first + middle + last) so a Full Name field
      gets "Chandra Prakash Pandey" while Last Name gets only "Pandey".
    - Otherwise: no name part is auto-filled. They become unresolved questions
      carrying a *suggestion* only, which the review widget shows as an
      editable, pre-filled confirmation prompt.
    """
    full_name = (profile.get("full_name") or "").strip()
    first = (profile.get("first_name") or "").strip()
    middle = (profile.get("middle_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    confirmed = bool(profile.get("name_confirmed")) and bool(first) and bool(last)

    if confirmed:
        # Preferred names fall back to the legal name: Greenhouse's preferred-name
        # fields state that the legal name should be used when no preferred name
        # exists, and the user confirmed these parts explicitly.
        preferred_first, preferred_last = resolve_preferred_names(
            preferred_first_name=profile.get("preferred_first_name"),
            preferred_last_name=profile.get("preferred_last_name"),
            first_name=first,
            last_name=last,
        )
        parts = [
            ("first_name", first),
            ("middle_name", middle),
            ("last_name", last),
            ("full_name", compose_full_name(first, middle, last)),
            ("preferred_first_name", preferred_first),
            ("preferred_last_name", preferred_last),
        ]
        return (
            [
                {
                    "canonical_key": key,
                    "value": value,
                    "display_value": value,
                    "source": "profile",
                    "confidence": 0.99,
                    "sensitive": False,
                    "requires_review": False,
                    "verified": True,
                }
                for key, value in parts
                if value
            ],
            [],
        )

    if not full_name:
        return [], []

    suggestion = suggest_name_parts(full_name)
    reason = "Confirm how your name splits into first, middle, and last name."
    unresolved = [
        {
            "canonical_key": key,
            "reason": reason,
            "sensitive": False,
            "has_saved_value": False,
            "action": "confirm_name",
            "suggested_value": suggested,
            # False for any multi-token name — the review UI must require an
            # explicit confirmation rather than pre-accepting the guess.
            "suggestion_certain": suggestion.certain,
        }
        for key, suggested in (
            ("first_name", suggestion.first_name),
            ("middle_name", suggestion.middle_name),
            ("last_name", suggestion.last_name),
        )
    ]
    return [], unresolved


def build_safe_answers(
    db: Session,
    user: User,
    company: str | None = None,
    *,
    allow_dev_fixtures: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Return ``(safe_answers, unresolved_questions)`` for a session.

    ``safe_answers`` are non-sensitive and auto-fillable (verified vault entries
    override derived profile facts). ``unresolved_questions`` are items the user
    must handle directly: sensitive categories, consequential facts not yet
    verified, and an unconfirmed structured name. ``company`` scopes
    company-specific saved answers (e.g. "previously employed here?") to the
    employer this session is actually for — a company-scoped answer saved for
    one employer is never silently reused for another.

    ``allow_dev_fixtures`` (default False) is the automated-test escape hatch:
    real employer/application sessions leave it False, so a known seeded demo
    identity (e.g. ``demo@example.com``) NEVER reaches an externally-hosted
    application as a verified answer — it becomes ``missing_information`` in
    EVERY environment, including development. Only tests that deliberately
    exercise the demo fixtures pass True.
    """
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    experiences = _recent_experiences(db, user.id)
    education = _education(db, user.id)
    profile_dict = _profile_dict(profile)
    company_key = normalize_company_key(company)

    derived = {
        a["canonical_key"]: a
        for a in derive_profile_answers(
            user.email, profile_dict, experiences, allow_dev_fixtures=allow_dev_fixtures
        )
    }
    name_answers, name_unresolved = _name_answers_and_unresolved(profile_dict)
    for a in name_answers:
        derived[a["canonical_key"]] = a
    # Keys owned by the CONFIRMED structured profile name. A legacy answer-vault
    # row (e.g. an old automatic "Chandra" / "Prakash Pandey" split) must never
    # override them — that stale split was still appearing on live applications.
    structured_name_keys = {a["canonical_key"] for a in name_answers}

    saved_rows = list(db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)))
    # Company-scoped rows for a DIFFERENT employer than this session are
    # invisible here entirely — not merged, not left as an unresolved "has a
    # saved value" hint. They simply don't apply to this application.
    saved = {
        row.canonical_key: row
        for row in saved_rows
        if row.scope != "company" or row.company_key == company_key
    }

    safe: dict[str, dict] = dict(derived)
    unresolved: list[dict] = list(name_unresolved)

    for key, row in saved.items():
        if key in structured_name_keys:
            # The structured profile name is authoritative; skip the legacy row.
            continue
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

    # The Profile wizard's Optional EEO section is the single source of truth
    # for voluntary demographics. Consent on that form explicitly covers
    # assisted application filling, so these are verified, auto-fillable
    # sensitive answers — never inferred from the career profile.
    demographic_answers = _demographic_answers(db, user.id)
    for answer in demographic_answers:
        key = answer["canonical_key"]
        safe[key] = answer
        unresolved = [item for item in unresolved if item.get("canonical_key") != key]

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

    # Real-employer boundary: a known seeded demo identity must never leave the
    # API as a verified answer for an external application. Turn it into an
    # unresolved item the user resolves via the authenticated profile flow.
    if not allow_dev_fixtures:
        _redact_dev_fixture_identities(safe, unresolved)

    safe_list = [a for a in safe.values() if _clean(a.get("value"))]
    return safe_list, unresolved


def _demographic_answers(db: Session, user_id: int) -> list[dict]:
    record = db.scalar(
        select(SensitiveDemographics).where(SensitiveDemographics.user_id == user_id)
    )
    if record is None or not record.consent_to_store or record.needs_review:
        return []

    answers: list[dict] = []

    def add(canonical_key: str, vocabulary_key: str, value: str | None) -> None:
        if not value:
            return
        label = display_label(vocabulary_key, value)
        answers.append(
            {
                "canonical_key": canonical_key,
                "value": label,
                "display_value": label,
                "source": "profile_eeo",
                "confidence": 1.0,
                "sensitive": True,
                "requires_review": False,
                "verified": True,
            }
        )

    add("gender", "gender_identity", record.gender_identity)
    add("veteran_status", "veteran_status", record.veteran_status)
    add("disability_status", "disability_status", record.disability_status)
    add("ethnicity", "hispanic_or_latino", record.hispanic_or_latino)

    races = [value for value in (record.race_ethnicity or []) if value]
    if len(races) == 1:
        add("race", "race_ethnicity", races[0])
    elif len(races) > 1:
        # A single-select employer field represents several selected categories
        # truthfully as "Two or More Races". We never pick one race and discard
        # the others.
        answers.append(
            {
                "canonical_key": "race",
                "value": "Two or More Races",
                "display_value": "Two or More Races",
                "source": "profile_eeo",
                "confidence": 1.0,
                "sensitive": True,
                "requires_review": False,
                "verified": True,
            }
        )
    return answers


def _redact_dev_fixture_identities(safe: dict[str, dict], unresolved: list[dict]) -> None:
    """Drop known dev-fixture identities from the auto-fillable answer set and
    surface them as ``missing_information`` instead. Currently the seeded demo
    email; structured to extend to any future seeded identity."""
    email_answer = safe.get("email")
    fixture_valued = bool(email_answer) and is_known_fixture_email(email_answer.get("value"))

    # Two ways to end up with no usable application email:
    #   1. a fixture value slipped into the answer set (legacy path), or
    #   2. resolve_application_email refused to produce one at all because the
    #      account email is a fixture and no application_email is set.
    # Case 2 previously produced SILENCE — no answer and no question — which is
    # exactly why the live Airbnb Email field stayed blank with nothing in the
    # UI explaining it.
    if fixture_valued or not (email_answer and _clean(email_answer.get("value"))):
        safe.pop("email", None)
        if not any(u.get("canonical_key") == "email" for u in unresolved):
            unresolved.append(
                {
                    "canonical_key": "email",
                    "reason": DEMO_EMAIL_REPLACEMENT_REASON,
                    "sensitive": False,
                    "has_saved_value": False,
                    "action": "replace_demo_email",
                }
            )


# --------------------------------------------------------------------------- #
# CRUD helpers used by the routes
# --------------------------------------------------------------------------- #
def invalidate_legacy_name_answers(db: Session, user_id: int) -> int:
    """Remove answer-vault rows for the structured-name keys once the profile
    carries a CONFIRMED given/family name.

    These rows are the residue of an earlier release that split ``full_name`` on
    whitespace. They are not merely ignored (see ``build_safe_answers``) but
    deleted, so no future code path can resurrect the wrong split. Returns the
    number of rows removed."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None or not profile.name_confirmed:
        return 0
    if not (profile.first_name or "").strip() or not (profile.last_name or "").strip():
        return 0

    stale = list(
        db.scalars(
            select(ApplicationAnswer).where(
                (ApplicationAnswer.user_id == user_id)
                & (ApplicationAnswer.canonical_key.in_(STRUCTURED_NAME_KEYS))
            )
        )
    )
    for row in stale:
        db.delete(row)
    return len(stale)


def list_answers(db: Session, user_id: int) -> list[ApplicationAnswer]:
    return list(db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user_id)).all())


STRUCTURED_NAME_KEYS: frozenset[str] = frozenset(
    {
        "first_name",
        "middle_name",
        "last_name",
        "full_name",
        "preferred_first_name",
        "preferred_last_name",
    }
)


def confirm_name(
    db: Session,
    user_id: int,
    first_name: str,
    last_name: str,
    *,
    middle_name: str | None = None,
    preferred_first_name: str | None = None,
    preferred_last_name: str | None = None,
) -> UserProfile | None:
    """Explicit user confirmation of the structured name split.

    Never called automatically — only in direct response to the user confirming
    (or correcting) the suggested split. Because this IS the confirmation, it
    also recomputes the display ``full_name`` from the parts, so the two can
    never disagree.
    """
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if not first or not last:
        return None
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        return None
    middle = (middle_name or "").strip()
    profile.first_name = first
    profile.middle_name = middle or None
    profile.last_name = last
    profile.preferred_first_name = (preferred_first_name or "").strip() or None
    profile.preferred_last_name = (preferred_last_name or "").strip() or None
    profile.full_name = compose_full_name(first, middle, last)
    profile.name_confirmed = True
    # Confirming the split also clears any legacy auto-derived name rows, so the
    # old incorrect split can never reappear on a later application.
    invalidate_legacy_name_answers(db, user_id)
    return profile


def upsert_answer(db: Session, user_id: int, canonical_key: str, values: dict[str, Any]) -> ApplicationAnswer:
    """Create/update a vault answer. ``values`` may include ``scope`` and
    ``company_key`` (both optional); scope defaults per canonical key
    (see ``default_scope_for_key``), and company_key is only meaningful — and
    only persisted as non-empty — when the resolved scope is "company"."""
    scope = values.get("scope") or default_scope_for_key(canonical_key)
    company_key = normalize_company_key(values.get("company_key")) if scope == "company" else ""

    row = db.scalar(
        select(ApplicationAnswer).where(
            (ApplicationAnswer.user_id == user_id)
            & (ApplicationAnswer.canonical_key == canonical_key)
            & (ApplicationAnswer.company_key == company_key)
        )
    )
    if row is None:
        row = ApplicationAnswer(
            user_id=user_id,
            canonical_key=canonical_key,
            scope=scope,
            company_key=company_key,
            verification_required=is_sensitive_key(canonical_key) or is_verification_required(canonical_key),
        )
        db.add(row)
    else:
        row.scope = scope
        row.company_key = company_key
    for field in ("value", "display_value", "source", "is_user_verified", "allow_auto_fill", "confidence"):
        if field in values and values[field] is not None:
            setattr(row, field, values[field])
    # A sensitive answer defaults to auto-fill OFF until the user opts in.
    if is_sensitive_key(canonical_key) and "allow_auto_fill" not in values:
        row.allow_auto_fill = bool(row.is_user_verified) and bool(row.allow_auto_fill)
    return row


def mark_verified(db: Session, user_id: int, canonical_key: str, company_key: str = "") -> ApplicationAnswer | None:
    from datetime import UTC, datetime

    row = db.scalar(
        select(ApplicationAnswer).where(
            (ApplicationAnswer.user_id == user_id)
            & (ApplicationAnswer.canonical_key == canonical_key)
            & (ApplicationAnswer.company_key == normalize_company_key(company_key))
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
        "first_name": profile.first_name,
        "middle_name": profile.middle_name,
        "last_name": profile.last_name,
        "preferred_first_name": profile.preferred_first_name,
        "preferred_last_name": profile.preferred_last_name,
        "preferred_name": profile.preferred_name,
        "name_confirmed": profile.name_confirmed,
        "application_email": profile.application_email,
        "application_email_confirmed": profile.application_email_confirmed,
        "phone": profile.phone,
        "phone_country_code": profile.phone_country_code,
        "phone_country_iso2": profile.phone_country_iso2,
        "phone_national_number": profile.phone_national_number,
        "phone_e164": profile.phone_e164,
        "location_city": profile.location_city,
        "location_state": profile.location_state,
        "location_postal_code": profile.location_postal_code,
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


def _bool_str(value: Any) -> str:
    if value is None:
        return ""
    return "Yes" if bool(value) else "No"


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""
