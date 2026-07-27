from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.models.entities import (
    Education,
    Experience,
    JobPeopleCandidate,
    JobPosting,
    PeopleDiscoveryRun,
    PeopleEmploymentVerificationRun,
    PeopleRecommendationFeedback,
    ProfessionalPerson,
    ProfessionalPersonSource,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
)
from app.people.employment_validation import (
    EMPLOYMENT_EVIDENCE_VERSION,
    EMPLOYMENT_VALIDATION_VERSION,
    EmploymentValidationResult,
    validate_current_employment,
)
from app.people.feature_flags import is_beta
from app.people.intelligence import extract_job_people_profile
from app.people.observability import metric
from app.people.providers import (
    PDLPeopleProvider,
    ProviderUnavailable,
    get_email_provider,
    get_people_provider,
)
from app.people.schemas import (
    FeedbackRequest,
    OutreachDraftRequest,
    PeopleCategory,
    PeopleSearchQuery,
    PersonEnrichmentRequest,
    ProviderPerson,
    WorkEmailRequest,
)
from app.people.scoring import (
    SCORING_VERSION,
    candidate_rejection_reasons,
    confidence,
    confidence_label,
    explanations,
    normalize_text,
    score_candidate,
)
from app.people.security import (
    decrypt_email,
    email_hash,
    encrypt_email,
    is_professional_email,
    safe_profile_url,
)
from app.people.title_ontology import (
    TitleGroup,
    is_early_career_job,
    manager_title_groups,
    recruiter_title_groups,
)

logger = logging.getLogger("jobpilot.people")

DiscoveryStrategy = Literal["exact", "broadened"]
DISCOVERY_STRATEGY_VERSION = "people-discovery-v3"
DISPLAYABLE_EMPLOYMENT_STATUSES = frozenset(
    {
        "confirmed_exact_company_verified",
        "exact_company_current_but_unverified_freshness",
    }
)

_SAFE_PROVIDER_MESSAGES = {
    "provider_unauthorized": "The people data provider credentials could not be verified.",
    "provider_forbidden": "The configured provider account does not have access to people search.",
    "provider_rate_limited": "The people data provider rate limit has been reached.",
    "provider_timeout": "The people search provider took too long to respond.",
    "provider_circuit_open": "People search is temporarily paused after repeated provider failures.",
    "provider_schema_error": "The people provider returned an unsupported response.",
}


def _safe_provider_message(reason: str) -> str:
    return _SAFE_PROVIDER_MESSAGES.get(
        reason, "Professional data providers are temporarily unavailable."
    )


def _log_provider_failure(exc: ProviderUnavailable, discovery_run_id: int) -> None:
    logger.warning(
        "people_provider_failure reason=%s provider=%s http_status=%s duration_ms=%s discovery_run_id=%s",
        exc.reason,
        exc.provider,
        exc.http_status if exc.http_status is not None else "none",
        round(exc.duration_ms, 2) if exc.duration_ms is not None else "none",
        discovery_run_id,
    )
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = Lock()


def rate_limit(key: str, limit: int, window_seconds: int = 3600) -> None:
    if limit <= 0:
        return
    now = datetime.now(UTC).timestamp()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "PEOPLE_RATE_LIMITED", "message": "Please try again later."},
            )
        bucket.append(now)


