"""Discovery orchestration: profile -> criteria -> fetch -> normalize -> match.

`discover_jobs` reads the logged-in user's profile, builds search criteria,
fetches from allowed public sources (concurrently, with per-source error
isolation so one bad source never fails the whole run), normalizes and
deduplicates, persists fresh jobs, and scores them against the profile.

Sources can be injected for testing; in production they come from the curated
`source_registry`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs.company_logo_service import get_or_create_company_branding
from app.jobs.job_normalization_service import is_fresh, normalize_jobs
from app.jobs.job_search_criteria_service import SearchCriteria, build_search_criteria
from app.jobs.scoring_service import (
    build_profile_view,
    compute_job_content_hash,
    job_view as _job_view,
    score_jobs_for_user,
)
from app.jobs.source_packs import packs_for_profile, tags_for_packs
from app.jobs.source_registry import build_adapters, is_configured
from app.models.entities import (
    Experience,
    JobPosting,
    JobSource,
    UserProfile,
)

# Re-exported for callers that import these from the ingestion service (kept for
# backward compatibility with routes/tests that used the pre-refactor location).
__all__ = [
    "DiscoveryResult",
    "discover_jobs",
    "rematch_user",
    "build_profile_view",
    "_job_view",
]


@dataclass
class DiscoveryResult:
    criteria: SearchCriteria
    fetched: int = 0
    fresh: int = 0
    persisted: int = 0
    matched: int = 0
    source_warnings: list[str] = field(default_factory=list)
    used_ai: bool = False
    sources_searched: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    packs: list[str] = field(default_factory=list)
    scoring_tasks_queued: int = 0


# Simple in-process cache of a source's fetched jobs, to avoid hammering ATS
# endpoints across back-to-back discoveries. Keyed by (provider, slug).
_SOURCE_CACHE: dict[tuple[str, str], tuple[datetime, list[NormalizedJob]]] = {}


def _cache_get(
    adapter: JobSourceAdapter, *, allow_expired: bool = False
) -> list[NormalizedJob] | None:
    ttl = settings.job_discovery_cache_ttl_minutes
    if ttl <= 0:
        return None
    entry = _SOURCE_CACHE.get((adapter.source_type, adapter.company_slug))
    if entry is None:
        return None
    stamp, jobs = entry
    if not allow_expired and datetime.now(UTC) - stamp > timedelta(minutes=ttl):
        return None
    return jobs


def _cache_put(adapter: JobSourceAdapter, jobs: list[NormalizedJob]) -> None:
    if settings.job_discovery_cache_ttl_minutes > 0:
        _SOURCE_CACHE[(adapter.source_type, adapter.company_slug)] = (datetime.now(UTC), jobs)


def clear_source_cache() -> None:
    """Clear the in-process source-result cache (used by tests and manual refresh)."""
    _SOURCE_CACHE.clear()


async def discover_jobs(
    db: Session,
    user_id: int,
    *,
    days: int = 7,
    include_unknown: bool = False,
    sources: list[JobSourceAdapter] | None = None,
) -> DiscoveryResult:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    experiences = list(db.scalars(select(Experience).where(Experience.user_id == user_id)).all())
    criteria = build_search_criteria(profile, experiences)

    packs: list[str] = []
    if sources is None:
        # Pick source packs from the user's profile, then build adapters limited
        # to the packs' tags (falls back to the whole catalog when no packs match).
        packs = _resolve_packs(profile)
        tags = tags_for_packs(packs) if packs else None
        adapters = build_adapters(settings.job_discovery_max_companies, tags=tags)
        if not adapters:
            adapters = build_adapters(settings.job_discovery_max_companies)
        if not is_configured():
            result = DiscoveryResult(criteria=criteria)
            result.source_warnings.append(
                "No job sources are configured. Add real public ATS boards in "
                "app/jobs/sources_config.json or the JOB_SOURCE_COMPANIES env var."
            )
            result.matched = rematch_user(db, user_id, days=days, include_unknown=include_unknown)
            return result
    else:
        adapters = sources

    fetched_jobs, warnings, stats = await _fetch_all(adapters, days)

    result = DiscoveryResult(
        criteria=criteria,
        fetched=len(fetched_jobs),
        source_warnings=warnings,
        packs=packs,
        **stats,
    )

    normalized = normalize_jobs(fetched_jobs)
    fresh_jobs = [job for job in normalized if is_fresh(job.posted_at, days, include_unknown=include_unknown)]
    result.fresh = len(fresh_jobs)

    changed_job_ids: list[int] = []
    for job in fresh_jobs:
        outcome = _persist_job(db, job)
        if outcome is None:
            continue
        record, is_new, content_changed = outcome
        result.persisted += 1
        if is_new or content_changed:
            changed_job_ids.append(record.id)
    # Commit BEFORE any scoring so we never score an uncommitted job, and a
    # scoring failure can never roll back a successfully ingested job.
    db.commit()

    # On-demand fallback: score synchronously for the user who triggered the run
    # so they see immediate results without a manual "Refresh matches".
    result.matched = rematch_user(db, user_id, days=days, include_unknown=include_unknown)

    # Automatic background scoring for every other active user, limited to the
    # jobs that were newly inserted or materially changed in this run.
    if changed_job_ids:
        result.scoring_tasks_queued = enqueue_scoring_for_jobs(
            db, changed_job_ids, exclude_user_ids=[user_id]
        )
    return result


def enqueue_scoring_for_jobs(
    db: Session, job_ids: list[int], *, exclude_user_ids: list[int] | None = None
) -> int:
    """Hand newly changed jobs to the background worker for scoring across all
    active users. Falls back to inline scoring **on the provided session** if the
    broker is unavailable, so scores are never silently dropped. Returns the
    number of jobs handed off (queued or scored inline)."""
    if not job_ids:
        return 0
    exclude = exclude_user_ids or []
    if _try_enqueue(job_ids, exclude):
        return len(job_ids)

    # Broker unavailable → degrade to synchronous inline scoring using the caller's
    # session so the work still happens (and, in tests, hits the same database).
    logging.getLogger("jobpilot.scoring").warning(
        "Background scoring broker unavailable; scoring %d job(s) inline.", len(job_ids)
    )
    from app.jobs.scoring_service import active_user_ids, score_users_for_job

    targets = [uid for uid in active_user_ids(db) if uid not in exclude]
    for job_id in job_ids:
        score_users_for_job(db, job_id, targets, commit=False)
    db.commit()
    return len(job_ids)


def _try_enqueue(job_ids: list[int], exclude: list[int]) -> bool:
    """Best-effort, fast-failing publish to Celery. Returns False (rather than
    blocking on broker retries) when the broker cannot be reached quickly."""
    if not broker_reachable():
        return False
    try:
        from app.workers.tasks import score_jobs_for_users_task

        score_jobs_for_users_task.apply_async(args=[job_ids, exclude], retry=False)
        return True
    except Exception as exc:  # noqa: BLE001 - broker down / not configured
        logging.getLogger("jobpilot.scoring").info(
            "Could not enqueue scoring task (%s).", type(exc).__name__
        )
        return False


# A single cheap TCP probe tells us whether the Celery broker is up. Celery's own
# publish path can take seconds to fail on a down broker (bounded retries × socket
# timeouts), which would tax every request that enqueues work; this probe fails in
# milliseconds so we can fall back to inline scoring immediately. Cached briefly to
# avoid a probe on every single enqueue.
_BROKER_PROBE: dict[str, tuple[float, bool]] = {}


def broker_reachable(*, cache_seconds: float = 5.0) -> bool:
    import socket
    import time
    from urllib.parse import urlparse

    now = time.monotonic()
    cached = _BROKER_PROBE.get("v")
    if cached is not None and now - cached[0] < cache_seconds:
        return cached[1]

    parsed = urlparse(settings.redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    ok = False
    try:
        with socket.create_connection((host, port), timeout=0.25):
            ok = True
    except OSError:
        ok = False
    _BROKER_PROBE["v"] = (now, ok)
    return ok


def _resolve_packs(profile: UserProfile | None) -> list[str]:
    if settings.job_discovery_source_packs:
        return list(settings.job_discovery_source_packs)
    if profile is None:
        return []
    return packs_for_profile(profile.target_roles or [], profile.target_levels or [])


async def _fetch_all(
    adapters: list[JobSourceAdapter], days: int
) -> tuple[list[NormalizedJob], list[str], dict[str, object]]:
    if not adapters:
        return [], [], {"sources_searched": 0, "sources_succeeded": 0, "sources_failed": 0, "by_provider": {}}
    timeout = settings.job_discovery_timeout_seconds
    semaphore = asyncio.Semaphore(max(1, settings.job_discovery_concurrency))

    async def run(adapter: JobSourceAdapter) -> tuple[list[NormalizedJob], str | None]:
        cached = _cache_get(adapter)
        if cached is not None:
            return cached, None
        label = f"{adapter.source_type}:{adapter.company_name}"
        try:
            async with semaphore:
                jobs = await asyncio.wait_for(adapter.fetch_recent_jobs(days), timeout=timeout)
            for job in jobs:
                if not job.source:
                    job.source = adapter.source_type
            _cache_put(adapter, jobs)
            return jobs, None
        except Exception as exc:  # noqa: BLE001 - isolate per-source failures
            # A transient timeout must not erase jobs from a source that worked
            # on the preceding refresh. Return the last successful in-process
            # snapshot and let the scheduled 24-hour ingestion retry it later.
            if isinstance(exc, TimeoutError | asyncio.TimeoutError):
                stale = _cache_get(adapter, allow_expired=True)
                if stale is not None:
                    return stale, None
            return [], f"Could not fetch from {label}: {type(exc).__name__}"

    results = await asyncio.gather(*[run(adapter) for adapter in adapters])
    jobs: list[NormalizedJob] = []
    warnings: list[str] = []
    succeeded = 0
    by_provider: dict[str, int] = {}
    for adapter, (source_jobs, warning) in zip(adapters, results, strict=True):
        if warning:
            warnings.append(warning)
        else:
            succeeded += 1
            by_provider[adapter.source_type] = by_provider.get(adapter.source_type, 0) + len(source_jobs)
        jobs.extend(source_jobs)
    stats = {
        "sources_searched": len(adapters),
        "sources_succeeded": succeeded,
        "sources_failed": len(adapters) - succeeded,
        "by_provider": by_provider,
    }
    return jobs, warnings, stats


def _persist_job(db: Session, job: NormalizedJob) -> tuple[JobPosting, bool, bool] | None:
    """Insert or update a posting. Returns ``(record, is_new, content_changed)``
    so the caller can enqueue scoring only for jobs whose score-relevant content
    actually changed (avoids an uncontrolled rescore on irrelevant churn)."""
    source = _upsert_source(db, job)
    existing = db.scalar(
        select(JobPosting).where(
            (JobPosting.source_id == source.id) & (JobPosting.external_id == job.external_id)
        )
    )
    # Cross-source dedupe against already-persisted postings.
    if existing is None:
        existing = db.scalar(
            select(JobPosting).where(JobPosting.hash_for_deduplication == job.dedupe_hash)
        )
    # Resolve (or reuse) this employer's branding ONCE, persisted separately
    # from job rows — every posting from the same company reuses the same
    # CompanyBranding row instead of re-resolving per job (see Part 8).
    branding = get_or_create_company_branding(
        db,
        job.company or "",
        catalog_domain=job.company_domain or None,
        catalog_logo_url=job.company_logo_url or None,
        application_url=job.application_url or None,
        source_type=job.source or None,
    )
    values = {
        "source_id": source.id,
        "external_id": job.external_id,
        "title": job.title,
        "company": job.company,
        "company_domain": branding.domain,
        "company_logo_url": branding.logo_url,
        "location": job.location,
        "remote_type": job.workplace_type,
        "employment_type": job.employment_type,
        "seniority_level": job.seniority_level,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "currency": job.salary_currency,
        "posted_at": _as_utc(job.posted_at),
        "application_url": job.application_url,
        "source_url": job.source_url,
        "description_raw": job.description_raw,
        "description_clean": job.description_clean,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "responsibilities": job.responsibilities,
        "years_experience_min": job.years_experience_min,
        "degree_requirement": job.degree_requirement,
        "work_authorization_notes": job.work_authorization_notes,
        "parse_confidence": job.parse_confidence,
        "raw_json": job.raw or {},
        "hash_for_deduplication": job.dedupe_hash,
    }
    now = datetime.now(UTC)
    if existing is None:
        record = JobPosting(
            discovered_at=now, last_seen_at=now, is_active=True, **values
        )
        db.add(record)
        db.flush()
        return record, True, True
    # Re-seen on the official source: refresh presence + reactivate if it had
    # previously expired, and detect whether score-relevant content changed.
    before_hash = compute_job_content_hash(existing)
    for key, value in values.items():
        setattr(existing, key, value)
    existing.last_seen_at = now
    existing.is_active = True
    db.flush()
    content_changed = compute_job_content_hash(existing) != before_hash
    return existing, False, content_changed


def _upsert_source(db: Session, job: NormalizedJob) -> JobSource:
    name = job.company
    source = db.scalar(select(JobSource).where(JobSource.name == name))
    if source is None:
        terms_notes = (
            "Public SimplifyJobs GitHub listing with attribution and a direct employer application URL."
            if (job.source or "").lower() == "simplifyjobs"
            else "Public ATS endpoint; no restricted portal scraping."
        )
        source = JobSource(
            name=name,
            type=job.source or "ats",
            base_url=job.source_url,
            enabled=True,
            supports_api=True,
            terms_notes=terms_notes,
        )
        db.add(source)
        db.flush()
    return source


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def rematch_user(db: Session, user_id: int, *, days: int = 7, include_unknown: bool = False) -> int:
    """Re-score every fresh, active job for one user against their current
    profile. This is the on-demand path (discovery + "Refresh matches"); it
    forces a rescore so the user always sees fresh numbers. Returns the number of
    match rows that ended up scored."""
    stats = score_jobs_for_user(
        db, user_id, days=days, include_unknown=include_unknown, force=True
    )
    return stats.scored + stats.profile_incomplete


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
