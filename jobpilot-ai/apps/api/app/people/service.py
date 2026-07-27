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
    PeopleRecommendationFeedback,
    ProfessionalPerson,
    ProfessionalPersonSource,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
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

logger = logging.getLogger("jobpilot.people")

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


def query_fingerprint(job: JobPosting) -> str:
    profile = extract_job_people_profile(job)
    payload = profile.model_dump(mode="json")
    payload["scoring_version"] = SCORING_VERSION
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
            )
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
            employment_last_verified_at=value.employment_verified_at or value.source_last_updated_at,
        )
        db.add(canonical)
        db.flush()
    else:
        # Prefer fresher provider evidence; never overwrite with missing values.
        incoming = value.employment_verified_at or value.source_last_updated_at
        current = canonical.employment_last_verified_at
        if incoming and (
            not current or incoming.replace(tzinfo=UTC) >= current.replace(tzinfo=UTC)
        ):
            canonical.current_company_name = value.current_company_name[:255]
            canonical.current_company_domain = value.current_company_domain or canonical.current_company_domain
            canonical.current_title = value.current_title[:255]
            canonical.normalized_title = normalize_text(value.current_title)[:255]
            canonical.department = value.department or canonical.department
            canonical.seniority = value.seniority or canonical.seniority
            canonical.professional_location = value.location or canonical.professional_location
            canonical.employment_last_verified_at = incoming
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
            normalized_evidence=value.evidence,
            field_provenance=value.field_provenance,
            redacted_payload={},
        )
        db.add(source)
    else:
        source.normalized_evidence = value.evidence
        source.field_provenance = value.field_provenance
        source.source_last_updated_at = value.source_last_updated_at
    return canonical


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
            PeopleDiscoveryRun.started_at >= start
        )
    ) or 0
    user_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0)).where(
            PeopleDiscoveryRun.user_id == user_id, PeopleDiscoveryRun.started_at >= start
        )
    ) or 0
    if settings.people_daily_credit_budget and global_used >= settings.people_daily_credit_budget:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_GLOBAL_BUDGET_EXCEEDED",
                "message": "People discovery is temporarily unavailable because today's usage limit was reached.",
                "retryable": True,
            },
        )
    if settings.people_per_user_daily_limit and user_used >= settings.people_per_user_daily_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_USER_BUDGET_EXCEEDED",
                "message": "People discovery is temporarily unavailable because your daily usage limit was reached.",
                "retryable": True,
            },
        )


@contextmanager
def _redis_lock(job_id: int, fingerprint: str) -> Iterator[bool]:
    client = None
    key = f"people:discover:{job_id}:{fingerprint}"
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