def query_fingerprint(
    job: JobPosting, strategy: DiscoveryStrategy = "exact"
) -> str:
    profile = extract_job_people_profile(job)
    payload = profile.model_dump(mode="json")
    payload["scoring_version"] = SCORING_VERSION
    payload["discovery_strategy_version"] = DISCOVERY_STRATEGY_VERSION
    payload["discovery_strategy"] = strategy
    payload["employment_validation_version"] = EMPLOYMENT_VALIDATION_VERSION
    payload["employment_evidence_version"] = EMPLOYMENT_EVIDENCE_VERSION
    # Changing this flag invalidates cached no-match/unresolved runs. Existing
    # fresh displayable candidates are still returned before fingerprint lookup.
    payload["secondary_employment_verification_enabled"] = (
        settings.people_employment_secondary_verification_enabled
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _job_or_404(db: Session, job_id: int) -> JobPosting:
    job = db.get(JobPosting, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _fresh_candidates(db: Session, job_id: int) -> list[JobPeopleCandidate]:
    return list(
        db.scalars(
            select(JobPeopleCandidate).where(
                JobPeopleCandidate.job_id == job_id,
                JobPeopleCandidate.expires_at > _now(),
                JobPeopleCandidate.scoring_version == SCORING_VERSION,
                JobPeopleCandidate.employment_validation_version
                == EMPLOYMENT_VALIDATION_VERSION,
                JobPeopleCandidate.employment_validation_status.in_(
                    DISPLAYABLE_EMPLOYMENT_STATUSES
                ),
            )
        )
    )


def _fresh_no_match_run(
    db: Session,
    *,
    job_id: int,
    user_id: int,
    fingerprint: str,
) -> PeopleDiscoveryRun | None:
    cutoff = _now() - timedelta(days=settings.people_result_ttl_days)
    return db.scalar(
        select(PeopleDiscoveryRun)
        .where(
            PeopleDiscoveryRun.job_id == job_id,
            PeopleDiscoveryRun.user_id == user_id,
            PeopleDiscoveryRun.query_fingerprint == fingerprint,
            PeopleDiscoveryRun.status == "complete",
            PeopleDiscoveryRun.completed_at.is_not(None),
            PeopleDiscoveryRun.completed_at > cutoff,
        )
        .order_by(PeopleDiscoveryRun.completed_at.desc())
    )


def _latest_run(
    db: Session,
    *,
    job_id: int,
    user_id: int,
    fingerprints: list[str] | None = None,
) -> PeopleDiscoveryRun | None:
    statement = select(PeopleDiscoveryRun).where(
        PeopleDiscoveryRun.job_id == job_id,
        PeopleDiscoveryRun.user_id == user_id,
    )
    if fingerprints is not None:
        statement = statement.where(
            PeopleDiscoveryRun.query_fingerprint.in_(fingerprints)
        )
    return db.scalar(
        statement.order_by(
            PeopleDiscoveryRun.started_at.desc(),
            PeopleDiscoveryRun.id.desc(),
        )
    )


def _normalized_linkedin(value: str | None) -> str | None:
    return safe_profile_url(value)


def _same_identity(left: ProviderPerson, right: ProviderPerson) -> bool:
    left_url, right_url = _normalized_linkedin(left.linkedin_url), _normalized_linkedin(right.linkedin_url)
    if left_url and right_url:
        return left_url == right_url
    if left.provider == right.provider and left.provider_person_id == right.provider_person_id:
        return True
    # Conservative fallback: exact normalized name + domain + title. Never name alone.
    return bool(
        normalize_text(left.full_name) == normalize_text(right.full_name)
        and left.current_company_domain
        and left.current_company_domain == right.current_company_domain
        and normalize_text(left.current_title) == normalize_text(right.current_title)
    )


def deduplicate(people: list[ProviderPerson]) -> list[ProviderPerson]:
    result: list[ProviderPerson] = []
    for person in people:
        if not any(_same_identity(person, existing) for existing in result):
            result.append(person)
    return result


def _shared_evidence(
    db: Session, user_id: int, person: ProviderPerson
) -> tuple[str | None, str | None]:
    if not settings.people_network_matching_enabled:
        return None, None
    user_schools = {
        normalize_text(row.school): row.school
        for row in db.scalars(select(Education).where(Education.user_id == user_id))
    }
    user_employers = {
        normalize_text(row.company): row.company
        for row in db.scalars(select(Experience).where(Experience.user_id == user_id))
    }
    school = next((user_schools[n] for item in person.education if (n := normalize_text(item)) in user_schools), None)
    employer = next(
        (user_employers[n] for item in person.previous_employers if (n := normalize_text(item)) in user_employers),
        None,
    )
    return school, employer


def _person_for_provider(db: Session, value: ProviderPerson) -> ProfessionalPerson:
    linkedin = _normalized_linkedin(value.linkedin_url)
    person = db.scalar(
        select(ProfessionalPersonSource)
        .where(
            ProfessionalPersonSource.provider == value.provider,
            ProfessionalPersonSource.provider_person_id == value.provider_person_id,
        )
    )
    canonical = db.get(ProfessionalPerson, person.person_id) if person else None
    if canonical is None and linkedin:
        canonical = db.scalar(
            select(ProfessionalPerson).where(ProfessionalPerson.linkedin_url_normalized == linkedin)
        )
    if canonical is None:
        canonical = db.scalar(
            select(ProfessionalPerson).where(
                ProfessionalPerson.normalized_full_name == normalize_text(value.full_name),
                ProfessionalPerson.current_company_domain == value.current_company_domain,
                ProfessionalPerson.normalized_title == normalize_text(value.current_title),
            )
        )
    if canonical is None:
        canonical = ProfessionalPerson(
            canonical_full_name=value.full_name[:255],
            normalized_full_name=normalize_text(value.full_name)[:255],
            current_company_name=value.current_company_name[:255],
            current_company_domain=value.current_company_domain,
            current_title=value.current_title[:255],
            normalized_title=normalize_text(value.current_title)[:255],
            department=(value.department or "")[:120] or None,
            seniority=(value.seniority or "")[:80] or None,
            professional_location=(value.location or "")[:255] or None,
            linkedin_url=linkedin,
            linkedin_url_normalized=linkedin,
            employment_last_verified_at=value.employment_verified_at,
        )
        db.add(canonical)
        db.flush()
    else:
        # Prefer fresher provider evidence; never overwrite with missing values.
        incoming = (
            value.employment_verified_at
            or value.provider_employment_updated_at
            or value.provider_record_observed_at
            or value.source_last_updated_at
        )
        current = canonical.updated_at
        if incoming and (
            not current or incoming.replace(tzinfo=UTC) >= current.replace(tzinfo=UTC)
        ):
            prior_company_domain = canonical.current_company_domain
            canonical.current_company_name = value.current_company_name[:255]
            canonical.current_company_domain = value.current_company_domain or canonical.current_company_domain
            canonical.current_title = value.current_title[:255]
            canonical.normalized_title = normalize_text(value.current_title)[:255]
            canonical.department = value.department or canonical.department
            canonical.seniority = value.seniority or canonical.seniority
            canonical.professional_location = value.location or canonical.professional_location
            if value.employment_verified_at:
                canonical.employment_last_verified_at = (
                    value.employment_verified_at
                )
            elif (
                prior_company_domain
                and value.current_company_domain
                and prior_company_domain != value.current_company_domain
            ):
                canonical.employment_last_verified_at = None
    source = db.scalar(
        select(ProfessionalPersonSource).where(
            ProfessionalPersonSource.provider == value.provider,
            ProfessionalPersonSource.provider_person_id == value.provider_person_id,
        )
    )
    if source is None:
        source = ProfessionalPersonSource(
            person_id=canonical.id,
            provider=value.provider,
            provider_person_id=value.provider_person_id[:255],
            source_profile_url=safe_profile_url(value.source_profile_url),
            source_last_updated_at=value.source_last_updated_at,
            provider_record_observed_at=value.provider_record_observed_at,
            provider_employment_updated_at=value.provider_employment_updated_at,
            employment_verified_at=value.employment_verified_at,
            employment_source=value.employment_source,
            exact_company_match=value.exact_company_match,
            current_role_indicator=value.current_role_indicator,
            conflicting_employer_observed_at=(
                value.conflicting_employer_observed_at
            ),
            normalized_evidence=value.evidence,
            field_provenance=value.field_provenance,
            redacted_payload={},
        )
        db.add(source)
    else:
        prior = source.normalized_evidence if isinstance(source.normalized_evidence, dict) else {}
        observations = list(prior.get("employment_observations") or [])[-9:]
        previous_snapshot = {
            "company_name": prior.get("current_company_name"),
            "company_domain": prior.get("current_company_domain"),
            "title": prior.get("current_title"),
            "verified_at": prior.get("employment_verified_at"),
            "provider_record_observed_at": prior.get(
                "provider_record_observed_at"
            ),
            "provider_employment_updated_at": prior.get(
                "provider_employment_updated_at"
            ),
        }
        incoming_snapshot = {
            "company_name": value.evidence.get("current_company_name"),
            "company_domain": value.evidence.get("current_company_domain"),
            "title": value.evidence.get("current_title"),
            "verified_at": value.evidence.get("employment_verified_at"),
            "provider_record_observed_at": value.evidence.get(
                "provider_record_observed_at"
            ),
            "provider_employment_updated_at": value.evidence.get(
                "provider_employment_updated_at"
            ),
        }
        if any(previous_snapshot.values()) and previous_snapshot != incoming_snapshot:
            observations.append(previous_snapshot)
        source.normalized_evidence = {
            **value.evidence,
            "employment_observations": observations,
        }
        source.field_provenance = value.field_provenance
        source.source_last_updated_at = value.source_last_updated_at
        source.provider_record_observed_at = value.provider_record_observed_at
        source.provider_employment_updated_at = (
            value.provider_employment_updated_at
        )
        source.employment_verified_at = value.employment_verified_at
        source.employment_source = value.employment_source
        source.exact_company_match = value.exact_company_match
        source.current_role_indicator = value.current_role_indicator
        source.conflicting_employer_observed_at = (
            value.conflicting_employer_observed_at
        )
    return canonical


def _employment_observations(
    db: Session, value: ProviderPerson
) -> tuple[list[dict], bool, datetime | None]:
    source = db.scalar(
        select(ProfessionalPersonSource).where(
            ProfessionalPersonSource.provider == value.provider,
            ProfessionalPersonSource.provider_person_id == value.provider_person_id,
        )
    )
    canonical = db.get(ProfessionalPerson, source.person_id) if source else None
    linkedin = _normalized_linkedin(value.linkedin_url)
    if canonical is None and linkedin:
        canonical = db.scalar(
            select(ProfessionalPerson).where(
                ProfessionalPerson.linkedin_url_normalized == linkedin
            )
        )
    if canonical is None:
        return [], False, None
    observations: list[dict] = []
    for existing_source in db.scalars(
        select(ProfessionalPersonSource).where(
            ProfessionalPersonSource.person_id == canonical.id
        )
    ):
        evidence = (
            existing_source.normalized_evidence
            if isinstance(existing_source.normalized_evidence, dict)
            else {}
        )
        snapshot = {
            "company_name": evidence.get("current_company_name"),
            "company_domain": evidence.get("current_company_domain"),
            "title": evidence.get("current_title"),
            "employment_verified_at": (
                existing_source.employment_verified_at.isoformat()
                if existing_source.employment_verified_at
                else evidence.get("employment_verified_at")
            ),
            "provider_record_observed_at": (
                existing_source.provider_record_observed_at.isoformat()
                if existing_source.provider_record_observed_at
                else evidence.get("provider_record_observed_at")
            ),
            "provider_employment_updated_at": (
                existing_source.provider_employment_updated_at.isoformat()
                if existing_source.provider_employment_updated_at
                else evidence.get("provider_employment_updated_at")
            ),
            "provider": existing_source.provider,
        }
        if any(snapshot.values()):
            observations.append(snapshot)
        observations.extend(
            item
            for item in (evidence.get("employment_observations") or [])
            if isinstance(item, dict)
        )
    return (
        observations,
        canonical.employment_revalidation_required,
        canonical.employment_conflict_detected_at,
    )


def _validate_employment(
    db: Session, person: ProviderPerson, profile
) -> EmploymentValidationResult:
    observations, revalidation_required, revalidation_required_since = (
        _employment_observations(db, person)
    )
    return validate_current_employment(
        person,
        profile,
        prior_observations=observations,
        revalidation_required=revalidation_required,
        revalidation_required_since=revalidation_required_since,
    )


async def _secondary_employment_validation(
    db: Session,
    user_id: int,
    job_id: int,
    discovery_run_id: int,
    person: ProviderPerson,
    profile,
    category: PeopleCategory,
) -> tuple[EmploymentValidationResult, ProviderPerson | None] | None:
    cache_key = hashlib.sha256(
        (
            f"{person.provider}:{person.provider_person_id}:"
            f"{profile.company_domain}:{EMPLOYMENT_EVIDENCE_VERSION}"
        ).encode()
    ).hexdigest()
    cached = db.scalar(
        select(PeopleEmploymentVerificationRun)
        .where(
            PeopleEmploymentVerificationRun.cache_key_hash == cache_key,
            PeopleEmploymentVerificationRun.verification_version
            == EMPLOYMENT_EVIDENCE_VERSION,
            PeopleEmploymentVerificationRun.expires_at > _now(),
        )
        .order_by(PeopleEmploymentVerificationRun.completed_at.desc())
    )
    if cached is not None:
        return _cached_verification_result(cached), None

    budget_reason = _employment_verification_budget_reason(db, user_id)
    if budget_reason is not None:
        return None
    with _redis_lock(job_id, cache_key, namespace="employment-verify") as acquired:
        if not acquired:
            return None
        provider = PDLPeopleProvider()
        result_status = "insufficient_evidence"
        credits = 0
        match: ProviderPerson | None = None
        try:
            rows = await provider.search_people(
                PeopleSearchQuery(
                    category=category,
                    company_name=profile.company_name,
                    company_domain=profile.company_domain,
                    company_aliases=profile.company_aliases,
                    titles=[person.current_title],
                    title_group="employment_verification",
                    seniorities=[],
                    role_family=profile.role_family,
                    department=profile.department,
                    location=profile.location,
                    location_filter_mode="none",
                    company_match_kind="canonical",
                    limit=5,
                )
            )
            match = next(
                (
                    row
                    for row in rows
                    if (
                        safe_profile_url(row.linkedin_url)
                        and safe_profile_url(row.linkedin_url)
                        == safe_profile_url(person.linkedin_url)
                    )
                    or (
                        normalize_text(row.full_name)
                        == normalize_text(person.full_name)
                        and normalize_text(row.current_title)
                        == normalize_text(person.current_title)
                    )
                ),
                None,
            )
            usage = await provider.get_usage()
            credits = usage.credits_used
            if match is None:
                result = EmploymentValidationResult(
                    status="insufficient_evidence",
                    confidence=0.3,
                    identity_strong=True,
                    rejection_codes=["insufficient_employment_evidence"],
                )
            elif match.current_company_domain != profile.company_domain:
                match.conflicting_employer_observed_at = (
                    match.provider_record_observed_at or _now()
                )
                result = EmploymentValidationResult(
                    status="conflicting_current_employment",
                    confidence=0.1,
                    verified_at=match.conflicting_employer_observed_at,
                    conflicting_employer=True,
                    identity_strong=True,
                    rejection_codes=["current_employment_conflict"],
                )
            else:
                match.exact_company_match = True
                base = validate_current_employment(match, profile)
                if base.status in DISPLAYABLE_EMPLOYMENT_STATUSES:
                    result = base.model_copy(
                        update={
                            "status": "confirmed_exact_company_verified",
                            "confidence": 0.98,
                            "verified_at": _now(),
                            "rejection_codes": [],
                        }
                    )
                else:
                    result = base
            result_status = result.status
        except ProviderUnavailable as exc:
            result = EmploymentValidationResult(
                status="stale_or_uncertain",
                confidence=0.3,
                identity_strong=True,
                rejection_codes=["insufficient_employment_evidence"],
            )
            result_status = exc.reason
            _log_provider_failure(exc, discovery_run_id)
        expires = _now() + timedelta(
            seconds=(
                _provider_retry_seconds(result_status)
                if result_status.startswith("provider_")
                else settings.people_employment_verification_ttl_days * 86400
            )
        )
        db.add(
            PeopleEmploymentVerificationRun(
                job_id=job_id,
                user_id=user_id,
                discovery_run_id=discovery_run_id,
                category=category,
                cache_key_hash=cache_key,
                verification_version=EMPLOYMENT_EVIDENCE_VERSION,
                status=result_status,
                credits_used=credits,
                completed_at=_now(),
                expires_at=expires,
            )
        )
        return (
            result.model_copy(
                update={
                    "verification_provider": "secondary_licensed_provider",
                    "credits_consumed": credits,
                }
            ),
            match,
        )


def _cached_verification_result(
    cached: PeopleEmploymentVerificationRun,
) -> EmploymentValidationResult:
    if cached.status == "confirmed_exact_company_verified":
        return EmploymentValidationResult(
            status="confirmed_exact_company_verified",
            confidence=0.98,
            verified_at=cached.completed_at,
            exact_company=True,
            identity_strong=True,
        )
    if cached.status == "conflicting_current_employment":
        return EmploymentValidationResult(
            status="conflicting_current_employment",
            confidence=0.1,
            verified_at=cached.completed_at,
            conflicting_employer=True,
            identity_strong=True,
            rejection_codes=["current_employment_conflict"],
        )
    return EmploymentValidationResult(
        status="stale_or_uncertain",
        confidence=0.3,
        verified_at=cached.completed_at,
        identity_strong=True,
        rejection_codes=["insufficient_employment_evidence"],
    )


def _employment_verification_budget_reason(
    db: Session, user_id: int
) -> str | None:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    global_used = db.scalar(
        select(
            func.coalesce(
                func.sum(PeopleEmploymentVerificationRun.credits_used), 0
            )
        ).where(PeopleEmploymentVerificationRun.started_at >= start)
    ) or 0
    user_used = db.scalar(
        select(
            func.coalesce(
                func.sum(PeopleEmploymentVerificationRun.credits_used), 0
            )
        ).where(
            PeopleEmploymentVerificationRun.user_id == user_id,
            PeopleEmploymentVerificationRun.started_at >= start,
        )
    ) or 0
    if (
        settings.people_employment_verification_daily_credit_budget > 0
        and global_used
        >= settings.people_employment_verification_daily_credit_budget
    ):
        return "provider_budget_exceeded"
    if (
        settings.people_employment_verification_per_user_daily_limit > 0
        and user_used
        >= settings.people_employment_verification_per_user_daily_limit
    ):
        return "provider_user_limit_exceeded"
    return None


def _provider_retry_seconds(reason: str) -> int:
    return {
        "provider_circuit_open": 60,
        "provider_rate_limited": 60,
        "provider_timeout": 30,
        "provider_network_error": 30,
    }.get(reason, 300)


def _ensure_recommendation(
    db: Session,
    user_id: int,
    candidate: JobPeopleCandidate,
    school: str | None,
    employer: str | None,
) -> UserJobPeopleRecommendation:
    recommendation = db.scalar(
        select(UserJobPeopleRecommendation).where(
            UserJobPeopleRecommendation.user_id == user_id,
            UserJobPeopleRecommendation.job_id == candidate.job_id,
            UserJobPeopleRecommendation.job_people_candidate_id == candidate.id,
        )
    )
    if recommendation is None:
        reasons = []
        if school:
            reasons.append(f"Shared school: {school}")
        if employer:
            reasons.append(f"Shared previous employer: {employer}")
        recommendation = UserJobPeopleRecommendation(
            user_id=user_id,
            job_id=candidate.job_id,
            job_people_candidate_id=candidate.id,
            relationship_type="relevant" if reasons else None,
            shared_school=school,
            shared_employer=employer,
            connection_strength=min(1.0, 0.55 * bool(school) + 0.45 * bool(employer)),
            personalized_reasons=reasons,
            personalized_score=candidate.category_score,
        )
        db.add(recommendation)
        db.flush()
    return recommendation


def _budget_check(db: Session, user_id: int) -> None:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    global_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0)).where(
            PeopleDiscoveryRun.started_at >= start,
            PeopleDiscoveryRun.provider != "hunter",
        )
    ) or 0
    user_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0)).where(
            PeopleDiscoveryRun.user_id == user_id,
            PeopleDiscoveryRun.started_at >= start,
            PeopleDiscoveryRun.provider != "hunter",
        )
    ) or 0
    if settings.people_daily_credit_budget and global_used >= settings.people_daily_credit_budget:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_GLOBAL_BUDGET_EXCEEDED",
                "message": "People discovery is temporarily unavailable because today's usage limit was reached.",
                "availability_reason": "provider_budget_exceeded",
                "retryable": False,
            },
        )
    if settings.people_per_user_daily_limit and user_used >= settings.people_per_user_daily_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_USER_BUDGET_EXCEEDED",
                "message": "People discovery is temporarily unavailable because your daily usage limit was reached.",
                "availability_reason": "provider_user_limit_exceeded",
                "retryable": False,
            },
        )


