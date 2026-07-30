from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import record_audit
from app.core.config import settings
from app.models.entities import (
    Education,
    Experience,
    JobPeopleCandidate,
    JobPosting,
    PeopleDiscoveryRun,
    PeopleEmploymentVerificationRun,
    PeopleProviderOperationUsage,
    PeopleRecommendationFeedback,
    ProfessionalPerson,
    ProfessionalPersonSource,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
)
from app.people.circuit import CircuitSnapshot, circuit_state
from app.people.coalescing import (
    provider_search_coalescer,
    search_identity,
)
from app.people.employment_validation import (
    EMPLOYMENT_EVIDENCE_VERSION,
    EMPLOYMENT_VALIDATION_VERSION,
    EmploymentValidationResult,
    validate_current_employment,
)
from app.people.errors import PeopleErrorCode, code_for_reason
from app.people.feature_flags import is_beta
from app.people.intelligence import extract_job_people_profile
from app.people.observability import metric
from app.people.pdl_company import PDL_COMPANY_RESOLUTION_VERSION
from app.people.pdl_query import PDL_QUERY_LADDER_VERSION
from app.people.provider_usage import (
    ProviderUsageContext,
    ProviderUsagePersistenceError,
)
from app.people.providers import (
    APOLLO_ENRICHMENT_ADAPTER_VERSION,
    PDL_DISCOVERY_STRATEGY_VERSION,
    PDLPeopleProvider,
    ProviderUnavailable,
    get_email_provider,
    get_people_provider,
)
from app.people.providers import (
    account_fingerprint as provider_account_fingerprint,
)
from app.people.quota import (
    quota_snapshot,
    reserve_user_discovery,
)
from app.people.schemas import (
    FeedbackRequest,
    JobPeopleSearchProfile,
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

# One identifier for "results produced under these semantics are comparable".
#
# Two Toshiba jobs displayed contradictory states because a run recorded before
# PDL's 404 was understood as "no profiles matched" kept being treated as
# current: the fingerprint did not change when the *meaning* of a stored result
# did. Composing the version from every input that can reinterpret a stored
# result makes that impossible — changing any component below retires every run
# recorded under the old one.
#
# Components: provider response contract, query strategy, company resolution,
# ranking/scoring, and the stored result schema.
PEOPLE_RESULT_SCHEMA_VERSION = "people-result-v2"
# Bump when the deterministic outreach templates change, so a client caching a
# draft knows to regenerate.
OUTREACH_TEMPLATE_VERSION = "people-outreach-template-v2"
PEOPLE_SEARCH_CONTRACT_VERSION = ":".join(
    (
        # PDL 404 now means "no profiles matched" rather than a rejected
        # request; every run recorded before that reads its own failure_code
        # with the opposite meaning.
        "pdl-person-search-v3",
        PDL_DISCOVERY_STRATEGY_VERSION,
        PDL_QUERY_LADDER_VERSION,
        PDL_COMPANY_RESOLUTION_VERSION,
        SCORING_VERSION,
        PEOPLE_RESULT_SCHEMA_VERSION,
    )
)
# Key under which each run records the contract it was produced under. Runs
# without it predate versioning and are legacy by definition.
CONTRACT_VERSION_KEY = "search_contract_version"


def run_contract_version(run: PeopleDiscoveryRun | None) -> str | None:
    """The contract a stored run was produced under, or ``None`` when legacy."""

    if run is None:
        return None
    value = (run.company_context or {}).get(CONTRACT_VERSION_KEY)
    return value if isinstance(value, str) and value else None


def run_is_compatible(run: PeopleDiscoveryRun | None) -> bool:
    """May this stored run be reused as a current result?

    A legacy run — one with no recorded contract, or one recorded under a
    different contract — must not be served: its status and failure_code were
    written under semantics that no longer hold.
    """

    return run_contract_version(run) == PEOPLE_SEARCH_CONTRACT_VERSION


DISPLAYABLE_EMPLOYMENT_STATUSES = frozenset(
    {
        "confirmed_exact_company_verified",
        "exact_company_current_but_unverified_freshness",
    }
)

# Every message here is user-facing. Each one must name the *actual* problem:
# the generic "paused after repeated provider failures" line is reserved for a
# genuinely open provider circuit and must never appear for an empty result, an
# unresolved domain, a per-user budget, or a request-specific rejection.
_SAFE_PROVIDER_MESSAGES = {
    "provider_unauthorized": (
        "People search is temporarily unavailable because the provider "
        "connection needs attention."
    ),
    "provider_forbidden": (
        "People search is temporarily unavailable because the provider "
        "connection needs attention."
    ),
    "provider_not_configured": (
        "People search is temporarily unavailable because the provider "
        "connection needs attention."
    ),
    "provider_configuration_circuit_open": (
        "People search is temporarily unavailable because the provider "
        "connection needs attention."
    ),
    "provider_master_key_required_or_forbidden": (
        "Apollo complete-profile access is unavailable for the configured account."
    ),
    "provider_rate_limited": (
        "The people provider is temporarily rate-limited. Try again after the "
        "displayed retry time."
    ),
    "provider_timeout": (
        "The people provider is temporarily unavailable. Cached results are "
        "shown when available."
    ),
    "provider_network_error": (
        "The people provider is temporarily unavailable. Cached results are "
        "shown when available."
    ),
    "provider_unavailable": (
        "The people provider is temporarily unavailable. Cached results are "
        "shown when available."
    ),
    "provider_circuit_open": (
        "The people provider is temporarily unavailable. Cached results are "
        "shown when available."
    ),
    "provider_budget_exceeded": (
        "People search is paused for today because the provider account's "
        "daily search budget is used up."
    ),
    "provider_user_limit_exceeded": "You have reached today's people-search limit.",
    "company_domain_unresolved": (
        "We could not confidently identify this company in the people provider."
    ),
    "provider_route_invalid": (
        "We could not complete this search because the provider request was "
        "invalid."
    ),
    "no_results": (
        "No strong recruiter, manager, or referral matches were found for this "
        "company yet."
    ),
    "provider_schema_error": "The people provider returned an unsupported response.",
    "provider_request_invalid": (
        "We could not complete this search because the provider request was "
        "invalid."
    ),
    "provider_response_invalid": "The people provider returned an unsupported response.",
    "provider_request_cancelled": "The people search was cancelled before it completed.",
}


def _safe_provider_message(reason: str) -> str:
    return _SAFE_PROVIDER_MESSAGES.get(
        reason, "Professional data providers are temporarily unavailable."
    )


# Which run status a typed failure produces. Keeping these distinct is what
# lets the UI say "we could not identify the domain" instead of implying the
# provider is down.
_STATUS_FOR_CODE: dict[PeopleErrorCode, str] = {
    PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED: "domain_unresolved",
    # A genuinely malformed request. Reachable now only by a real request
    # defect: a provider that answered a valid query with zero records is
    # handled as an empty result, not as a rejection.
    PeopleErrorCode.INVALID_INPUT: "invalid_request",
    PeopleErrorCode.USER_BUDGET_EXHAUSTED: "user_budget_exhausted",
    PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED: "provider_budget_exhausted",
    PeopleErrorCode.AUTHENTICATION_FAILED: "provider_configuration_error",
    PeopleErrorCode.AUTHORIZATION_FAILED: "provider_configuration_error",
}

# Every terminal state that is a failure rather than a result. Used wherever the
# code previously hard-coded {"provider_unavailable", "persistence_error"}.
PROVIDER_ERROR_STATUSES = frozenset(
    {
        "provider_unavailable",
        "persistence_error",
        "domain_unresolved",
        "invalid_request",
        "user_budget_exhausted",
        "provider_budget_exhausted",
        "provider_configuration_error",
    }
)

# When several categories fail differently, report the most actionable one.
# Configuration beats budget beats rate limiting beats transient beats
# request-scoped, because that is the order in which an operator or user can
# actually do something about it.
_FAILURE_PRIORITY: tuple[PeopleErrorCode, ...] = (
    PeopleErrorCode.AUTHENTICATION_FAILED,
    PeopleErrorCode.AUTHORIZATION_FAILED,
    PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED,
    PeopleErrorCode.USER_BUDGET_EXHAUSTED,
    PeopleErrorCode.RATE_LIMITED,
    PeopleErrorCode.PROVIDER_SERVER_ERROR,
    PeopleErrorCode.PROVIDER_TIMEOUT,
    PeopleErrorCode.NETWORK_ERROR,
    PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED,
    PeopleErrorCode.INVALID_INPUT,
    PeopleErrorCode.REQUEST_CANCELLED,
    PeopleErrorCode.UNKNOWN_PROVIDER_ERROR,
)


# Failures that mean the user's unit bought nothing. Configuration and
# authentication problems are JobPilot's to fix; an unresolved company is our
# own missing data; a cancelled request never ran. A provider that answered
# — including a truthful no-match — is a completed search and stays charged.
_REFUNDABLE_CODES: frozenset[PeopleErrorCode] = frozenset(
    {
        PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED,
        PeopleErrorCode.AUTHENTICATION_FAILED,
        PeopleErrorCode.AUTHORIZATION_FAILED,
        PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED,
        PeopleErrorCode.REQUEST_CANCELLED,
        PeopleErrorCode.INVALID_INPUT,
    }
)


def _provider_work_started(provider: object) -> bool:
    """Did any external search actually leave the building?

    Company resolution alone does not count: it is JobPilot deciding whether it
    can search at all, not the search the user asked for.
    """

    return bool(
        [
            call
            for call in getattr(provider, "strategy_calls", [])
            if isinstance(call, dict)
        ]
    )


def _refundable_failure(code: PeopleErrorCode, provider: object) -> bool:
    if code in _REFUNDABLE_CODES:
        return True
    # An open circuit that blocked the request before any search ran means no
    # provider work happened, so the unit is returned.
    return not _provider_work_started(provider)


def _dominant_failure(reasons: list[str]) -> str | None:
    """Pick the reason a human most needs to see out of a mixed failure list."""

    if not reasons:
        return None
    by_code: dict[PeopleErrorCode, str] = {}
    for reason in reasons:
        by_code.setdefault(code_for_reason(reason), reason)
    for code in _FAILURE_PRIORITY:
        if code in by_code:
            return by_code[code]
    return reasons[0]


def _log_provider_failure(exc: ProviderUnavailable, discovery_run_id: int) -> None:
    logger.warning(
        "people_provider_failure reason=%s error_code=%s provider=%s "
        "request_scoped=%s http_status=%s retry_after=%s duration_ms=%s "
        "discovery_run_id=%s",
        exc.reason,
        exc.code,
        exc.provider,
        exc.request_scoped,
        exc.http_status if exc.http_status is not None else "none",
        exc.retry_after_seconds if exc.retry_after_seconds is not None else "none",
        round(exc.duration_ms, 2) if exc.duration_ms is not None else "none",
        discovery_run_id,
    )
    metric(
        "people_provider_requests_total",
        provider=exc.provider,
        status="error",
        error_code=str(exc.code),
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
    payload["search_contract_version"] = PEOPLE_SEARCH_CONTRACT_VERSION
    if settings.people_primary_provider == "apollo":
        payload["provider_adapter_version"] = APOLLO_ENRICHMENT_ADAPTER_VERSION
    elif settings.people_primary_provider == "pdl":
        payload["provider_adapter_version"] = PDL_DISCOVERY_STRATEGY_VERSION
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _job_or_404(db: Session, job_id: int) -> JobPosting:
    job = db.get(JobPosting, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _fresh_candidates(
    db: Session,
    job_id: int,
    user_id: int,
) -> list[JobPeopleCandidate]:
    return list(
        db.scalars(
            select(JobPeopleCandidate)
            .join(
                UserJobPeopleRecommendation,
                UserJobPeopleRecommendation.job_people_candidate_id
                == JobPeopleCandidate.id,
            )
            .where(
                JobPeopleCandidate.job_id == job_id,
                UserJobPeopleRecommendation.user_id == user_id,
                UserJobPeopleRecommendation.job_id == job_id,
                UserJobPeopleRecommendation.suppressed_at.is_(None),
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
    cutoff = _now() - timedelta(days=_people_result_ttl_days())
    runs = db.scalars(
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
    for run in runs:
        if not run_is_compatible(run):
            # Recorded under semantics that no longer hold: its "no match" may
            # have meant something else entirely.
            continue
        if (
            settings.people_employment_secondary_verification_enabled
            and not bool(
                (run.company_context or {}).get(
                    "secondary_employment_verification_enabled"
                )
            )
        ):
            # Enabling secondary verification re-evaluates unresolved/no-match
            # runs, while successful current candidates remain reusable.
            continue
        return run
    return None


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


_RETRYABLE_PROVIDER_ERRORS = frozenset(
    {
        "provider_circuit_open",
        "provider_rate_limited",
        "provider_timeout",
        "provider_network_error",
        "provider_unavailable",
        "discovery_failed",
        "recommendation_commit_failed",
        # An unresolved domain is worth retrying once the company record is
        # enriched, and retrying costs no provider credit.
        "company_domain_unresolved",
    }
)
_NON_RETRYABLE_PROVIDER_ERRORS = frozenset(
    {
        "provider_not_configured",
        "provider_unauthorized",
        "provider_forbidden",
        "provider_configuration_circuit_open",
        "provider_master_key_required_or_forbidden",
        "provider_request_invalid",
        "provider_response_invalid",
        "provider_budget_exceeded",
        "provider_user_limit_exceeded",
        "provider_request_cancelled",
    }
)


def _current_provider_error_run(
    db: Session,
    *,
    job_id: int,
    user_id: int,
    fingerprint: str,
) -> PeopleDiscoveryRun | None:
    latest = _latest_run(
        db,
        job_id=job_id,
        user_id=user_id,
        fingerprints=[fingerprint],
    )
    if latest is None or not run_is_compatible(latest):
        # A legacy failure must never pin a job to a status produced under
        # different provider semantics — that is what left one Toshiba job
        # permanently showing "the provider request was invalid".
        return None
    return latest if latest.status in PROVIDER_ERROR_STATUSES else None


def _provider_error_retry_state(
    run: PeopleDiscoveryRun,
    *,
    now: datetime | None = None,
) -> tuple[bool, int | None, datetime | None]:
    reason = run.failure_code or "provider_unavailable"
    if reason == "provider_schema_error" or reason in _NON_RETRYABLE_PROVIDER_ERRORS:
        return False, None, None
    if reason not in _RETRYABLE_PROVIDER_ERRORS:
        return False, None, None
    value = (run.company_context or {}).get("retry_eligible_at")
    try:
        retry_at = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        retry_at = None
    if retry_at is None:
        completed = run.completed_at or run.started_at
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        retry_at = completed + timedelta(seconds=_provider_retry_seconds(reason))
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or _now()
    remaining = max(0, int((retry_at - current).total_seconds() + 0.999))
    return remaining == 0, remaining or None, retry_at


def _provider_error_blocks_discovery(run: PeopleDiscoveryRun) -> bool:
    retry_eligible, _, _ = _provider_error_retry_state(run)
    return not retry_eligible


def _provider_error_context(reason: str, *, now: datetime) -> dict[str, object]:
    if reason in _RETRYABLE_PROVIDER_ERRORS:
        retry_at = now + timedelta(seconds=_provider_retry_seconds(reason))
        return {
            "provider_error_retry_policy": "bounded_explicit_retry",
            "retry_eligible_at": retry_at.isoformat(),
        }
    if reason in {
        "provider_schema_error",
        "provider_request_invalid",
        "provider_response_invalid",
    }:
        return {
            "provider_error_retry_policy": "adapter_version_change_required",
            "retry_eligible_at": None,
        }
    return {
        "provider_error_retry_policy": "non_retryable",
        "retry_eligible_at": None,
    }


def _configure_provider_usage(
    provider: object,
    *,
    db: Session,
    user_id: int,
    job_id: int,
    discovery_run_id: int,
    adapter_version: str,
) -> None:
    configure = getattr(provider, "configure_usage", None)
    if not callable(configure):
        return
    configure(
        ProviderUsageContext(
            user_id=user_id,
            job_id=job_id,
            discovery_run_id=discovery_run_id,
            adapter_version=adapter_version,
        ),
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ),
    )


def _durable_usage_summary(
    db: Session,
    discovery_run_id: int,
) -> dict[str, object]:
    rows = db.execute(
        select(
            PeopleProviderOperationUsage.request_count,
            PeopleProviderOperationUsage.credits_reported,
            PeopleProviderOperationUsage.credits_estimated,
            PeopleProviderOperationUsage.budget_units,
            PeopleProviderOperationUsage.credit_status,
        ).where(
            PeopleProviderOperationUsage.discovery_run_id
            == discovery_run_id
        )
    ).all()
    operation_counts: dict[str, int] = defaultdict(int)
    operation_rows = db.execute(
        select(
            PeopleProviderOperationUsage.operation_type,
            PeopleProviderOperationUsage.request_count,
        ).where(
            PeopleProviderOperationUsage.discovery_run_id
            == discovery_run_id
        )
    ).all()
    for operation_type, request_count in operation_rows:
        operation_counts[str(operation_type)] += int(request_count)
    return {
        "request_count": sum(int(row.request_count) for row in rows),
        "reported_credits": sum(
            int(row.credits_reported or 0) for row in rows
        ),
        "estimated_credits": sum(
            int(row.credits_estimated or 0) for row in rows
        ),
        "unknown_credit_operations": sum(
            int(row.request_count)
            for row in rows
            if row.credit_status == "unknown"
        ),
        "budget_units": sum(int(row.budget_units) for row in rows),
        "operation_counts": dict(sorted(operation_counts.items())),
    }


def _provider_pipeline_outcomes(
    provider: object,
    usage_summary: dict[str, object],
) -> dict[str, str]:
    operation_counts = usage_summary.get("operation_counts", {})
    if not isinstance(operation_counts, dict):
        operation_counts = {}
    metrics = getattr(provider, "enrichment_safe_metrics", {})
    bulk_calls = int(operation_counts.get("bulk_enrichment", 0))
    single_calls = int(operation_counts.get("complete_person_by_id", 0))
    if metrics.get("bulk_payload_validation_failed"):
        bulk_outcome = "request_rejected_fallback_continued"
    elif metrics.get("bulk_capability_skipped"):
        bulk_outcome = "skipped_by_capability_cache"
    elif bulk_calls:
        bulk_outcome = "completed"
    else:
        bulk_outcome = "not_used"
    return {
        "bulk_enrichment": bulk_outcome,
        "bounded_single_fallback": (
            "completed" if single_calls else "not_used"
        ),
    }


def _normalized_linkedin(value: str | None) -> str | None:
    return safe_profile_url(value)


def _display_name(value: str) -> str:
    stripped = value.strip()
    if re.fullmatch(r"[a-z]+(?: [a-z]+)+", stripped):
        return " ".join(part.capitalize() for part in stripped.split())
    return stripped


def _same_identity(left: ProviderPerson, right: ProviderPerson) -> bool:
    left_url, right_url = _normalized_linkedin(left.linkedin_url), _normalized_linkedin(right.linkedin_url)
    if left_url and right_url:
        return left_url == right_url
    if left.provider == right.provider and left.provider_person_id == right.provider_person_id:
        return True
    # Names, employers, and titles are not stable identity keys. Without a
    # provider identifier or an allowlisted professional profile URL, keep the
    # records separate.
    return False


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
        _configure_provider_usage(
            provider,
            db=db,
            user_id=user_id,
            job_id=job_id,
            discovery_run_id=discovery_run_id,
            adapter_version=EMPLOYMENT_EVIDENCE_VERSION,
        )
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
    provider = settings.people_primary_provider.lower()
    global_budget = (
        settings.people_pdl_daily_credit_budget
        if provider == "pdl"
        else settings.people_daily_credit_budget
    )
    per_user_budget = (
        settings.people_pdl_per_user_daily_limit
        if provider == "pdl"
        else settings.people_per_user_daily_limit
    )
    has_durable_usage = (
        select(PeopleProviderOperationUsage.id)
        .where(
            PeopleProviderOperationUsage.discovery_run_id
            == PeopleDiscoveryRun.id
        )
        .exists()
    )
    legacy_global_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0)).where(
            PeopleDiscoveryRun.started_at >= start,
            PeopleDiscoveryRun.provider == provider,
            ~has_durable_usage,
        )
    ) or 0
    legacy_user_used = db.scalar(
        select(func.coalesce(func.sum(PeopleDiscoveryRun.provider_credits_used), 0)).where(
            PeopleDiscoveryRun.user_id == user_id,
            PeopleDiscoveryRun.started_at >= start,
            PeopleDiscoveryRun.provider == provider,
            ~has_durable_usage,
        )
    ) or 0
    durable_global_used = db.scalar(
        select(
            func.coalesce(
                func.sum(PeopleProviderOperationUsage.budget_units),
                0,
            )
        ).where(
            PeopleProviderOperationUsage.occurred_at >= start,
            PeopleProviderOperationUsage.provider == provider,
        )
    ) or 0
    durable_user_used = db.scalar(
        select(
            func.coalesce(
                func.sum(PeopleProviderOperationUsage.budget_units),
                0,
            )
        ).where(
            PeopleProviderOperationUsage.user_id == user_id,
            PeopleProviderOperationUsage.occurred_at >= start,
            PeopleProviderOperationUsage.provider == provider,
        )
    ) or 0
    global_used = int(legacy_global_used) + int(durable_global_used)
    user_used = int(legacy_user_used) + int(durable_user_used)
    # Both limits below are measured in provider *credit units*, not user
    # actions, so neither may be presented as the user's search limit. The
    # user's allowance lives in app.people.quota and is counted in actions.
    if global_budget and global_used >= global_budget:
        metric(
            "people_budget_rejections_total",
            provider=provider,
            status="provider_account_budget",
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_PROVIDER_BUDGET_EXCEEDED",
                "message": (
                    "People search is temporarily unavailable because the "
                    "provider budget has been reached."
                ),
                "availability_reason": "provider_budget_exceeded",
                "retryable": False,
            },
        )
    if per_user_budget and user_used >= per_user_budget:
        metric(
            "people_budget_rejections_total",
            provider=provider,
            status="provider_per_user_budget",
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_PROVIDER_BUDGET_EXCEEDED",
                "message": (
                    "People search is temporarily unavailable because the "
                    "provider budget has been reached."
                ),
                "availability_reason": "provider_budget_exceeded",
                "retryable": False,
            },
        )


def _pdl_budget_allows_call(
    db: Session,
    user_id: int,
    requested_maximum: int,
) -> bool:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    global_used = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(PeopleProviderOperationUsage.budget_units),
                    0,
                )
            ).where(
                PeopleProviderOperationUsage.provider == "pdl",
                PeopleProviderOperationUsage.occurred_at >= start,
            )
        )
        or 0
    )
    user_used = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(PeopleProviderOperationUsage.budget_units),
                    0,
                )
            ).where(
                PeopleProviderOperationUsage.provider == "pdl",
                PeopleProviderOperationUsage.user_id == user_id,
                PeopleProviderOperationUsage.occurred_at >= start,
            )
        )
        or 0
    )
    return bool(
        settings.people_pdl_daily_credit_budget > 0
        and settings.people_pdl_per_user_daily_limit > 0
        and global_used + requested_maximum
        <= settings.people_pdl_daily_credit_budget
        and user_used + requested_maximum
        <= settings.people_pdl_per_user_daily_limit
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


def _primary_provider_fingerprint() -> str:
    """Account fingerprint for whichever provider is configured as primary."""

    key = (
        settings.pdl_api_key
        if settings.people_primary_provider.lower() == "pdl"
        else settings.apollo_api_key
    )
    return provider_account_fingerprint(key)


def people_circuit_snapshot(operation: str = "people_search") -> CircuitSnapshot:
    """Current circuit health for the configured primary people provider."""

    return circuit_state(
        provider=settings.people_primary_provider.lower(),
        account_fingerprint=_primary_provider_fingerprint(),
        operation=operation,
    )


def _people_result_ttl_days() -> int:
    if settings.people_primary_provider.lower() == "pdl":
        return settings.people_pdl_result_ttl_days
    return settings.people_result_ttl_days


def _strongest_category_candidates(
    candidates: dict[PeopleCategory, list[PreliminaryCandidate]],
) -> dict[PeopleCategory, list[PreliminaryCandidate]]:
    """Assign each provider identity to its strongest-scoring category.

    Category scoring still evaluates every plausible role. The UI-facing
    candidate set is exclusive, preventing one paid PDL record from appearing
    as a recruiter, manager, and referrer simultaneously.
    """
    category_order = {
        category: index
        for index, category in enumerate(_PEOPLE_CATEGORIES)
    }
    winners: dict[tuple[str, str], PreliminaryCandidate] = {}
    for category in _PEOPLE_CATEGORIES:
        for item in candidates.get(category, []):
            key = (item[2].provider, item[2].provider_person_id)
            incumbent = winners.get(key)
            affinity = _title_category_affinity(item[2].current_title)
            item_affinity = item[1] == affinity
            incumbent_affinity = bool(
                incumbent and incumbent[1] == affinity
            )
            if incumbent is None or (
                item_affinity,
                item[0],
                -category_order[item[1]],
            ) > (
                incumbent_affinity,
                incumbent[0],
                -category_order[incumbent[1]],
            ):
                winners[key] = item
    selected = {
        category: [] for category in _PEOPLE_CATEGORIES
    }
    for item in winners.values():
        selected[item[1]].append(item)
    return selected


def _title_category_affinity(title: str) -> PeopleCategory:
    normalized = normalize_text(title)
    if any(
        marker in normalized
        for marker in (
            "recruiter",
            "recruiting",
            "talent acquisition",
            "talent partner",
        )
    ):
        return "likely_recruiter"
    leadership_markers = {
        "manager",
        "director",
        "head",
        "vp",
        "president",
        "chief",
        "executive",
    }
    if leadership_markers & set(normalized.split()):
        return "potential_hiring_manager"
    return "potential_referrer"


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


def _safe_user_reference(user_id: int) -> str:
    """Stable, non-reversible user reference for logs."""

    return hashlib.sha256(f"people-log-user:{user_id}".encode()).hexdigest()[:12]


def _log_search_orchestration(
    *,
    run: PeopleDiscoveryRun,
    job_id: int,
    user_id: int,
    profile: JobPeopleSearchProfile,
    strategy: DiscoveryStrategy,
    failures: list[str],
    displayed: int,
    started: float,
    cache: str,
) -> None:
    """One structured line per orchestration request.

    Deliberately excludes API keys, authorization headers, raw provider
    payloads, and any personal contact record. The company name is business
    data the job already carries; the user is referenced by a hashed id.
    """

    dominant = _dominant_failure(failures)
    snapshot = people_circuit_snapshot()
    logger.info(
        "people_search_orchestration run_id=%s job_id=%s user_ref=%s "
        "raw_company=%r normalized_company=%r canonical_domain=%s "
        "domain_source=%s domain_confidence=%.2f role_family=%s provider=%s "
        "strategy=%s cache=%s coalesced=%s circuit=%s error_code=%s "
        "retry_after=%s latency_ms=%.1f results=%s status=%s",
        run.id,
        job_id,
        _safe_user_reference(user_id),
        profile.company_raw_name or profile.company_name,
        profile.company_normalized_name,
        profile.company_domain or "unresolved",
        profile.company_evidence_source,
        profile.domain_confidence,
        profile.role_family or "none",
        run.provider,
        strategy,
        cache,
        provider_search_coalescer.inflight_count > 0,
        snapshot.as_label(),
        str(code_for_reason(dominant)) if dominant else "none",
        snapshot.retry_after_seconds if snapshot.retry_after_seconds else "none",
        (time.monotonic() - started) * 1000,
        displayed,
        run.status,
    )
    metric(
        "people_search_requests_total",
        provider=run.provider,
        status=run.status,
        error_code=str(code_for_reason(dominant)) if dominant else "none",
    )
    metric(
        "people_domain_resolution_total",
        result="resolved" if profile.company_domain else "unresolved",
        source=profile.company_evidence_source,
    )


def _searched_the_provider(provider: object, queries: list[PeopleSearchQuery]) -> bool:
    """Did a provider call actually happen for this category?

    An empty result only means "nobody matched" when the provider was asked.
    A category that produced no query at all was never asked.
    """

    if getattr(provider, "search_calls", 0):
        return True
    return bool(queries)


async def _coalesced_search(
    provider: object,
    *,
    profile: JobPeopleSearchProfile,
    category: PeopleCategory,
    queries: list[PeopleSearchQuery],
    limit: int,
    adapter_version: str,
    company: object = None,
) -> list[ProviderPerson]:
    """One provider call per canonical search, under a bounded concurrency cap.

    Ten job cards for the same company and role family expanded at once now
    share a single provider request instead of producing ten. The provider is
    only ever reached through this path so the concurrency limit cannot be
    bypassed.

    When the employer resolved to a PDL company identity, the progressive
    ladder is used; otherwise the caller's prepared queries are.
    """

    provider_name = getattr(provider, "provider_name", "unknown")
    use_ladder = bool(
        company is not None
        and getattr(company, "searchable", False)
        and hasattr(provider, "search_current_company_people")
    )
    key = search_identity(
        provider=provider_name,
        adapter_version=(
            f"{adapter_version}:{PDL_QUERY_LADDER_VERSION}"
            if use_ladder
            else adapter_version
        ),
        company_domain=(
            getattr(company, "pdl_company_id", None) or profile.company_domain
            if use_ladder
            else profile.company_domain
        ),
        company_name=profile.company_normalized_name or profile.company_name,
        role_family=profile.role_family,
        category=category,
        location=profile.location,
        # Location is a soft signal in every current query builder, so it must
        # not fragment the coalescing key.
        location_material=False,
    )

    def _call():
        if use_ladder:
            return provider.search_current_company_people(
                company=company,
                category=category,
                role_family=profile.role_family,
                job_location=profile.location,
                limit=limit,
            )
        return provider.search_people_category(queries, limit=limit)

    started = time.monotonic()
    try:
        rows = await provider_search_coalescer.run(key, provider_name, _call)
    finally:
        metric(
            "people_provider_latency",
            round((time.monotonic() - started) * 1000, 2),
            provider=provider_name,
            category=category,
        )
    metric(
        "people_provider_requests_total",
        provider=provider_name,
        status="ok",
        category=category,
    )
    return rows


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
    fresh = _fresh_candidates(db, job_id, user.id)
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

    cached_provider_error = _current_provider_error_run(
        db,
        job_id=job_id,
        user_id=user.id,
        fingerprint=fingerprint,
    )
    if (
        cached_provider_error is not None
        and _provider_error_blocks_discovery(cached_provider_error)
    ):
        metric(
            "people_discovery_cache_hits_total",
            provider="database_provider_error",
            scoring_version=SCORING_VERSION,
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

    # Burst first: a rejected burst must not consume a daily unit.
    rate_limit(f"discover:{user.id}", settings.people_discovery_rate_limit_per_hour)
    # Provider cost control. Its exhaustion is an operational stop, not the
    # user's entitlement running out, so it is checked before the reservation
    # and reported with its own code.
    _budget_check(db, user.id)
    with _redis_lock(job_id, fingerprint) as acquired:
        if not acquired:
            # A coalesced waiter pays nothing: the leader's single unit covers
            # the work both callers are waiting on.
            metric("people_discovery_coalesced_waiter_total", provider=settings.people_primary_provider)
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
        if _fresh_candidates(db, job_id, user.id):
            return recommendations_payload(db, user, job_id)
        if _fresh_no_match_run(
            db,
            job_id=job_id,
            user_id=user.id,
            fingerprint=fingerprint,
        ) is not None:
            return recommendations_payload(db, user, job_id)
        cached_provider_error = _current_provider_error_run(
            db,
            job_id=job_id,
            user_id=user.id,
            fingerprint=fingerprint,
        )
        if (
            cached_provider_error is not None
            and _provider_error_blocks_discovery(cached_provider_error)
        ):
            return recommendations_payload(db, user, job_id)
        # Past every cache and coalescing opportunity: this is a genuine new
        # search, so exactly one user unit is reserved here — never inside the
        # category loop, the strategy ladder, or a provider adapter.
        reservation = reserve_user_discovery(db, user)
        db.commit()
        metric("people_user_discoveries_total", provider=settings.people_primary_provider)
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
            "provider_adapter_version": (
                APOLLO_ENRICHMENT_ADAPTER_VERSION
                if settings.people_primary_provider == "apollo"
                else PDL_DISCOVERY_STRATEGY_VERSION
                if settings.people_primary_provider == "pdl"
                else "provider-neutral-v1"
            ),
            "secondary_employment_verification_enabled": (
                settings.people_employment_secondary_verification_enabled
            ),
            "discovery_strategy_version": DISCOVERY_STRATEGY_VERSION,
            "discovery_strategy": strategy,
            CONTRACT_VERSION_KEY: PEOPLE_SEARCH_CONTRACT_VERSION,
        }
        run = PeopleDiscoveryRun(
            job_id=job_id, user_id=user.id, status="running",
            provider=settings.people_primary_provider, query_fingerprint=fingerprint,
            company_context=company_context, category_diagnostics={},
        )
        db.add(run)
        db.commit()
        _configure_provider_usage(
            provider,
            db=db,
            user_id=user.id,
            job_id=job_id,
            discovery_run_id=run.id,
            adapter_version=str(
                company_context["provider_adapter_version"]
            ),
        )
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
                "query_executed": False,
                "provider_call_count": 0,
                "normalized_profile_count": 0,
                "exact_company_current_profiles": 0,
                "former_employees": 0,
                "conflicting_employees": 0,
                "stale_or_insufficient_evidence": 0,
                "below_title_relevance": 0,
                "below_confidence_threshold": 0,
                "deduplicated_into_another_identity": 0,
                "assigned_to_another_category": 0,
                "assigned_to_stronger_category": 0,
                "accepted": 0,
                "display_cap_excluded": 0,
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
                "employment_unresolved_candidates": 0,
            }
            for category in _PEOPLE_CATEGORIES
        }
        failures: list[str] = []
        # Categories the provider answered successfully with zero people. These
        # are results, not failures, and are what separates a truthful
        # "no strong matches" from "the provider rejected the request".
        no_match_categories: set[str] = set()
        searched = 0
        enriched: list[ProviderPerson] = []
        displayed: dict[str, int] = defaultdict(int)
        employment_outcomes: dict[str, int] = defaultdict(int)
        pdl_company_identity = None
        pipeline_stage = "search"
        try:
            exact_queries = {
                category: (
                    build_category_search_queries(profile, category)
                    if strategy == "exact"
                    else build_broadened_search_queries(profile, category)
                )
                for category in _PEOPLE_CATEGORIES
            }
            category_pdl_search = bool(
                strategy == "exact"
                and settings.people_primary_provider == "pdl"
                and hasattr(provider, "search_people_category")
            )
            # Resolve the employer to a stable PDL company id before searching
            # anyone. Searching by display name alone depends on the job feed
            # and the provider spelling the company identically, which is what
            # left Toshiba Global Commerce and Vanderbilt Health with nothing.
            if category_pdl_search and hasattr(provider, "resolve_company"):
                try:
                    pdl_company_identity = await provider.resolve_company(
                        raw_name=profile.company_raw_name or profile.company_name,
                        normalized_name=profile.company_normalized_name,
                        aliases=tuple(profile.company_aliases),
                        verified_domain=profile.company_domain,
                    )
                except ProviderUnavailable as exc:
                    failures.append(exc.reason)
                    _log_provider_failure(exc, run.id)
                    pdl_company_identity = None
                if pdl_company_identity is not None:
                    company_context.update(pdl_company_identity.safe_summary())
                # No verified company evidence at all — no PDL company id and no
                # verified domain. Searching anyway would return strangers who
                # merely work somewhere similarly named, so nothing is searched
                # and no credit is spent. This is distinct from "we searched and
                # nobody matched".
                company_unresolved = not (
                    (pdl_company_identity is not None and pdl_company_identity.searchable)
                    or profile.company_domain
                )
                if company_unresolved:
                    failures.append("company_domain_unresolved")
                    metric(
                        "people_domain_resolution_total",
                        result="unresolved",
                        source=profile.company_evidence_source,
                    )
                    category_pdl_search = False
            total_pdl_remaining = (
                settings.people_pdl_max_results_per_discovery
            )
            category_limits = {
                "likely_recruiter": settings.people_pdl_recruiter_results,
                "potential_hiring_manager": settings.people_pdl_manager_results,
                "potential_referrer": settings.people_pdl_referral_results,
            }
            for category in _PEOPLE_CATEGORIES:
                category_rows: list[ProviderPerson] = []
                queries = exact_queries[category]
                if category_pdl_search:
                    for query in queries:
                        diagnostics[category]["search_queries"].append({
                            "title_group": query.title_group,
                            "titles": query.titles,
                            "company_match_kind": query.company_match_kind,
                        })
                        diagnostics[category]["title_groups"].append(
                            query.title_group
                        )
                        diagnostics[category]["seniorities_used"].extend(
                            query.seniorities
                        )
                    call_limit = max(
                        0,
                        min(
                            category_limits[category],
                            total_pdl_remaining,
                        ),
                    )
                    if call_limit and _pdl_budget_allows_call(
                        db,
                        user.id,
                        call_limit,
                    ):
                        diagnostics[category]["query_executed"] = True
                        diagnostics[category]["provider_call_count"] = 1
                        try:
                            category_rows = await _coalesced_search(
                                provider,
                                profile=profile,
                                category=category,
                                queries=queries,
                                limit=call_limit,
                                adapter_version=str(
                                    company_context["provider_adapter_version"]
                                ),
                                company=pdl_company_identity,
                            )
                            if not category_rows and _searched_the_provider(
                                provider, queries
                            ):
                                # The provider answered; nobody matched. That is
                                # a result, and must never be reported as a
                                # rejected request. An empty *query set* is not
                                # an answer and must not land here.
                                no_match_categories.add(category)
                                metric(
                                    "people_no_match_total",
                                    provider=settings.people_primary_provider,
                                    category=category,
                                )
                        except ProviderUnavailable as exc:
                            failures.append(exc.reason)
                            _log_provider_failure(exc, run.id)
                        raw_count = int(
                            getattr(
                                provider,
                                "last_search_raw_count",
                                len(category_rows),
                            )
                        )
                        normalized_count = int(
                            getattr(
                                provider,
                                "last_search_normalized_count",
                                len(category_rows),
                            )
                        )
                        diagnostics[category][
                            "raw_search_result_count"
                        ] = raw_count
                        diagnostics[category][
                            "normalized_profile_count"
                        ] = normalized_count
                        searched += normalized_count
                        total_pdl_remaining -= raw_count
                    elif call_limit:
                        # A spent provider budget is its own operational state,
                        # not a provider failure, and it must never move the
                        # circuit.
                        failures.append("provider_budget_exceeded")
                        metric(
                            "people_budget_rejections_total",
                            provider=settings.people_primary_provider,
                            status="provider_budget",
                            category=category,
                        )
                        diagnostics[category]["rejection_reason_counts"][
                            "provider_budget_insufficient"
                        ] = 1
                for query in queries:
                    if category_pdl_search:
                        continue
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
                    diagnostics[category]["normalized_profile_count"] += len(rows)
                    diagnostics[category]["query_executed"] = True
                    diagnostics[category]["provider_call_count"] += 1
                categories[category] = deduplicate(category_rows)
                diagnostics[category]["unique_candidate_count"] = len(categories[category])
                duplicate_count = max(
                    0,
                    len(category_rows)
                    - diagnostics[category]["unique_candidate_count"],
                )
                if duplicate_count:
                    diagnostics[category]["rejection_reason_counts"]["duplicate_person"] = duplicate_count
                    diagnostics[category][
                        "deduplicated_into_another_identity"
                    ] += duplicate_count
                if not category_rows:
                    diagnostics[category]["rejection_reason_counts"]["no_search_results"] = 1
                metric(
                    "people_discovery_candidates_found",
                    len(categories[category]),
                    provider=settings.people_primary_provider,
                    category=category,
                    scoring_version=SCORING_VERSION,
                )
            pipeline_stage = "enrichment"
            if (
                not any(categories.values())
                and settings.people_pdl_fallback_enabled
                and settings.people_primary_provider != "pdl"
            ):
                fallback = PDLPeopleProvider()
                _configure_provider_usage(
                    fallback,
                    db=db,
                    user_id=user.id,
                    job_id=job_id,
                    discovery_run_id=run.id,
                    adapter_version="provider-neutral-v1",
                )
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
            assigned_by_category = _strongest_category_candidates(
                preliminary_by_category
            )
            for category in _PEOPLE_CATEGORIES:
                weaker_assignments = (
                    len(preliminary_by_category[category])
                    - len(assigned_by_category[category])
                )
                if weaker_assignments:
                    counts = diagnostics[category][
                        "rejection_reason_counts"
                    ]
                    counts["weaker_category_assignment"] = (
                        counts.get("weaker_category_assignment", 0)
                        + weaker_assignments
                    )
                    diagnostics[category][
                        "assigned_to_another_category"
                    ] += weaker_assignments
                    diagnostics[category][
                        "assigned_to_stronger_category"
                    ] += weaker_assignments
            complete_person_only = getattr(
                provider, "bulk_capability_state", "unknown"
            ) in {"temporarily_rejected", "account_not_supported"}
            pdl_direct_profiles = category_pdl_search
            enrich_targets = allocate_enrichment_targets(
                assigned_by_category,
                total=(
                    settings.people_apollo_complete_person_max_per_job
                    if complete_person_only
                    else (
                        settings.people_max_displayed_recruiters
                        + settings.people_max_displayed_managers
                        + settings.people_max_displayed_referrers
                        if pdl_direct_profiles
                        else settings.people_max_enrichments_per_job
                    )
                ),
                reservations=(
                    {
                        "likely_recruiter": (
                            settings.people_apollo_complete_person_max_recruiters
                        ),
                        "potential_hiring_manager": (
                            settings.people_apollo_complete_person_max_managers
                        ),
                        "potential_referrer": (
                            settings.people_apollo_complete_person_max_referrers
                        ),
                    }
                    if complete_person_only
                    else (
                        {
                            "likely_recruiter": (
                                settings.people_max_displayed_recruiters
                            ),
                            "potential_hiring_manager": (
                                settings.people_max_displayed_managers
                            ),
                            "potential_referrer": (
                                settings.people_max_displayed_referrers
                            ),
                        }
                        if pdl_direct_profiles
                        else {
                            "likely_recruiter": (
                                settings.people_recruiter_enrichment_reserve
                            ),
                            "potential_hiring_manager": (
                                settings.people_manager_enrichment_reserve
                            ),
                            "potential_referrer": (
                                settings.people_referrer_enrichment_reserve
                            ),
                        }
                    )
                ),
            )
            for _score, category, _person, _school, _employer in enrich_targets:
                diagnostics[category]["selected_for_enrichment"] += 1
            unique_enrichment_requests = list(
                dict.fromkeys(
                    item[2].provider_person_id for item in enrich_targets
                )
            )
            try:
                enriched = await provider.enrich_people(
                    [
                        PersonEnrichmentRequest(
                            provider_person_id=person.provider_person_id,
                            category=category,
                            rank_score=score,
                        )
                        for score, category, person, _school, _employer
                        in enrich_targets
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
            pipeline_stage = "employment_validation"
            metric(
                "people_enrichment_requests_total",
                len(unique_enrichment_requests),
                provider=settings.people_primary_provider,
            )
            enriched_by_id = {item.provider_person_id: item for item in enriched}
            rejection_reason_for = getattr(
                provider, "enrichment_rejection_reason", lambda _value: None
            )
            for _score, category, initial, _school, _employer in enrich_targets:
                key = (
                    "enrichment_matches"
                    if initial.provider_person_id in enriched_by_id
                    else "enrichment_misses"
                )
                diagnostics[category][key] += 1
                if key == "enrichment_misses":
                    counts = diagnostics[category]["rejection_reason_counts"]
                    reason = (
                        rejection_reason_for(initial.provider_person_id)
                        or "enrichment_record_not_found"
                    )
                    counts[reason] = counts.get(reason, 0) + 1
            for category in _PEOPLE_CATEGORIES:
                not_selected = max(
                    0,
                    len(assigned_by_category[category])
                    - diagnostics[category]["selected_for_enrichment"],
                )
                diagnostics[category]["candidates_rejected"] += not_selected
                if not_selected:
                    counts = diagnostics[category]["rejection_reason_counts"]
                    counts["enrichment_budget_exhausted"] = not_selected
            expires = _now() + timedelta(days=_people_result_ttl_days())
            caps = {
                "likely_recruiter": settings.people_max_displayed_recruiters,
                "potential_hiring_manager": settings.people_max_displayed_managers,
                "potential_referrer": settings.people_max_displayed_referrers,
            }
            for _, category, initial, school, employer in enrich_targets:
                if displayed[category] >= caps[category]:
                    diagnostics[category]["display_cap_excluded"] += 1
                    diagnostics[category]["candidates_rejected"] += 1
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
                secondary_verification_candidate = bool(
                    settings.people_employment_secondary_verification_enabled
                    and person.exact_company_match
                    and employment.identity_strong
                    and not verification_blockers
                    and (
                        employment.status
                        in {
                            "conflicting_current_employment",
                            "stale_or_uncertain",
                            "exact_company_current_but_unverified_freshness",
                        }
                        or settings.people_employment_comparison_mode
                    )
                )
                if secondary_verification_candidate:
                    diagnostics[category]["employment_unresolved_candidates"] += 1
                should_secondary_verify = bool(
                    secondary_verification_candidate
                    and verification_budget_reason is None
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
                employment_outcomes[employment.status] += 1
                if employment.status in {
                    "confirmed_exact_company_verified",
                    "exact_company_current_but_unverified_freshness",
                }:
                    diagnostics[category][
                        "exact_company_current_profiles"
                    ] += 1
                elif employment.status == "former_employee":
                    diagnostics[category]["former_employees"] += 1
                elif employment.status == "conflicting_current_employment":
                    diagnostics[category]["conflicting_employees"] += 1
                elif employment.status in {
                    "stale_or_uncertain",
                    "insufficient_evidence",
                }:
                    diagnostics[category][
                        "stale_or_insufficient_evidence"
                    ] += 1
                diagnostics[category].setdefault(
                    "employment_validation_outcomes", {}
                )
                diagnostics[category]["employment_validation_outcomes"][
                    employment.status
                ] = (
                    diagnostics[category][
                        "employment_validation_outcomes"
                    ].get(employment.status, 0)
                    + 1
                )
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
                if any(
                    reason in {
                        "weak_role_similarity",
                        "title_mismatch",
                        "below_relevance_threshold",
                    }
                    for reason in rejection_reasons
                ):
                    diagnostics[category]["below_title_relevance"] += 1
                if "below_confidence_threshold" in rejection_reasons:
                    diagnostics[category][
                        "below_confidence_threshold"
                    ] += 1
                if (
                    rejection_reasons == ["weak_company_confidence"]
                    and category != "potential_referrer"
                    and score >= threshold + 15
                ):
                    rejection_reasons = []
                if rejection_reasons:
                    pipeline_stage = "recommendation_persistence"
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
                            job_id=job_id,
                            person_id=canonical.id,
                            candidate_category=category,
                            category_score=score,
                            data_confidence=data_confidence,
                            current_employment_confidence=employment.confidence,
                            employment_validation_status=employment.status,
                            employment_validation_version=(
                                EMPLOYMENT_VALIDATION_VERSION
                            ),
                            employment_validation_checked_at=_now(),
                            recommendation_reasons=[],
                            recommendation_limitations=rejection_reasons,
                            scoring_version=SCORING_VERSION,
                            expires_at=expires,
                        )
                        db.add(candidate)
                        db.flush()
                    else:
                        candidate.category_score = score
                        candidate.data_confidence = data_confidence
                        candidate.current_employment_confidence = (
                            employment.confidence
                        )
                        candidate.employment_validation_status = (
                            employment.status
                        )
                        candidate.employment_validation_version = (
                            EMPLOYMENT_VALIDATION_VERSION
                        )
                        candidate.employment_validation_checked_at = _now()
                        candidate.recommendation_reasons = []
                        candidate.recommendation_limitations = (
                            rejection_reasons
                        )
                        candidate.scoring_version = SCORING_VERSION
                        candidate.expires_at = expires
                    suppressed = _ensure_recommendation(
                        db,
                        user.id,
                        candidate,
                        school,
                        employer,
                    )
                    suppressed.suppressed_at = _now()
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
                pipeline_stage = "recommendation_persistence"
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
                recommendation = _ensure_recommendation(
                    db,
                    user.id,
                    candidate,
                    school,
                    employer,
                )
                recommendation.suppressed_at = None
                canonical.employment_revalidation_required = False
                canonical.employment_conflict_detected_at = None
                displayed[category] += 1
                diagnostics[category]["accepted"] += 1
                diagnostics[category]["final_displayed_count"] += 1
                metric(
                    "people_discovery_candidates_displayed",
                    provider=settings.people_primary_provider,
                    category=category,
                    scoring_version=SCORING_VERSION,
                )
                pipeline_stage = "employment_validation"
            usage = await provider.get_usage()
            run = db.get(PeopleDiscoveryRun, run.id)
            any_displayed = any(displayed.values())
            # A category the provider answered with zero people is a result, not
            # a failure. Only real failures can downgrade the run.
            run.status = "partial" if failures and any_displayed else "complete"
            if any_displayed and no_match_categories:
                # Some categories matched and some were answered with nobody:
                # the honest answer is partial coverage, not a provider problem.
                run.status = "partial"
            if failures and not any_displayed:
                # The failure a human can act on wins, and its typed code — not
                # the fact that *something* failed — decides the status. An
                # unresolved domain or an exhausted user budget must never be
                # reported as a provider outage.
                dominant = _dominant_failure(failures) or "provider_unavailable"
                dominant_code = code_for_reason(dominant)
                run.status = _STATUS_FOR_CODE.get(
                    dominant_code, "provider_unavailable"
                )
                run.failure_code = dominant[:60]
                run.safe_failure_message = _safe_provider_message(dominant)
                if _refundable_failure(dominant_code, provider):
                    # Nothing useful was bought with the user's unit: either
                    # JobPilot's own data was insufficient, or the provider was
                    # never meaningfully reached. Give it back.
                    reservation.refund(db, reason=str(dominant_code))
            elif not any_displayed and no_match_categories:
                # Every category the provider answered came back empty. The run
                # completed successfully; there is simply nobody to show.
                run.status = "complete"
                run.failure_code = None
                run.safe_failure_message = None
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
            completed_at = _now()
            provider_error_context = (
                _provider_error_context(
                    run.failure_code or "provider_unavailable",
                    now=completed_at,
                )
                if run.status in PROVIDER_ERROR_STATUSES
                else {}
            )
            durable_usage = _durable_usage_summary(db, run.id)
            run.company_context = {
                **company_context,
                "provider_request_count": usage.requests,
                "durable_provider_usage": durable_usage,
                "provider_bulk_capability_state": getattr(
                    provider, "bulk_capability_state", "unknown"
                ),
                "pipeline_outcomes": {
                    "search": (
                        "partial_failure" if failures else "completed"
                    ),
                    "enrichment": "completed",
                    **_provider_pipeline_outcomes(
                        provider, durable_usage
                    ),
                    "employment_validation": dict(
                        sorted(employment_outcomes.items())
                    ),
                    "persistence": "completed",
                },
                "provider_enrichment_safe_metrics": getattr(
                    provider, "enrichment_safe_metrics", {}
                ),
                "provider_search_identifier_safe_metrics": getattr(
                    provider, "search_identifier_safe_metrics", {}
                ),
                **provider_error_context,
            }
            run.category_diagnostics = diagnostics
            run.completed_at = completed_at
            pipeline_stage = "recommendation_commit"
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
            _log_search_orchestration(
                run=run,
                job_id=job_id,
                user_id=user.id,
                profile=profile,
                strategy=strategy,
                failures=failures,
                displayed=sum(displayed.values()),
                started=started,
                cache="miss",
            )
        except Exception as exc:
            db.rollback()
            persistence_failure = (
                isinstance(exc, ProviderUsagePersistenceError)
                or (
                    isinstance(exc, SQLAlchemyError)
                    and pipeline_stage
                    in {
                        "recommendation_persistence",
                        "recommendation_commit",
                    }
                )
            )
            failed_run = db.get(PeopleDiscoveryRun, run.id)
            if failed_run:
                completed_at = _now()
                durable_usage = _durable_usage_summary(db, run.id)
                failure_code = (
                    "recommendation_commit_failed"
                    if persistence_failure
                    else "discovery_failed"
                )
                failed_run.status = (
                    "persistence_error"
                    if persistence_failure
                    else "provider_unavailable"
                )
                failed_run.failure_code = failure_code
                failed_run.safe_failure_message = (
                    "JobPilot found potential contacts but could not save "
                    "the results. No additional search will run unless you retry."
                    if persistence_failure
                    else "People discovery is temporarily unavailable."
                )
                failed_run.company_context = {
                    **(failed_run.company_context or {}),
                    "durable_provider_usage": durable_usage,
                    "provider_bulk_capability_state": getattr(
                        provider, "bulk_capability_state", "unknown"
                    ),
                    "pipeline_outcomes": {
                        "search": (
                            "completed"
                            if pipeline_stage != "search"
                            else "failed"
                        ),
                        "enrichment": (
                            "completed"
                            if pipeline_stage
                            in {
                                "employment_validation",
                                "recommendation_persistence",
                                "recommendation_commit",
                            }
                            else "failed"
                        ),
                        **_provider_pipeline_outcomes(
                            provider, durable_usage
                        ),
                        "employment_validation": dict(
                            sorted(employment_outcomes.items())
                        ),
                        "persistence": (
                            "failed"
                            if persistence_failure
                            else "not_completed"
                        ),
                    },
                    **_provider_error_context(
                        failure_code,
                        now=completed_at,
                    ),
                }
                failed_run.category_diagnostics = diagnostics
                failed_run.records_searched = searched
                failed_run.records_enriched = len(enriched)
                failed_run.completed_at = completed_at
                if not _provider_work_started(provider):
                    # An internal failure before any provider call is not a
                    # search the user should pay for.
                    reservation.refund(db, reason="internal_error_before_provider_work")
                db.commit()
            metric(
                "people_discovery_provider_errors_total",
                provider=settings.people_primary_provider,
                status=(
                    "recommendation_commit_failed"
                    if persistence_failure
                    else "discovery_failed"
                ),
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
    # Stale-while-error: when the provider is unavailable, previously verified
    # results are far more useful than an error page. They are only served
    # inside an explicitly configured window, and the response says so.
    circuit_snapshot = people_circuit_snapshot()
    serve_stale = bool(circuit_snapshot.open_kinds)
    stale_cutoff = now - timedelta(days=max(0, settings.people_stale_result_window_days))
    served_stale = False
    for recommendation, candidate, person in rows:
        expires = candidate.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        stale = stale or expires <= now
        expires_at = expires if expires_at is None else min(expires_at, expires)
        if expires <= now:
            if not (serve_stale and expires > stale_cutoff):
                continue
            served_stale = True
            metric(
                "people_cache_stale_served_total",
                provider=settings.people_primary_provider,
                circuit=circuit_snapshot.as_label(),
            )
        email_lookup_allowed = (
            candidate.employment_validation_status
            in {
                "confirmed_exact_company_verified",
                "exact_company_current_but_unverified_freshness",
            }
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
                "full_name": _display_name(person.canonical_full_name),
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
                "employment_warning": (
                    "Currently listed at the hiring company. Current employment has not been independently verified."
                    if candidate.employment_validation_status
                    == "exact_company_current_but_unverified_freshness"
                    else None
                ),
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
    if latest_current_run is not None and not run_is_compatible(latest_current_run):
        # A run recorded without the current contract is legacy even when its
        # fingerprint still matches: rows written before versioning existed
        # carry statuses that were interpreted differently. Treating it as
        # absent routes the job to the "refresh to check again" state.
        latest_current_run = None
    latest_any_run = _latest_run(db, job_id=job_id, user_id=user.id)
    latest_run = latest_current_run or latest_any_run
    has_results = any(categories.values())
    stale_version = latest_any_run is not None and latest_current_run is None
    stale_strategy_without_results = stale_version and not has_results
    response_status = "complete" if has_results else "not_started"
    if (
        has_results
        and latest_current_run is not None
        and latest_current_run.status == "partial"
    ):
        # People were found, but at least one category was answered with
        # nobody. Saying "complete" would overstate the coverage.
        response_status = "partial"
    warnings: list[str] = []
    if stale_strategy_without_results:
        response_status = "stale"
        warnings.append(
            "Contact discovery has been upgraded. Refresh to check again."
        )
        if latest_any_run is not None and not run_is_compatible(latest_any_run):
            metric(
                "people_legacy_cache_invalidations_total",
                provider=latest_any_run.provider or "unknown",
                status=latest_any_run.status,
            )
    elif not has_results and latest_run and latest_run.status in {"running"}:
        response_status = "in_progress"
    elif (
        not has_results
        and latest_run
        and latest_run.status == "persistence_error"
    ):
        response_status = "persistence_error"
        warnings.append(
            latest_run.safe_failure_message
            or (
                "JobPilot found potential contacts but could not save the "
                "results. No additional search will run unless you retry."
            )
        )
    elif (
        not has_results
        and latest_run
        and latest_run.status in PROVIDER_ERROR_STATUSES
    ):
        # Each failure keeps its own status so the UI can explain the real
        # cause instead of the old catch-all "temporarily paused" line.
        response_status = latest_run.status
        warnings.append(
            latest_run.safe_failure_message
            or _safe_provider_message(latest_run.failure_code or "")
        )
    elif not has_results and latest_run and latest_run.status == "partial":
        response_status = "partial"
        warnings.append("Some professional data sources were unavailable; showing reliable partial results.")
    elif latest_run and not has_results:
        response_status = "no_reliable_matches"
    if stale and not has_results:
        response_status = "stale"
        warnings.append("Previous results are stale. Refresh is available.")
    availability_reason = (
        latest_run.failure_code
        # A legacy run's failure_code was written under provider semantics that
        # no longer hold, so it must not be surfaced as the current reason.
        # Doing so is what left one Toshiba job permanently reporting
        # "the provider request was invalid" while an identical job did not.
        if latest_run
        and run_is_compatible(latest_run)
        and (
            response_status in PROVIDER_ERROR_STATUSES
            or (
                response_status == "stale"
                and latest_run.status
                in PROVIDER_ERROR_STATUSES
            )
        )
        else "available"
    )
    retry_eligible = False
    retry_after_seconds: int | None = None
    retry_eligible_at: datetime | None = None
    if (
        stale_version
        and latest_run
        and latest_run.status == "provider_unavailable"
        and availability_reason == "provider_schema_error"
    ):
        # A provider-schema failure becomes retryable only when its adapter
        # fingerprint is obsolete. The POST remains an explicit user action.
        retry_eligible = True
    elif latest_run and latest_run.status in PROVIDER_ERROR_STATUSES:
        (
            retry_eligible,
            retry_after_seconds,
            retry_eligible_at,
        ) = _provider_error_retry_state(latest_run)
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
    if has_results:
        metric(
            "people_results_total",
            sum(len(items) for items in categories.values()),
            provider=settings.people_primary_provider,
            status="stale" if served_stale else "fresh",
        )
    return {
        "status": response_status,
        "availability_reason": availability_reason,
        "retry_eligible": retry_eligible,
        "retry_after_seconds": retry_after_seconds,
        "retry_eligible_at": retry_eligible_at,
        # Tells the client these results predate the provider outage, so it can
        # label them as cached instead of presenting them as a fresh search.
        "result_freshness": (
            "stale" if served_stale else "fresh" if has_results else "none"
        ),
        "provider_circuit": circuit_snapshot.as_label(),
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
            "refresh_eligible": response_status == "stale"
            or (
                response_status
                in PROVIDER_ERROR_STATUSES
                and retry_eligible
            ),
            "exact_company_search_completed": exact_no_match is not None
            or bool(has_results and latest_current_run),
            "related_company_search_attempted": related_company_search_attempted,
            "broaden_eligible": broaden_eligible,
            "broaden_attempted": broaden_attempted,
        },
        "warnings": warnings,
        # The user's remaining allowance, counted in deliberate actions. Reading
        # this payload never consumes any of it.
        "quota": quota_snapshot(db, user).as_payload(),
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
        recommendation.suppressed_at is not None
        or
        candidate.employment_validation_version != EMPLOYMENT_VALIDATION_VERSION
        or candidate.employment_validation_status
        not in {
            "confirmed_exact_company_verified",
            "exact_company_current_but_unverified_freshness",
        }
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
    started = time.monotonic()
    recommendation, candidate, person = owned_recommendation(db, user, job_id, recommendation_id)
    metric(
        "people_outreach_draft_requests_total",
        channel=request.message_type,
        category=candidate.candidate_category,
    )
    # The gate must match what the UI is allowed to *show*. It previously
    # accepted only ``confirmed_exact_company_verified`` while the card and the
    # work-email action both accept the full displayable set, so every person
    # discovered through PDL — which never carries an independent freshness
    # verification — rendered a draft button that always answered 409.
    if (
        recommendation.suppressed_at is not None
        or candidate.employment_validation_version
        != EMPLOYMENT_VALIDATION_VERSION
        or candidate.employment_validation_status
        not in DISPLAYABLE_EMPLOYMENT_STATUSES
        or person.employment_revalidation_required
    ):
        metric(
            "people_outreach_draft_failures_total",
            channel=request.message_type,
            stage="employment_validation",
            error_code="PEOPLE_EMPLOYMENT_REVALIDATION_REQUIRED",
        )
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
    # Channel handoff evidence. The client opens LinkedIn or an email composer
    # only from these values — it never derives a profile URL from a name or a
    # company domain, because a guessed URL points at a real stranger.
    linkedin_url = safe_profile_url(person.linkedin_url)
    verified_email = (
        decrypt_email(person.professional_email_ciphertext)
        if person.email_verification_status == "verified"
        else None
    )
    generation_path = "deterministic_template"
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
        # Every draft is built from verified fields by a deterministic template,
        # so there is no model call to fail and nothing to fall back from.
        "generation_path": generation_path,
        "template_version": OUTREACH_TEMPLATE_VERSION,
        "recipient_name": _display_name(person.canonical_full_name),
        "recipient_category": candidate.candidate_category,
        "linkedin_url": linkedin_url,
        "linkedin_available": linkedin_url is not None,
        "professional_email": verified_email,
        "email_available": verified_email is not None,
    }
    metric(
        "people_outreach_draft_success_total",
        channel=request.message_type,
        generation_path=generation_path,
        category=candidate.candidate_category,
    )
    logger.info(
        "people_outreach_draft job_id=%s recommendation_id=%s user_ref=%s "
        "channel=%s category=%s generation_path=%s template=%s cache=miss "
        "grounding=passed linkedin_available=%s email_available=%s "
        "character_count=%s duration_ms=%.1f",
        job_id,
        recommendation.id,
        _safe_user_reference(user.id),
        request.message_type,
        candidate.candidate_category,
        generation_path,
        OUTREACH_TEMPLATE_VERSION,
        linkedin_url is not None,
        verified_email is not None,
        len(body),
        (time.monotonic() - started) * 1000,
    )
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