async def discover(db: Session, user: User, job_id: int) -> dict:
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

    rate_limit(f"discover:{user.id}", settings.people_discovery_rate_limit_per_hour)
    _budget_check(db, user.id)
    fingerprint = query_fingerprint(job)
    with _redis_lock(job_id, fingerprint) as acquired:
        if not acquired:
            return {
                "status": "in_progress",
                "availability_reason": "available",
                "beta": is_beta(user),
                "categories": _empty_categories(),
                "warnings": [],
                "controls": {
                    "email_discovery": settings.people_email_discovery_enabled,
                    "outreach_drafting": settings.people_outreach_drafting_enabled,
                },
            }
        # Recheck after lock acquisition.
        if _fresh_candidates(db, job_id):
            return recommendations_payload(db, user, job_id)
        profile = extract_job_people_profile(job)
        provider = get_people_provider()
        run = PeopleDiscoveryRun(
            job_id=job_id, user_id=user.id, status="running",
            provider=settings.people_primary_provider, query_fingerprint=fingerprint,
        )
        db.add(run)
        db.commit()
        categories: dict[PeopleCategory, list[ProviderPerson]] = {}
        failures: list[str] = []
        query_specs: list[tuple[PeopleCategory, list[str]]] = [
            ("likely_recruiter", profile.recruiter_titles),
            ("potential_hiring_manager", profile.hiring_manager_titles),
            ("potential_referrer", profile.team_member_titles),
        ]
        searched = 0
        try:
            for category, titles in query_specs:
                query = PeopleSearchQuery(
                    category=category,
                    company_name=profile.company_name,
                    company_domain=profile.company_domain,
                    titles=titles,
                    role_family=profile.role_family,
                    department=profile.department,
                    location=profile.location,
                    limit=settings.people_max_discovery_results_per_category,
                )
                try:
                    rows = await provider.search_people(query)
                except ProviderUnavailable as exc:
                    failures.append(exc.reason)
                    _log_provider_failure(exc, run.id)
                    rows = []
                searched += len(rows)
                metric(
                    "people_discovery_candidates_found",
                    len(rows),
                    provider=settings.people_primary_provider,
                    category=category,
                    scoring_version=SCORING_VERSION,
                )
                categories[category] = deduplicate(rows)
            if not any(categories.values()) and settings.people_pdl_fallback_enabled:
                fallback = PDLPeopleProvider()
                for category, titles in query_specs:
                    try:
                        categories[category] = await fallback.search_people(
                            PeopleSearchQuery(
                                category=category, company_name=profile.company_name,
                                company_domain=profile.company_domain, titles=titles,
                                role_family=profile.role_family, department=profile.department,
                                location=profile.location,
                                limit=settings.people_max_discovery_results_per_category,
                            )
                        )
                    except ProviderUnavailable as exc:
                        failures.append(exc.reason)
                        _log_provider_failure(exc, run.id)

            preliminary: list[tuple[float, PeopleCategory, ProviderPerson, str | None, str | None]] = []
            for category, rows in categories.items():
                for person in rows:
                    school, employer = _shared_evidence(db, user.id, person)
                    score = score_candidate(
                        category, person, profile,
                        shared_school=bool(school), shared_employer=bool(employer),
                    )
                    preliminary.append((score, category, person, school, employer))
            preliminary.sort(key=lambda item: item[0], reverse=True)
            enrich_targets = preliminary[: settings.people_max_enrichments_per_job]
            try:
                enriched = await provider.enrich_people(
                    [
                        PersonEnrichmentRequest(provider_person_id=item[2].provider_person_id)
                        for item in enrich_targets
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
                len(enrich_targets),
                provider=settings.people_primary_provider,
            )
            enriched_by_id = {item.provider_person_id: item for item in enriched}
            expires = _now() + timedelta(days=settings.people_result_ttl_days)
            caps = {
                "likely_recruiter": settings.people_max_displayed_recruiters,
                "potential_hiring_manager": settings.people_max_displayed_managers,
                "potential_referrer": settings.people_max_displayed_referrers,
            }
            displayed: dict[str, int] = defaultdict(int)
            for _, category, initial, school, employer in preliminary:
                if displayed[category] >= caps[category]:
                    continue
                person = enriched_by_id.get(initial.provider_person_id, initial)
                score = score_candidate(
                    category, person, profile,
                    shared_school=bool(school), shared_employer=bool(employer),
                )
                data_confidence = confidence(person)
                if score < settings.people_min_relevance_score or data_confidence < 0.5:
                    continue
                reasons, limitations = explanations(
                    category, person, profile, shared_school=school, shared_employer=employer
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
                        current_employment_confidence=min(1.0, data_confidence + 0.05),
                        recommendation_reasons=reasons,
                        recommendation_limitations=limitations,
                        scoring_version=SCORING_VERSION, expires_at=expires,
                    )
                    db.add(candidate)
                    db.flush()
                else:
                    candidate.category_score = score
                    candidate.data_confidence = data_confidence
                    candidate.recommendation_reasons = reasons
                    candidate.recommendation_limitations = limitations
                    candidate.expires_at = expires
                _ensure_recommendation(db, user.id, candidate, school, employer)
                displayed[category] += 1
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
            run.provider_credits_used = usage.credits_used
            run.completed_at = _now()
            db.commit()
            metric(
                "people_provider_credits_used",
                usage.credits_used,
                provider=usage.provider,
            )
            logger.info(
                "people_discovery status=%s job_id=%s searched=%s displayed=%s credits=%s scoring_version=%s",
                run.status, job_id, searched, sum(displayed.values()), usage.credits_used, SCORING_VERSION,
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
    _job_or_404(db, job_id)
    rows = db.execute(
        select(UserJobPeopleRecommendation, JobPeopleCandidate, ProfessionalPerson)
        .join(JobPeopleCandidate, UserJobPeopleRecommendation.job_people_candidate_id == JobPeopleCandidate.id)
        .join(ProfessionalPerson, JobPeopleCandidate.person_id == ProfessionalPerson.id)
        .where(
            UserJobPeopleRecommendation.user_id == user.id,
            UserJobPeopleRecommendation.job_id == job_id,
            UserJobPeopleRecommendation.suppressed_at.is_(None),
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
        email = (
            decrypt_email(person.professional_email_ciphertext)
            if person.email_verification_status == "verified"
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
                "reasons": [*candidate.recommendation_reasons, *recommendation.personalized_reasons][:3],
                "limitations": candidate.recommendation_limitations,
                "last_checked_at": candidate.discovered_at,
                "professional_profile_url": safe_profile_url(person.linkedin_url),
                "email_status": person.email_verification_status,
                "professional_email": email,
                "email_verified_at": person.email_verified_at,
                "saved": recommendation.saved_at is not None,
                "contacted": recommendation.contacted_at is not None,
            }
        )
    latest_run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(PeopleDiscoveryRun.job_id == job_id, PeopleDiscoveryRun.user_id == user.id)
        .order_by(PeopleDiscoveryRun.started_at.desc())
    )
    has_results = any(categories.values())
    response_status = "complete" if has_results else "not_started"
    warnings: list[str] = []
    if latest_run and latest_run.status in {"running"}:
        response_status = "in_progress"
    elif latest_run and latest_run.status == "provider_unavailable":
        response_status = "provider_unavailable"
        warnings.append(latest_run.safe_failure_message or "Professional data provider unavailable.")
    elif latest_run and latest_run.status == "partial":
        response_status = "partial"
        warnings.append("Some professional data sources were unavailable; showing reliable partial results.")
    elif latest_run and not has_results:
        response_status = "no_reliable_matches"
        warnings.append("No sufficiently reliable people were found.")
    if stale and not has_results:
        response_status = "stale"
        warnings.append("Previous results are stale. Refresh is available.")
    return {
        "status": response_status,
        "availability_reason": (
            latest_run.failure_code
            if latest_run and latest_run.status == "provider_unavailable"
            else "available"
        ),
        "beta": is_beta(user),
        "generated_at": latest_run.completed_at if latest_run else None,
        "expires_at": expires_at,
        "categories": categories,
        "coverage": {key: bool(value) for key, value in categories.items()},
        "warnings": warnings,
        "controls": {
            "email_discovery": settings.people_email_discovery_enabled,
            "outreach_drafting": settings.people_outreach_drafting_enabled,
        },
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
    recommendation, _, person = owned_recommendation(db, user, job_id, recommendation_id)
    checked_at = person.email_verified_at
    if checked_at and checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    reusable_statuses = {"verified", "accept_all", "risky", "unknown", "not_found"}
    if (
        person.email_verification_status in reusable_statuses
        and checked_at
        and checked_at > _now() - timedelta(days=settings.people_result_ttl_days)
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
    rate_limit(f"email:{user.id}", settings.people_email_rate_limit_per_hour)
    _budget_check(db, user.id)
    domain = person.current_company_domain
    if not domain:
        return {"status": "not_found", "professional_email": None}
    provider = get_email_provider()
    metric("people_email_find_requests_total", provider="hunter")
    try:
        found = await provider.find_work_email(
            WorkEmailRequest(full_name=person.canonical_full_name, company_domain=domain)
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
        db.add(
            PeopleDiscoveryRun(
                job_id=job_id,
                user_id=user.id,
                status=f"email_{person.email_verification_status}",
                provider="hunter",
                query_fingerprint=hashlib.sha256(
                    f"email:{person.id}:{_now().date().isoformat()}".encode()
                ).hexdigest(),
                records_searched=1,
                provider_credits_used=int(getattr(provider, "credits", 0)),
                completed_at=_now(),
            )
        )
        record_audit(
            db, user.id, "people_work_email_discovered",
            {"job_id": job_id, "recommendation_id": recommendation.id, "status": person.email_verification_status},
        )
        db.commit()
    except ProviderUnavailable:
        person.email_verification_status = "provider_error"
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
    job = _job_or_404(db, job_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    name = (profile.full_name if profile else "") or "a candidate"
    greeting = f"Hi {person.canonical_full_name.split()[0]},"
    evidence_line = ""
    if request.draft_type == "shared_school" and recommendation.shared_school:
        evidence_line = f" I noticed that we both attended {recommendation.shared_school}."
    elif request.draft_type == "shared_previous_employer" and recommendation.shared_employer:
        evidence_line = f" I noticed that we both worked at {recommendation.shared_employer}."
    elif request.draft_type in {"shared_school", "shared_previous_employer"}:
        raise HTTPException(status_code=422, detail="The selected shared evidence is not available.")
    if request.draft_type == "referral_request":
        ask = "If you feel comfortable, would you be open to sharing your perspective on the role or referral process?"
    elif request.draft_type == "potential_hiring_manager_introduction":
        ask = "I would value any public context you can share about the function and what the team values."
    elif request.draft_type == "follow_up":
        ask = "I wanted to follow up in case you have a moment to share any perspective."
    elif request.draft_type == "thank_you":
        ask = "Thank you for taking the time to consider my note."
    else:
        ask = "I would appreciate any perspective you are comfortable sharing about the opportunity."
    detail = f" {request.user_details.strip()}" if request.user_details else ""
    draft = (
        f"{greeting}\n\nI’m {name}, and I’m applying for the {job.title} role at {job.company}."
        f"{evidence_line}{detail} {ask}\n\nThank you for your time,\n{name}"
    )
    record_audit(db, user.id, "people_outreach_draft_generated", {
        "job_id": job_id, "recommendation_id": recommendation.id, "draft_type": request.draft_type,
        "automatically_sent": False, "category": candidate.candidate_category,
    })
    db.commit()
    return {"draft": draft, "requires_user_review": True, "sent": False}


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
    recommendation, _, _ = owned_recommendation(db, user, job_id, recommendation_id)
    feedback = PeopleRecommendationFeedback(
        user_id=user.id, recommendation_id=recommendation.id,
        relevance_rating=request.relevance_rating,
        employment_current_rating=request.employment_current_rating,
        information_correct_rating=request.information_correct_rating,
        contacted=request.contacted, received_response=request.received_response,
        incorrect_reason=request.incorrect_reason,
    )
    db.add(feedback)
    metric(
        "people_recommendation_feedback_total",
        category="all",
        scoring_version=SCORING_VERSION,
    )
    if request.information_correct_rating == "incorrect":
        recommendation.suppressed_at = _now()
        metric(
            "people_recommendation_reported_incorrect_total",
            scoring_version=SCORING_VERSION,
        )
    record_audit(db, user.id, "people_recommendation_feedback", {
        "job_id": job_id, "recommendation_id": recommendation.id,
        "reported_incorrect": request.information_correct_rating == "incorrect",
    })
    db.commit()
    return {"accepted": True, "suppressed": recommendation.suppressed_at is not None}