def _email_budget_exceeded(db: Session, user_id: int) -> bool:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    global_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0))
        .where(
            PeopleDiscoveryRun.provider == "hunter",
            PeopleDiscoveryRun.started_at >= start,
        )
    ) or 0
    user_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0))
        .where(
            PeopleDiscoveryRun.provider == "hunter",
            PeopleDiscoveryRun.user_id == user_id,
            PeopleDiscoveryRun.started_at >= start,
        )
    ) or 0
    return bool(
        (
            settings.people_email_daily_credit_budget > 0
            and settings.people_email_daily_credit_budget <= global_used
        )
        or (
            settings.people_email_per_user_daily_limit > 0
            and settings.people_email_per_user_daily_limit <= user_used
        )
    )


_PEOPLE_CATEGORIES: tuple[PeopleCategory, ...] = (
    "likely_recruiter",
    "potential_hiring_manager",
    "potential_referrer",
)


def _category_threshold(category: PeopleCategory) -> float:
    return {
        "likely_recruiter": settings.people_min_recruiter_relevance,
        "potential_hiring_manager": settings.people_min_manager_relevance,
        "potential_referrer": settings.people_min_referrer_relevance,
    }[category]


def _employment_verification_cap(category: PeopleCategory) -> int:
    return {
        "likely_recruiter": (
            settings.people_employment_verification_max_recruiters
        ),
        "potential_hiring_manager": (
            settings.people_employment_verification_max_managers
        ),
        "potential_referrer": (
            settings.people_employment_verification_max_referrers
        ),
    }[category]


def _score_distribution(scores: list[float]) -> dict[str, int | float | None]:
    buckets = {"0_39": 0, "40_59": 0, "60_79": 0, "80_100": 0}
    for score in scores:
        if score < 40:
            buckets["0_39"] += 1
        elif score < 60:
            buckets["40_59"] += 1
        elif score < 80:
            buckets["60_79"] += 1
        else:
            buckets["80_100"] += 1
    return {
        "minimum": min(scores) if scores else None,
        "maximum": max(scores) if scores else None,
        "buckets": buckets,
    }


