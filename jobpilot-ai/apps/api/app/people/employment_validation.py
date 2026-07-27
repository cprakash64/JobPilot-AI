from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.people.schemas import JobPeopleSearchProfile, ProviderPerson
from app.people.security import safe_profile_url

EMPLOYMENT_VALIDATION_VERSION = "people-employment-v2.1"
EMPLOYMENT_EVIDENCE_VERSION = "people-employment-evidence-v1"

EmploymentValidationStatus = Literal[
    "confirmed_exact_company_verified",
    "exact_company_current_but_unverified_freshness",
    "confirmed_related_company",
    "conflicting_current_employment",
    "former_employee",
    "stale_or_uncertain",
    "insufficient_evidence",
]


class EmploymentValidationResult(BaseModel):
    status: EmploymentValidationStatus
    confidence: float = Field(ge=0, le=1)
    verified_at: datetime | None = None
    provider_record_observed_at: datetime | None = None
    provider_employment_updated_at: datetime | None = None
    verification_provider: str | None = None
    exact_company: bool = False
    conflicting_employer: bool = False
    identity_strong: bool = False
    rejection_codes: list[str] = Field(default_factory=list)
    credits_consumed: int = 0
    version: str = EMPLOYMENT_VALIDATION_VERSION
    evidence_version: str = EMPLOYMENT_EVIDENCE_VERSION


def _normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _identity_is_strong(person: ProviderPerson) -> bool:
    if not person.provider_person_id:
        return False
    if safe_profile_url(person.linkedin_url):
        return True
    tokens = [
        token
        for token in re.findall(r"[A-Za-z]+", person.full_name)
        if len(token) > 1 and "*" not in token
    ]
    return len(tokens) >= 2


def _is_former_employee(
    person: ProviderPerson, profile: JobPeopleSearchProfile
) -> bool:
    target = _normalize(profile.company_name)
    aliases = {_normalize(value) for value in profile.company_aliases}
    aliases.add(target)
    return any(_normalize(value) in aliases for value in person.previous_employers)


def validate_current_employment(
    person: ProviderPerson,
    profile: JobPeopleSearchProfile,
    *,
    prior_observations: list[dict] | None = None,
    revalidation_required: bool = False,
    revalidation_required_since: datetime | None = None,
    now: datetime | None = None,
) -> EmploymentValidationResult:
    """Conservatively validate current employment without scraping profiles."""
    now = now or datetime.now(UTC)
    verified_at = _aware(person.employment_verified_at)
    employment_updated_at = _aware(person.provider_employment_updated_at)
    provider_observed_at = _aware(person.provider_record_observed_at)
    evidence_at = verified_at or employment_updated_at or provider_observed_at
    identity_strong = _identity_is_strong(person)
    current_domain = (person.current_company_domain or "").lower().strip()
    expected_domain = (profile.company_domain or "").lower().strip()
    related_domain = (profile.parent_company_domain or "").lower().strip()

    if not identity_strong:
        return EmploymentValidationResult(
            status="insufficient_evidence",
            confidence=0.2,
            verified_at=evidence_at,
            verification_provider=person.provider,
            identity_strong=False,
            rejection_codes=["identity_mismatch"],
        )
    revalidation_since = _aware(revalidation_required_since)
    if revalidation_required and (
        evidence_at is None
        or revalidation_since is None
        or evidence_at <= revalidation_since
    ):
        return EmploymentValidationResult(
            status="conflicting_current_employment",
            confidence=0.1,
            verified_at=evidence_at,
            verification_provider=person.provider,
            conflicting_employer=True,
            identity_strong=True,
            rejection_codes=["current_employment_conflict"],
        )

    newer_conflict = False
    for observation in prior_observations or []:
        observed_domain = str(observation.get("company_domain") or "").lower()
        observed_at_raw = (
            observation.get("employment_verified_at")
            or observation.get("provider_employment_updated_at")
            or observation.get("provider_record_observed_at")
            or observation.get("verified_at")
        )
        try:
            conflict_observed_at = _aware(datetime.fromisoformat(str(observed_at_raw)))
        except (TypeError, ValueError):
            conflict_observed_at = None
        if (
            observed_domain
            and expected_domain
            and observed_domain != expected_domain
            and conflict_observed_at
            and (evidence_at is None or conflict_observed_at > evidence_at)
        ):
            newer_conflict = True
            break
    if newer_conflict:
        return EmploymentValidationResult(
            status="conflicting_current_employment",
            confidence=0.15,
            verified_at=evidence_at,
            verification_provider=person.provider,
            conflicting_employer=True,
            identity_strong=True,
            rejection_codes=["current_employment_conflict"],
        )

    if not expected_domain:
        return EmploymentValidationResult(
            status="insufficient_evidence",
            confidence=0.2,
            verified_at=evidence_at,
            verification_provider=person.provider,
            identity_strong=True,
            rejection_codes=["insufficient_employment_evidence"],
        )
    if related_domain and current_domain == related_domain and current_domain != expected_domain:
        return EmploymentValidationResult(
            status="confirmed_related_company",
            confidence=0.45,
            verified_at=evidence_at,
            verification_provider=person.provider,
            identity_strong=True,
            rejection_codes=["related_company_only"],
        )
    if expected_domain and current_domain != expected_domain:
        if _is_former_employee(person, profile):
            return EmploymentValidationResult(
                status="former_employee",
                confidence=0.9,
                verified_at=evidence_at,
                verification_provider=person.provider,
                identity_strong=True,
                rejection_codes=["former_employee"],
            )
        return EmploymentValidationResult(
            status="insufficient_evidence",
            confidence=0.25,
            verified_at=evidence_at,
            verification_provider=person.provider,
            identity_strong=True,
            rejection_codes=["exact_company_not_confirmed"],
        )

    if person.current_role_indicator is False:
        return EmploymentValidationResult(
            status="former_employee",
            confidence=0.9,
            verified_at=evidence_at,
            verification_provider=person.provider,
            identity_strong=True,
            rejection_codes=["former_employee"],
        )
    if evidence_at is None or evidence_at < now - timedelta(
        days=settings.people_employment_freshness_days
    ):
        return EmploymentValidationResult(
            status="stale_or_uncertain",
            confidence=0.35,
            verified_at=evidence_at,
            verification_provider=person.provider,
            exact_company=bool(expected_domain and current_domain == expected_domain),
            identity_strong=True,
            rejection_codes=[
                "stale_employment_record"
                if evidence_at
                else "insufficient_employment_evidence"
            ],
        )
    if verified_at is not None:
        return EmploymentValidationResult(
            status="confirmed_exact_company_verified",
            confidence=0.98,
            verified_at=verified_at,
            verification_provider=person.provider,
            exact_company=True,
            identity_strong=True,
        )
    return EmploymentValidationResult(
        status="exact_company_current_but_unverified_freshness",
        confidence=0.78 if employment_updated_at else 0.68,
        provider_record_observed_at=provider_observed_at,
        provider_employment_updated_at=employment_updated_at,
        verification_provider=person.provider,
        exact_company=True,
        identity_strong=True,
    )
