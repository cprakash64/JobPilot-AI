"""Deterministic profile revision for autofill.

The live failure this closes: a user corrected their name and set an application
email, but the already-prepared application session kept serving the OLD answer
snapshot, so the employer form still received nothing. There was no way to tell
that a session had gone stale.

``compute_profile_revision`` hashes exactly the inputs autofill depends on. Two
profiles that would autofill identically produce the same revision; changing any
autofill-relevant fact produces a different one. Fields that cannot affect an
application (display preferences, timestamps, target roles) are deliberately
excluded so a cosmetic edit does not invalidate every prepared session.

It is a pure function of its inputs — no clock, no randomness — so the same
profile always yields the same revision across processes and restarts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Bumped when the SHAPE of the revision changes, so every session is treated as
# stale after a deploy that changes what autofill consumes.
REVISION_SCHEMA_VERSION = "1"


def _norm(value: Any) -> Any:
    """Normalize a value for hashing. None and "" are the same absence."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items())}
    return str(value)


def compute_profile_revision(
    *,
    profile: dict[str, Any] | None,
    vault_answers: list[dict[str, Any]] | None = None,
    document_ids: dict[str, Any] | None = None,
) -> str:
    """Hash the autofill-relevant state of a profile.

    ``vault_answers`` are the user's reusable saved answers (canonical key +
    value + verified flag); ``document_ids`` identifies the selected/generated
    resume and cover letter, because swapping a document changes what an
    application would receive just as much as changing a phone number.
    """
    profile = profile or {}

    payload: dict[str, Any] = {
        "v": REVISION_SCHEMA_VERSION,
        # Identity — only the CONFIRMED structured name matters; an unconfirmed
        # name is not autofilled at all, so it cannot change an application.
        "name_confirmed": _norm(profile.get("name_confirmed")),
        "first_name": _norm(profile.get("first_name")),
        "middle_name": _norm(profile.get("middle_name")),
        "last_name": _norm(profile.get("last_name")),
        "preferred_first_name": _norm(profile.get("preferred_first_name")),
        "preferred_last_name": _norm(profile.get("preferred_last_name")),
        # Contact
        "application_email": _norm(profile.get("application_email")),
        "application_email_confirmed": _norm(profile.get("application_email_confirmed")),
        "phone_e164": _norm(profile.get("phone_e164")),
        "phone_country_iso2": _norm(profile.get("phone_country_iso2")),
        "phone_country_code": _norm(profile.get("phone_country_code")),
        "phone_national_number": _norm(profile.get("phone_national_number")),
        # Location
        "location_city": _norm(profile.get("location_city")),
        "location_state": _norm(profile.get("location_state")),
        "location_postal_code": _norm(profile.get("location_postal_code")),
        "location_country": _norm(profile.get("location_country")),
        # Links
        "linkedin_url": _norm(profile.get("linkedin_url")),
        "github_url": _norm(profile.get("github_url")),
        "portfolio_url": _norm(profile.get("portfolio_url")),
        # Work eligibility
        "work_authorization": _norm(profile.get("work_authorization")),
        "requires_sponsorship": _norm(profile.get("requires_sponsorship")),
    }

    # Reusable vault answers, order-independent.
    answers = []
    for answer in vault_answers or []:
        answers.append(
            {
                "k": _norm(answer.get("canonical_key")),
                "v": _norm(answer.get("value")),
                "verified": _norm(answer.get("is_user_verified") or answer.get("verified")),
                "scope": _norm(answer.get("scope")),
                "company": _norm(answer.get("company_key")),
            }
        )
    payload["answers"] = sorted(answers, key=lambda a: (a["k"], a["scope"], a["company"]))
    payload["documents"] = _norm(document_ids or {})

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