def build_category_search_queries(
    profile, category: PeopleCategory, *, related_company: bool = False
) -> list[PeopleSearchQuery]:
    domain = profile.parent_company_domain if related_company else profile.company_domain
    company_kind = "related" if related_company else "canonical"
    if category == "likely_recruiter":
        groups = recruiter_title_groups(
            early_career=is_early_career_job(profile.job_title)
        )
        location_mode = "soft"
    elif category == "potential_hiring_manager":
        groups = manager_title_groups(profile.role_family, profile.job_title)
        location_mode = "soft"
    else:
        midpoint = max(1, len(profile.team_member_titles) // 2)
        groups = [
            TitleGroup("exact_role_family", profile.team_member_titles[:midpoint], []),
            TitleGroup("adjacent_role_family", profile.team_member_titles[midpoint:], []),
        ]
        location_mode = "soft"
    return [
        PeopleSearchQuery(
            category=category,
            company_name=profile.company_name,
            company_domain=domain,
            company_aliases=profile.company_aliases,
            titles=group.titles,
            title_group=group.name,
            seniorities=group.seniorities,
            role_family=profile.role_family,
            department=profile.department,
            location=profile.location,
            location_filter_mode=location_mode,
            company_match_kind=company_kind,
            limit=settings.people_max_discovery_results_per_category,
        )
        for group in groups
        if group.titles and domain
    ]


def build_broadened_search_queries(
    profile, category: PeopleCategory
) -> list[PeopleSearchQuery]:
    """Return only bounded secondary queries.

    The exact strategy already ran the primary title groups. A user-triggered
    broaden therefore adds new canonical-title groups plus the normal groups
    against an evidence-backed related domain, without automatically repeating
    the paid exact-company search.
    """
    if category == "likely_recruiter":
        broader_groups = [
            TitleGroup(
                "broader_recruiting",
                [
                    "Recruiter",
                    "Talent Acquisition Specialist",
                    "Recruiting Lead",
                    "Early Talent Partner",
                ],
                [],
            )
        ]
    elif category == "potential_hiring_manager":
        broader_groups = [
            TitleGroup(
                "broader_engineering_leadership",
                [
                    "Engineering Director",
                    "Technical Director",
                    "VP Engineering",
                ],
                ["director", "head", "vp"],
            ),
            TitleGroup(
                "broader_technical_leadership",
                ["Technical Lead", "Engineering Lead"],
                ["manager"],
            ),
        ]
    else:
        broader_groups = [
            TitleGroup(
                "broader_role_family",
                [
                    "Software Engineer",
                    "Software Developer",
                    "Data Scientist",
                    "Platform Engineer",
                ],
                [],
            )
        ]

    queries = [
        PeopleSearchQuery(
            category=category,
            company_name=profile.company_name,
            company_domain=profile.company_domain,
            company_aliases=profile.company_aliases,
            titles=group.titles,
            title_group=group.name,
            seniorities=group.seniorities,
            role_family=profile.role_family,
            department=profile.department,
            location=profile.location,
            location_filter_mode="soft",
            company_match_kind="canonical",
            limit=settings.people_max_discovery_results_per_category,
        )
        for group in broader_groups
        if profile.company_domain
    ]
    if profile.parent_company_domain and profile.domain_confidence >= 0.8:
        queries.extend(
            build_category_search_queries(
                profile, category, related_company=True
            )
        )
    return queries


PreliminaryCandidate = tuple[
    float, PeopleCategory, ProviderPerson, str | None, str | None
]


def allocate_enrichment_targets(
    candidates: dict[PeopleCategory, list[PreliminaryCandidate]],
    *,
    total: int,
    reservations: dict[PeopleCategory, int],
) -> list[PreliminaryCandidate]:
    selected: list[PreliminaryCandidate] = []
    selected_keys: set[tuple[PeopleCategory, str, str]] = set()
    for category in _PEOPLE_CATEGORIES:
        rows = sorted(candidates.get(category, []), key=lambda item: item[0], reverse=True)
        for item in rows[: max(0, reservations.get(category, 0))]:
            key = (category, item[2].provider, item[2].provider_person_id)
            if key not in selected_keys and len(selected) < total:
                selected.append(item)
                selected_keys.add(key)
    remaining = sorted(
        (
            item
            for category in _PEOPLE_CATEGORIES
            for item in candidates.get(category, [])
            if (category, item[2].provider, item[2].provider_person_id) not in selected_keys
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    for item in remaining:
        if len(selected) >= total:
            break
        key = (item[1], item[2].provider, item[2].provider_person_id)
        if key not in selected_keys:
            selected.append(item)
            selected_keys.add(key)
    return selected


@contextmanager
def _redis_lock(
    job_id: int, fingerprint: str, *, namespace: str = "discover"
) -> Iterator[bool]:
    client = None
    key = f"people:{namespace}:{job_id}:{fingerprint}"
    token = hashlib.sha256(f"{key}:{_now().isoformat()}".encode()).hexdigest()
    acquired = True
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        acquired = bool(client.set(key, token, nx=True, ex=120))
    except Exception:
        client = None
    try:
        yield acquired
    finally:
        if client is not None and acquired:
            try:
                if client.get(key) == token.encode():
                    client.delete(key)
            except Exception:
                pass


async def discover(
    db: Session,
    user: User,
    job_id: int,
    strategy: DiscoveryStrategy = "exact",
) -> dict:
    started = time.monotonic()
    metric(
        "people_discovery_requests_total",
        provider=settings.people_primary_provider,
        scoring_version=SCORING_VERSION,
    )
    job = _job_or_404(db, job_id)
    fresh = _fresh_candidates(db, job_id)
    if fresh:
        run = PeopleDiscoveryRun(
            job_id=job_id, user_id=user.id, status="complete", provider="cache",
            query_fingerprint=query_fingerprint(job), cache_hit=True, completed_at=_now(),
        )
        db.add(run)
        for candidate in fresh:
            _ensure_recommendation(db, user.id, candidate, None, None)
        db.commit()
        metric(
            "people_discovery_cache_hits_total",
            provider="database",
            scoring_version=SCORING_VERSION,
        )
        metric("people_discovery_duration_ms", (time.monotonic() - started) * 1000)
        logger.info("people_discovery cache_hit=true job_id=%s scoring_version=%s", job_id, SCORING_VERSION)
        return recommendations_payload(db, user, job_id)

    fingerprint = query_fingerprint(job, strategy)
    cached_no_match = _fresh_no_match_run(
        db,
        job_id=job_id,
        user_id=user.id,
        fingerprint=fingerprint,
    )
    if cached_no_match is not None:
        metric(
            "people_discovery_cache_hits_total",
            provider="database_no_match",
            scoring_version=SCORING_VERSION,
        )
        logger.info(
            "people_discovery cache_hit=true no_match=true job_id=%s strategy=%s scoring_version=%s",
            job_id,
            strategy,
            SCORING_VERSION,
        )
        return recommendations_payload(db, user, job_id)

    if strategy == "broadened":
        exact_fingerprint = query_fingerprint(job, "exact")
        if _fresh_no_match_run(
            db,
            job_id=job_id,
            user_id=user.id,
            fingerprint=exact_fingerprint,
        ) is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PEOPLE_BROADEN_NOT_ELIGIBLE",
                    "message": (
                        "Complete an exact-company search before broadening."
                    ),
                },
            )

    rate_limit(f"discover:{user.id}", settings.people_discovery_rate_limit_per_hour)
    _budget_check(db, user.id)
    with _redis_lock(job_id, fingerprint) as acquired:
        if not acquired:
            return {
                "status": "in_progress",
                "availability_reason": "available",
                "beta": is_beta(user),
                "categories": _empty_categories(),
                "warnings": [],
                "search_scope": {
                    "company_scope": "Hiring company only",
                    "location_filter": "soft",
                    "parent_company_matches_included": False,
                    "refresh_eligible": False,
                },
                "controls": {
                    "email_discovery": settings.people_email_discovery_enabled,
                    "outreach_drafting": settings.people_outreach_drafting_enabled,
                },
            }
        # Recheck after lock acquisition.
        if _fresh_candidates(db, job_id):
            return recommendations_payload(db, user, job_id)
        if _fresh_no_match_run(
            db,
            job_id=job_id,
            user_id=user.id,
            fingerprint=fingerprint,
        ) is not None:
            return recommendations_payload(db, user, job_id)
        profile = extract_job_people_profile(job, db)
        provider = get_people_provider()
        company_context = {
            "canonical_company_name": profile.company_name,
            "canonical_company_domain": profile.company_domain,
            "aliases": profile.company_aliases,
            "parent_company": profile.parent_company_name,
            "parent_domain": profile.parent_company_domain,
            "domain_confidence": profile.domain_confidence,
            "evidence_source": profile.company_evidence_source,
            "scoring_version": SCORING_VERSION,
            "employment_validation_version": EMPLOYMENT_VALIDATION_VERSION,
            "employment_evidence_version": EMPLOYMENT_EVIDENCE_VERSION,
            "secondary_employment_verification_enabled": (
                settings.people_employment_secondary_verification_enabled
            ),
            "discovery_strategy_version": DISCOVERY_STRATEGY_VERSION,
            "discovery_strategy": strategy,
        }
        run = PeopleDiscoveryRun(
            job_id=job_id, user_id=user.id, status="running",
            provider=settings.people_primary_provider, query_fingerprint=fingerprint,
            company_context=company_context, category_diagnostics={},
        )
        db.add(run)
        db.commit()
        categories: dict[PeopleCategory, list[ProviderPerson]] = {}
        diagnostics: dict[str, dict] = {
            category: {
                "search_queries": [],
                "title_groups": [],
                "company_domain_used": profile.company_domain,
                "company_aliases_considered": profile.company_aliases,
                "seniorities_used": [],
                "location_filter_mode": "soft",
                "raw_search_result_count": 0,
                "unique_candidate_count": 0,
                "preliminary_score_distribution": _score_distribution([]),
                "selected_for_enrichment": 0,
                "enrichment_matches": 0,
                "enrichment_misses": 0,
                "candidates_rejected": 0,
                "rejection_reason_counts": {},
                "final_displayed_count": 0,
                "related_company_search_used": False,
                "broadened_title_search_used": strategy == "broadened",
                "discovery_strategy": strategy,
                "employment_secondary_verification_attempts": 0,
                "employment_secondary_verification_matches": 0,
                "employment_secondary_verification_credits": 0,
            }
            for category in _PEOPLE_CATEGORIES
        }
        failures: list[str] = []
        searched = 0
        try:
            for category in _PEOPLE_CATEGORIES:
                category_rows: list[ProviderPerson] = []
                queries = (
                    build_category_search_queries(profile, category)
                    if strategy == "exact"
                    else build_broadened_search_queries(profile, category)
                )
                for query in queries:
                    diagnostics[category]["search_queries"].append({
                        "title_group": query.title_group,
                        "titles": query.titles,
                        "company_match_kind": query.company_match_kind,
                    })
                    diagnostics[category]["title_groups"].append(query.title_group)
                    diagnostics[category]["seniorities_used"].extend(query.seniorities)
                    if query.company_match_kind == "related":
                        diagnostics[category]["related_company_search_used"] = True
                    try:
                        rows = await provider.search_people(query)
                    except ProviderUnavailable as exc:
                        failures.append(exc.reason)
                        _log_provider_failure(exc, run.id)
                        rows = []
                    category_rows.extend(rows)
                    searched += len(rows)
                    diagnostics[category]["raw_search_result_count"] += len(rows)
                categories[category] = deduplicate(category_rows)
                diagnostics[category]["unique_candidate_count"] = len(categories[category])
                duplicate_count = max(
                    0,
                    diagnostics[category]["raw_search_result_count"]
                    - diagnostics[category]["unique_candidate_count"],
                )
                if duplicate_count:
                    diagnostics[category]["rejection_reason_counts"]["duplicate_person"] = duplicate_count
                if not category_rows:
                    diagnostics[category]["rejection_reason_counts"]["no_search_results"] = 1
                metric(
                    "people_discovery_candidates_found",
                    len(categories[category]),
                    provider=settings.people_primary_provider,
                    category=category,
                    scoring_version=SCORING_VERSION,
                )
            if not any(categories.values()) and settings.people_pdl_fallback_enabled:
                fallback = PDLPeopleProvider()
                for category in _PEOPLE_CATEGORIES:
                    fallback_rows: list[ProviderPerson] = []
                    for query in build_category_search_queries(profile, category):
                        try:
                            fallback_rows.extend(await fallback.search_people(query))
                        except ProviderUnavailable as exc:
                            failures.append(exc.reason)
                            _log_provider_failure(exc, run.id)
                    categories[category] = deduplicate(fallback_rows)

            preliminary_by_category: dict[PeopleCategory, list[PreliminaryCandidate]] = {
                category: [] for category in _PEOPLE_CATEGORIES
            }
            for category, rows in categories.items():
                for person in rows:
                    school, employer = _shared_evidence(db, user.id, person)
                    score = score_candidate(
                        category, person, profile,
                        shared_school=bool(school), shared_employer=bool(employer),
                    )
                    preliminary_by_category[category].append(
                        (score, category, person, school, employer)
                    )
                diagnostics[category]["preliminary_score_distribution"] = _score_distribution(
                    [item[0] for item in preliminary_by_category[category]]
                )
            enrich_targets = allocate_enrichment_targets(
                preliminary_by_category,
                total=settings.people_max_enrichments_per_job,
                reservations={
                    "likely_recruiter": settings.people_recruiter_enrichment_reserve,
                    "potential_hiring_manager": settings.people_manager_enrichment_reserve,
                    "potential_referrer": settings.people_referrer_enrichment_reserve,
                },
            )
            for _score, category, _person, _school, _employer in enrich_targets:
                diagnostics[category]["selected_for_enrichment"] += 1
            unique_enrichment_requests = list(dict.fromkeys(
                item[2].provider_person_id for item in enrich_targets
            ))
            try:
                enriched = await provider.enrich_people(
                    [
                        PersonEnrichmentRequest(provider_person_id=provider_person_id)
                        for provider_person_id in unique_enrichment_requests
                    ]
                )
            except ProviderUnavailable as exc:
                failures.append(exc.reason)
                _log_provider_failure(exc, run.id)
                enriched = []
                metric(
                    "people_discovery_provider_errors_total",
                    provider=settings.people_primary_provider,
                    status="enrichment_failed",
                )
            metric(
                "people_enrichment_requests_total",
                len(unique_enrichment_requests),
                provider=settings.people_primary_provider,
            )
            enriched_by_id = {item.provider_person_id: item for item in enriched}
            for _score, category, initial, _school, _employer in enrich_targets:
                key = (
                    "enrichment_matches"
                    if initial.provider_person_id in enriched_by_id
                    else "enrichment_misses"
                )
                diagnostics[category][key] += 1
                if key == "enrichment_misses":
                    counts = diagnostics[category]["rejection_reason_counts"]
                    counts["enrichment_not_found"] = counts.get("enrichment_not_found", 0) + 1
            for category in _PEOPLE_CATEGORIES:
                not_selected = max(
                    0,
                    len(preliminary_by_category[category])
                    - diagnostics[category]["selected_for_enrichment"],
                )
                diagnostics[category]["candidates_rejected"] += not_selected
                if not_selected:
                    counts = diagnostics[category]["rejection_reason_counts"]
                    counts["enrichment_budget_exhausted"] = not_selected
            expires = _now() + timedelta(days=settings.people_result_ttl_days)
            caps = {
                "likely_recruiter": settings.people_max_displayed_recruiters,
                "potential_hiring_manager": settings.people_max_displayed_managers,
                "potential_referrer": settings.people_max_displayed_referrers,
            }
            displayed: dict[str, int] = defaultdict(int)
            for _, category, initial, school, employer in enrich_targets:
                if displayed[category] >= caps[category]:
                    continue
                person = enriched_by_id.get(initial.provider_person_id)
                if person is None:
                    diagnostics[category]["candidates_rejected"] += 1
                    continue
                person.exact_company_match = (
                    bool(profile.company_domain)
                    and person.current_company_domain == profile.company_domain
                )
                employment = _validate_employment(db, person, profile)
                score = score_candidate(
                    category, person, profile,
                    shared_school=bool(school), shared_employer=bool(employer),
                )
                data_confidence = confidence(person)
                threshold = _category_threshold(category)
                rejection_reasons = candidate_rejection_reasons(
                    category,
                    person,
                    profile,
                    relevance=score,
                    data_confidence=data_confidence,
                    relevance_threshold=threshold,
                    confidence_threshold=settings.people_min_data_confidence,
                )
                verification_blockers = {
                    reason
                    for reason in rejection_reasons
                    if reason
                    not in {"stale_employment", "below_confidence_threshold"}
                }
                verification_budget_reason = (
                    _employment_verification_budget_reason(db, user.id)
                )
                if verification_budget_reason:
                    diagnostics[category][
                        "employment_secondary_verification_budget_reason"
                    ] = verification_budget_reason
                should_secondary_verify = bool(
                    settings.people_employment_secondary_verification_enabled
                    and person.exact_company_match
                    and employment.identity_strong
                    and not verification_blockers
                    and verification_budget_reason is None
                    and (
                        employment.status
                        in {
                            "conflicting_current_employment",
                            "stale_or_uncertain",
                            "exact_company_current_but_unverified_freshness",
                        }
                        or settings.people_employment_comparison_mode
                    )
                    and diagnostics[category][
                        "employment_secondary_verification_attempts"
                    ]
                    < _employment_verification_cap(category)
                )
                if should_secondary_verify:
                    diagnostics[category][
                        "employment_secondary_verification_attempts"
                    ] += 1
                    secondary_match = await _secondary_employment_validation(
                        db,
                        user.id,
                        job_id,
                        run.id,
                        person,
                        profile,
                        category,
                    )
                    if secondary_match is not None:
                        secondary_result, secondary_person = secondary_match
                        diagnostics[category][
                            "employment_secondary_verification_matches"
                        ] += 1
                        diagnostics[category][
                            "employment_secondary_verification_credits"
                        ] += secondary_result.credits_consumed
                        if secondary_person is not None:
                            if (
                                secondary_result.status
                                == "confirmed_exact_company_verified"
                            ):
                                secondary_person.employment_verified_at = (
                                    secondary_result.verified_at
                                )
                                secondary_person.employment_source = (
                                    "secondary_verification"
                                )
                            _person_for_provider(db, secondary_person)
                            db.flush()
                        if secondary_result.status in {
                            "confirmed_exact_company_verified",
                            "conflicting_current_employment",
                        }:
                            employment = secondary_result
                if employment.status == "confirmed_exact_company_verified":
                    data_confidence = max(data_confidence, 0.75)
                    rejection_reasons = [
                        reason
                        for reason in rejection_reasons
                        if reason
                        not in {
                            "stale_employment",
                            "below_confidence_threshold",
                        }
                    ]
                rejection_reasons.extend(employment.rejection_codes)
                rejection_reasons = list(dict.fromkeys(rejection_reasons))
                if (
                    rejection_reasons == ["weak_company_confidence"]
                    and category != "potential_referrer"
                    and score >= threshold + 15
                ):
                    rejection_reasons = []
                if rejection_reasons:
                    diagnostics[category]["candidates_rejected"] += 1
                    counts = diagnostics[category]["rejection_reason_counts"]
                    for reason in rejection_reasons:
                        counts[reason] = counts.get(reason, 0) + 1
                    continue
                reasons, limitations = explanations(
                    category,
                    person,
                    profile,
                    shared_school=school,
                    shared_employer=employer,
                    employment_validation_status=employment.status,
                )
                canonical = _person_for_provider(db, person)
                db.flush()
                candidate = db.scalar(
                    select(JobPeopleCandidate).where(
                        JobPeopleCandidate.job_id == job_id,
                        JobPeopleCandidate.person_id == canonical.id,
                        JobPeopleCandidate.candidate_category == category,
                    )
                )
                if candidate is None:
                    candidate = JobPeopleCandidate(
                        job_id=job_id, person_id=canonical.id, candidate_category=category,
                        category_score=score, data_confidence=data_confidence,
                        current_employment_confidence=employment.confidence,
                        employment_validation_status=employment.status,
                        employment_validation_version=EMPLOYMENT_VALIDATION_VERSION,
                        employment_validation_checked_at=_now(),
                        recommendation_reasons=reasons,
                        recommendation_limitations=limitations,
                        scoring_version=SCORING_VERSION, expires_at=expires,
                    )
                    db.add(candidate)
                    db.flush()
                else:
                    candidate.category_score = score
                    candidate.data_confidence = data_confidence
                    candidate.current_employment_confidence = employment.confidence
                    candidate.employment_validation_status = employment.status
                    candidate.employment_validation_version = EMPLOYMENT_VALIDATION_VERSION
                    candidate.employment_validation_checked_at = _now()
                    candidate.recommendation_reasons = reasons
                    candidate.recommendation_limitations = limitations
                    candidate.expires_at = expires
                _ensure_recommendation(db, user.id, candidate, school, employer)
                canonical.employment_revalidation_required = False
                canonical.employment_conflict_detected_at = None
                displayed[category] += 1
                diagnostics[category]["final_displayed_count"] += 1
                metric(
                    "people_discovery_candidates_displayed",
                    provider=settings.people_primary_provider,
                    category=category,
                    scoring_version=SCORING_VERSION,
                )
            usage = await provider.get_usage()
            run = db.get(PeopleDiscoveryRun, run.id)
            run.status = "partial" if failures and any(displayed.values()) else "complete"
            if failures and not any(displayed.values()):
                run.status = "provider_unavailable"
                run.failure_code = failures[0][:60]
                run.safe_failure_message = _safe_provider_message(failures[0])
            run.records_searched = searched
            run.records_enriched = len(enriched)
            # Secondary employment verification has its own ledger and budget.
            # Do not fold those credits into the primary discovery run.
            run.provider_credits_used = usage.credits_used
            for category in _PEOPLE_CATEGORIES:
                diagnostics[category]["seniorities_used"] = list(dict.fromkeys(
                    diagnostics[category]["seniorities_used"]
                ))
                diagnostics[category]["title_groups"] = list(dict.fromkeys(
                    diagnostics[category]["title_groups"]
                ))
            run.company_context = company_context
            run.category_diagnostics = diagnostics
            run.completed_at = _now()
            db.commit()
            metric(
                "people_provider_credits_used",
                run.provider_credits_used,
                provider=usage.provider,
            )
            logger.info(
                "people_discovery status=%s job_id=%s searched=%s displayed=%s credits=%s scoring_version=%s",
                run.status, job_id, searched, sum(displayed.values()),
                run.provider_credits_used, SCORING_VERSION,
            )
        except Exception:
            db.rollback()
            failed_run = db.get(PeopleDiscoveryRun, run.id)
            if failed_run:
                failed_run.status = "provider_unavailable"
                failed_run.failure_code = "discovery_failed"
                failed_run.safe_failure_message = "People discovery is temporarily unavailable."
                failed_run.completed_at = _now()
                db.commit()
            metric(
                "people_discovery_provider_errors_total",
                provider=settings.people_primary_provider,
                status="discovery_failed",
            )
            logger.exception("people_discovery failed job_id=%s", job_id)
        metric("people_discovery_duration_ms", (time.monotonic() - started) * 1000)
        return recommendations_payload(db, user, job_id)


def _empty_categories() -> dict[str, list]:
    return {
        "likely_recruiters": [],
        "potential_hiring_managers": [],
        "potential_referrers": [],
    }


def recommendations_payload(db: Session, user: User, job_id: int) -> dict:
    job = _job_or_404(db, job_id)
    rows = db.execute(
        select(UserJobPeopleRecommendation, JobPeopleCandidate, ProfessionalPerson)
        .join(JobPeopleCandidate, UserJobPeopleRecommendation.job_people_candidate_id == JobPeopleCandidate.id)
        .join(ProfessionalPerson, JobPeopleCandidate.person_id == ProfessionalPerson.id)
        .where(
            UserJobPeopleRecommendation.user_id == user.id,
            UserJobPeopleRecommendation.job_id == job_id,
            UserJobPeopleRecommendation.suppressed_at.is_(None),
            JobPeopleCandidate.scoring_version == SCORING_VERSION,
            JobPeopleCandidate.employment_validation_version
            == EMPLOYMENT_VALIDATION_VERSION,
            JobPeopleCandidate.employment_validation_status.in_(
                DISPLAYABLE_EMPLOYMENT_STATUSES
            ),
            ProfessionalPerson.employment_revalidation_required.is_(False),
        )
        .order_by(JobPeopleCandidate.category_score.desc())
    ).all()
    categories = _empty_categories()
    key_map = {
        "likely_recruiter": "likely_recruiters",
        "potential_hiring_manager": "potential_hiring_managers",
        "potential_referrer": "potential_referrers",
    }
    now = _now()
    stale = False
    expires_at: datetime | None = None
    for recommendation, candidate, person in rows:
        expires = candidate.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        stale = stale or expires <= now
        expires_at = expires if expires_at is None else min(expires_at, expires)
        if expires <= now:
            continue
        email_lookup_allowed = (
            candidate.employment_validation_status
            == "confirmed_exact_company_verified"
            and not person.employment_revalidation_required
        )
        email = (
            decrypt_email(person.professional_email_ciphertext)
            if person.email_verification_status == "verified"
            and email_lookup_allowed
            else None
        )
        categories[key_map[candidate.candidate_category]].append(
            {
                "recommendation_id": recommendation.id,
                "full_name": person.canonical_full_name,
                "current_title": person.current_title,
                "current_company": person.current_company_name,
                "category": candidate.candidate_category,
                "category_label": {
                    "likely_recruiter": "Likely recruiter",
                    "potential_hiring_manager": "Potential hiring manager",
                    "potential_referrer": "Potential referral candidate",
                }[candidate.candidate_category],
                "relevance_score": round(candidate.category_score),
                "confidence": confidence_label(candidate.data_confidence),
                "current_employment_confidence": round(
                    candidate.current_employment_confidence, 2
                ),
                "employment_validation_status": (
                    candidate.employment_validation_status
                ),
                "employment_last_verified_at": (
                    person.employment_last_verified_at
                    if candidate.employment_validation_status
                    == "confirmed_exact_company_verified"
                    else None
                ),
                "employment_warning": None,
                "email_lookup_allowed": email_lookup_allowed,
                "reasons": [*candidate.recommendation_reasons, *recommendation.personalized_reasons][:3],
                "limitations": candidate.recommendation_limitations,
                "last_checked_at": candidate.discovered_at,
                "professional_profile_url": safe_profile_url(person.linkedin_url),
                "email_status": (
                    person.email_verification_status
                    if email_lookup_allowed
                    else "employment_conflict"
                ),
                "professional_email": email,
                "email_verified_at": person.email_verified_at,
                "saved": recommendation.saved_at is not None,
                "contacted": recommendation.contacted_at is not None,
            }
        )
    exact_fingerprint = query_fingerprint(job, "exact")
    broadened_fingerprint = query_fingerprint(job, "broadened")
    current_fingerprints = [exact_fingerprint, broadened_fingerprint]
    latest_current_run = _latest_run(
        db,
        job_id=job_id,
        user_id=user.id,
        fingerprints=current_fingerprints,
    )
    latest_any_run = _latest_run(db, job_id=job_id, user_id=user.id)
    latest_run = latest_current_run or latest_any_run
    stale_version = latest_any_run is not None and latest_current_run is None
    has_results = any(categories.values())
    response_status = "complete" if has_results else "not_started"
    warnings: list[str] = []
    if stale_version:
        response_status = "stale"
        warnings.append("Previous search results used an older search version. Refresh is available.")
    elif latest_run and latest_run.status in {"running"}:
        response_status = "in_progress"
    elif latest_run and latest_run.status == "provider_unavailable":
        response_status = "provider_unavailable"
        warnings.append(latest_run.safe_failure_message or "Professional data provider unavailable.")
    elif latest_run and latest_run.status == "partial":
        response_status = "partial"
        warnings.append("Some professional data sources were unavailable; showing reliable partial results.")
    elif latest_run and not has_results:
        response_status = "no_reliable_matches"
    if stale and not has_results:
        response_status = "stale"
        warnings.append("Previous results are stale. Refresh is available.")
    availability_reason = (
        latest_run.failure_code
        if latest_run and latest_run.status == "provider_unavailable"
        else "available"
    )
    retry_eligible = availability_reason in {
        "provider_circuit_open",
        "provider_rate_limited",
        "provider_timeout",
        "provider_network_error",
        "provider_unavailable",
    }
    retry_after_seconds = (
        _provider_retry_seconds(availability_reason) if retry_eligible else None
    )
    exact_no_match = _fresh_no_match_run(
        db,
        job_id=job_id,
        user_id=user.id,
        fingerprint=exact_fingerprint,
    )
    broadened_no_match = _fresh_no_match_run(
        db,
        job_id=job_id,
        user_id=user.id,
        fingerprint=broadened_fingerprint,
    )
    broaden_eligible = bool(
        not has_results
        and exact_no_match is not None
        and broadened_no_match is None
    )
    broaden_attempted = bool(
        latest_current_run
        and (latest_current_run.company_context or {}).get("discovery_strategy")
        == "broadened"
    )
    related_company_search_attempted = bool(
        latest_current_run
        and any(
            bool(value.get("related_company_search_used"))
            for value in (latest_current_run.category_diagnostics or {}).values()
            if isinstance(value, dict)
        )
    )
    return {
        "status": response_status,
        "availability_reason": availability_reason,
        "retry_eligible": retry_eligible,
        "retry_after_seconds": retry_after_seconds,
        "beta": is_beta(user),
        "generated_at": latest_run.completed_at if latest_run else None,
        "expires_at": expires_at,
        "categories": categories,
        "coverage": {key: bool(value) for key, value in categories.items()},
        "search_scope": {
            "company_scope": (
                "Hiring company and evidence-backed related domain"
                if related_company_search_attempted
                else "Hiring company only"
            ),
            "location_filter": "soft",
            "parent_company_matches_included": related_company_search_attempted,
            "refresh_eligible": response_status
            in {"provider_unavailable", "stale"},
            "exact_company_search_completed": exact_no_match is not None
            or bool(has_results and latest_current_run),
            "related_company_search_attempted": related_company_search_attempted,
            "broaden_eligible": broaden_eligible,
            "broaden_attempted": broaden_attempted,
        },
        "warnings": warnings,
        "controls": {
            "email_discovery": settings.people_email_discovery_enabled,
            "outreach_drafting": settings.people_outreach_drafting_enabled,
        },
    }


def diagnostics_payload(db: Session, user: User, job_id: int) -> dict:
    if settings.app_env != "development":
        raise HTTPException(status_code=404, detail="Not found")
    _job_or_404(db, job_id)
    run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(
            PeopleDiscoveryRun.job_id == job_id,
            PeopleDiscoveryRun.user_id == user.id,
        )
        .order_by(PeopleDiscoveryRun.started_at.desc())
    )
    if run is None:
        return {"discovery_run_id": None, "company_context": {}, "categories": {}}
    return {
        "discovery_run_id": run.id,
        "status": run.status,
        "company_context": run.company_context or {},
        "categories": run.category_diagnostics or {},
        "credits_consumed": run.provider_credits_used,
        "completed_at": run.completed_at,
    }


def owned_recommendation(
    db: Session, user: User, job_id: int, recommendation_id: int
) -> tuple[UserJobPeopleRecommendation, JobPeopleCandidate, ProfessionalPerson]:
    row = db.execute(
        select(UserJobPeopleRecommendation, JobPeopleCandidate, ProfessionalPerson)
        .join(JobPeopleCandidate, UserJobPeopleRecommendation.job_people_candidate_id == JobPeopleCandidate.id)
        .join(ProfessionalPerson, JobPeopleCandidate.person_id == ProfessionalPerson.id)
        .where(
            UserJobPeopleRecommendation.id == recommendation_id,
            UserJobPeopleRecommendation.user_id == user.id,
            UserJobPeopleRecommendation.job_id == job_id,
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return row


async def find_email(db: Session, user: User, job_id: int, recommendation_id: int) -> dict:
    if not settings.people_email_discovery_enabled:
        raise HTTPException(status_code=404, detail="Email discovery is disabled")
    recommendation, candidate, person = owned_recommendation(
        db, user, job_id, recommendation_id
    )
    if (
        candidate.employment_validation_version != EMPLOYMENT_VALIDATION_VERSION
        or candidate.employment_validation_status
        != "confirmed_exact_company_verified"
        or person.employment_revalidation_required
    ):
        status_value = (
            "employment_conflict"
            if person.employment_revalidation_required
            or candidate.employment_validation_status
            == "conflicting_current_employment"
            else "identity_uncertain"
        )
        return {
            "status": status_value,
            "professional_email": None,
            "verified_at": None,
        }
    checked_at = person.email_verified_at
    if checked_at and checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    reusable_statuses = {"verified", "accept_all", "risky", "unknown", "not_found"}
    if (
        person.email_verification_status in reusable_statuses
        and checked_at
        and checked_at
        > _now() - timedelta(days=settings.people_email_result_ttl_days)
    ):
        return {
            "status": person.email_verification_status,
            "professional_email": (
                decrypt_email(person.professional_email_ciphertext)
                if person.email_verification_status == "verified"
                else None
            ),
            "verified_at": person.email_verified_at,
        }
    try:
        rate_limit(f"email:{user.id}", settings.people_email_rate_limit_per_hour)
    except HTTPException:
        return {
            "status": "rate_limited",
            "professional_email": None,
            "verified_at": None,
        }
    if _email_budget_exceeded(db, user.id):
        return {
            "status": "budget_exceeded",
            "professional_email": None,
            "verified_at": None,
        }
    job = _job_or_404(db, job_id)
    domain = extract_job_people_profile(job, db).company_domain
    if not domain or person.current_company_domain != domain:
        return {
            "status": "employment_conflict",
            "professional_email": None,
            "verified_at": None,
        }
    with _redis_lock(
        job_id, f"{user.id}:{person.id}", namespace="email"
    ) as acquired:
        if not acquired:
            return {
                "status": "searching",
                "professional_email": None,
                "verified_at": None,
            }
        db.refresh(person)
        if (
            person.email_verification_status in reusable_statuses
            and person.email_verified_at
            and (
                person.email_verified_at
                if person.email_verified_at.tzinfo
                else person.email_verified_at.replace(tzinfo=UTC)
            )
            > _now() - timedelta(days=settings.people_email_result_ttl_days)
        ):
            return {
                "status": person.email_verification_status,
                "professional_email": (
                    decrypt_email(person.professional_email_ciphertext)
                    if person.email_verification_status == "verified"
                    else None
                ),
                "verified_at": person.email_verified_at,
            }
        provider = get_email_provider()
        metric("people_email_find_requests_total", provider="hunter")
        try:
            found = await provider.find_work_email(
                WorkEmailRequest(
                    full_name=person.canonical_full_name,
                    company_domain=domain,
                )
            )
            if not found.email or not is_professional_email(found.email, domain):
                person.email_verification_status = "not_found"
                person.email_verified_at = _now()
            else:
                verified = await provider.verify_work_email(found.email)
                person.email_verification_status = verified.status
                person.email_verified_at = verified.verified_at or _now()
                if verified.status == "verified":
                    person.professional_email_ciphertext = encrypt_email(found.email)
                    person.professional_email_hash = email_hash(found.email)
                    metric("people_email_verified_total", provider=verified.provider)
                else:
                    person.professional_email_ciphertext = None
                    person.professional_email_hash = None
            db.add(
                PeopleDiscoveryRun(
                    job_id=job_id,
                    user_id=user.id,
                    status=f"email_{person.email_verification_status}",
                    provider="hunter",
                    query_fingerprint=hashlib.sha256(
                        f"email:{user.id}:{person.id}:{_now().date().isoformat()}".encode()
                    ).hexdigest(),
                    records_searched=1,
                    records_enriched=1
                    if person.email_verification_status != "not_found"
                    else 0,
                    provider_credits_used=int(getattr(provider, "credits", 0)),
                    completed_at=_now(),
                )
            )
            record_audit(
                db,
                user.id,
                "people_work_email_discovered",
                {
                    "job_id": job_id,
                    "recommendation_id": recommendation.id,
                    "status": person.email_verification_status,
                },
            )
            db.commit()
        except ProviderUnavailable as exc:
            person.email_verification_status = (
                "rate_limited"
                if exc.reason == "provider_rate_limited"
                else "provider_unavailable"
            )
            db.commit()
    if person.email_verification_status == "not_found":
        metric("people_email_not_found_total", provider="hunter")
    return {
        "status": person.email_verification_status,
        "professional_email": (
            decrypt_email(person.professional_email_ciphertext)
            if person.email_verification_status == "verified" else None
        ),
        "verified_at": person.email_verified_at,
    }


def outreach_draft(
    db: Session, user: User, job_id: int, recommendation_id: int, request: OutreachDraftRequest
) -> dict:
    if not settings.people_outreach_drafting_enabled:
        raise HTTPException(status_code=404, detail="Outreach drafting is disabled")
    recommendation, candidate, person = owned_recommendation(db, user, job_id, recommendation_id)
    if (
        recommendation.suppressed_at is not None
        or candidate.employment_validation_version
        != EMPLOYMENT_VALIDATION_VERSION
        or candidate.employment_validation_status
        != "confirmed_exact_company_verified"
        or person.employment_revalidation_required
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PEOPLE_EMPLOYMENT_REVALIDATION_REQUIRED",
                "message": (
                    "Current employment must be revalidated before drafting outreach."
                ),
            },
        )
    job = _job_or_404(db, job_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    name = (profile.full_name if profile else "") or "a candidate"
    first_name = person.canonical_full_name.split()[0]
    greeting = f"Hi {first_name},"
    facts_used = [
        f"job:{job.title}",
        f"company:{job.company}",
        f"recipient_title:{person.current_title}",
        f"recipient_company:{person.current_company_name}",
    ]
    shared_line = ""
    if request.draft_type == "shared_school" and recommendation.shared_school:
        shared_line = f" We both attended {recommendation.shared_school}."
        facts_used.append("confirmed_shared_school")
    elif request.draft_type == "shared_previous_employer" and recommendation.shared_employer:
        shared_line = f" We both worked at {recommendation.shared_employer}."
        facts_used.append("confirmed_shared_previous_employer")
    elif request.draft_type in {"shared_school", "shared_previous_employer"}:
        raise HTTPException(status_code=422, detail="The selected shared evidence is not available.")

    skills = [
        str(value).strip()
        for value in (profile.skills if profile else [])
        if str(value).strip()
    ]
    job_skills = {
        normalize_text(str(value))
        for value in [*(job.required_skills or []), *(job.preferred_skills or [])]
    }
    qualifications = [
        skill for skill in skills if normalize_text(skill) in job_skills
    ][:2]
    if not qualifications:
        qualifications = skills[:2]
    qualification_line = ""
    if qualifications:
        qualification_line = (
            " My relevant experience includes "
            + " and ".join(qualifications)
            + "."
        )
        facts_used.extend(f"applicant_skill:{value}" for value in qualifications)
    advertised_skills = [
        str(value).strip()
        for value in (job.required_skills or [])
        if str(value).strip()
    ][:2]
    job_focus_line = ""
    if advertised_skills:
        job_focus_line = (
            " The posting specifically emphasizes "
            + " and ".join(advertised_skills)
            + "."
        )
        facts_used.extend(f"job_skill:{value}" for value in advertised_skills)

    if candidate.candidate_category == "likely_recruiter":
        role_line = (
            f" I applied for the {job.title} role at {job.company}."
            f"{qualification_line}{job_focus_line}"
        )
        ask = (
            " If you handle this area, could you point me to the most useful "
            "information to include for the recruiting team?"
        )
        omitted = ["recruiter_assignment_unconfirmed"]
    elif candidate.candidate_category == "potential_hiring_manager":
        role_line = (
            f" I’m applying for the {job.title} role at {job.company}."
            f"{qualification_line}{job_focus_line}"
        )
        ask = (
            " From your perspective, what capability matters most for someone "
            "joining this engineering function?"
        )
        omitted = ["team_membership_unconfirmed", "hiring_responsibility_unconfirmed"]
    else:
        role_line = (
            f" I’m applying for the {job.title} role at {job.company}, and your "
            f"work as {person.current_title} is close to the area I’m exploring."
            f"{qualification_line}{job_focus_line}"
        )
        direct_referral = request.draft_type in {
            "referral_request",
            "direct_referral_request",
        }
        ask = (
            " If you’re comfortable, would you consider referring me after "
            "reviewing my background?"
            if direct_referral
            else " Would you be open to sharing one perspective on the role or application process?"
        )
        omitted = ["referral_willingness_unconfirmed"]
    guidance = request.user_guidance or request.user_details
    guidance_line = f" {guidance.strip()}" if guidance and guidance.strip() else ""
    if guidance_line:
        facts_used.append("user_provided_guidance")

    if request.tone == "warm":
        opener = " I appreciate you taking a moment to read this."
        close = "Thanks for any perspective you’re comfortable sharing."
    elif request.tone == "direct":
        opener = ""
        close = "Thank you for considering the question."
    else:
        opener = ""
        close = "Thanks for your time."
    core = f"{greeting}{opener}{shared_line}{role_line}{guidance_line}{ask}"
    if request.message_type == "email":
        category_context = {
            "likely_recruiter": (
                "I want to give the recruiting team the clearest, most relevant "
                "summary rather than send a broad introduction."
            ),
            "potential_hiring_manager": (
                "I’m especially interested in how the advertised work maps to "
                "the engineering priorities your function is solving."
            ),
            "potential_referrer": (
                "I’m looking for candid context before making any request beyond "
                "learning more about the opportunity."
            ),
        }[candidate.candidate_category]
        body = (
            f"{core}\n\n{category_context} That context would help me keep the "
            "application focused and useful. I’m happy to share a concise resume "
            f"or clarify any relevant experience.\n\n{close}\n{name}"
        )
        subject = f"Question about {job.title} at {job.company}"
    elif request.message_type == "linkedin_connection_note":
        body = f"{greeting}{role_line}{ask}"
        if len(body) > 300:
            body = (
                f"Hi {first_name}, I’m applying for {job.title} at {job.company}. "
                "Would you be open to connecting and sharing one brief perspective?"
            )
        body = body[:300]
        subject = None
    else:
        direct_context = {
            "likely_recruiter": (
                "I want to make the application easy for the recruiting team "
                "to review."
            ),
            "potential_hiring_manager": (
                "I’m trying to understand how the advertised work connects to "
                "the function’s current engineering priorities."
            ),
            "potential_referrer": (
                "I’m looking for candid context before making any request beyond "
                "learning about the opportunity."
            ),
        }[candidate.candidate_category]
        body = f"{core}\n\n{direct_context}\n\n{close}\n{name}"
        subject = None
    response = {
        "message_type": request.message_type,
        "subject": subject,
        "body": body,
        "draft": body,
        "facts_used": facts_used,
        "assumptions": [],
        "omitted_uncertain_facts": omitted,
        "character_count": len(body),
        "requires_manual_review": True,
        "requires_user_review": True,
        "sent": False,
    }
    record_audit(db, user.id, "people_outreach_draft_generated", {
        "job_id": job_id, "recommendation_id": recommendation.id,
        "draft_type": request.draft_type, "message_type": request.message_type,
        "automatically_sent": False, "category": candidate.candidate_category,
    })
    db.commit()
    return response


def set_saved(db: Session, user: User, job_id: int, recommendation_id: int, saved: bool) -> dict:
    recommendation, _, _ = owned_recommendation(db, user, job_id, recommendation_id)
    recommendation.saved_at = _now() if saved else None
    db.commit()
    return {"saved": saved}


def mark_contacted(db: Session, user: User, job_id: int, recommendation_id: int) -> dict:
    recommendation, _, _ = owned_recommendation(db, user, job_id, recommendation_id)
    recommendation.contacted_at = _now()
    db.commit()
    return {"contacted": True}


def submit_feedback(
    db: Session, user: User, job_id: int, recommendation_id: int, request: FeedbackRequest
) -> dict:
    recommendation, candidate, person = owned_recommendation(
        db, user, job_id, recommendation_id
    )
    feedback = PeopleRecommendationFeedback(
        user_id=user.id, recommendation_id=recommendation.id,
        relevance_rating=request.relevance_rating,
        employment_current_rating=request.employment_current_rating,
        information_correct_rating=request.information_correct_rating,
        contacted=request.contacted, received_response=request.received_response,
        incorrect_reason=(
            "employment_revalidation_requested"
            if request.employment_current_rating == "stale"
            else "information_reported_incorrect"
            if request.information_correct_rating == "incorrect"
            else None
        ),
    )
    db.add(feedback)
    metric(
        "people_recommendation_feedback_total",
        category="all",
        scoring_version=SCORING_VERSION,
    )
    employment_reported_incorrect = (
        request.employment_current_rating == "stale"
        or request.information_correct_rating == "incorrect"
    )
    if request.information_correct_rating == "incorrect":
        recommendation.suppressed_at = _now()
        metric(
            "people_recommendation_reported_incorrect_total",
            scoring_version=SCORING_VERSION,
        )
    if employment_reported_incorrect:
        recommendation.suppressed_at = _now()
        person.employment_revalidation_required = True
        person.employment_conflict_detected_at = _now()
        candidate.employment_validation_status = "conflicting_current_employment"
        candidate.employment_validation_checked_at = _now()
    record_audit(db, user.id, "people_recommendation_feedback", {
        "job_id": job_id, "recommendation_id": recommendation.id,
        "reported_incorrect": request.information_correct_rating == "incorrect",
    })
    db.commit()
    return {"accepted": True, "suppressed": recommendation.suppressed_at is not None}
