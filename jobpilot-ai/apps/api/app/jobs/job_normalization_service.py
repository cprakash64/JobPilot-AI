"""Validate, enrich, and deduplicate normalized jobs from multiple sources.

Responsibilities:
- Reject jobs missing title / company / application URL (safety rule).
- Reject jobs that signal they are closed.
- Enrich each job with parsed skills/responsibilities/etc. when the source did
  not already provide them (never overwriting real source data with guesses).
- Deduplicate within and across sources.
- Provide UTC-based freshness filtering keyed on posted_at.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.job_sources.base import NormalizedJob, normalize_title
from app.jobs.job_description_parser import parse_job_description

CLOSED_MARKERS = ["this position is closed", "no longer accepting", "position filled", "job closed"]

# Hosts that indicate a fake/placeholder/demo posting. Any job whose apply or job
# URL points here is rejected so demo data can never reach the user.
PLACEHOLDER_HOSTS = (
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "127.0.0.1",
    "test.com",
    "demo.com",
    "placeholder",
    "yourcompany.com",
)
DEMO_COMPANIES = {"democo", "demo company", "acme demo"}


def is_placeholder_url(url: str | None) -> bool:
    low = (url or "").lower()
    return any(host in low for host in PLACEHOLDER_HOSTS)


def validate_job(job: NormalizedJob) -> bool:
    """A job must have a title, a company, and a real (non-placeholder) apply URL."""
    if not (job.title or "").strip() or not (job.company or "").strip():
        return False
    if (job.company or "").strip().lower() in DEMO_COMPANIES:
        return False
    if (job.source or "").strip().lower() == "demo":
        return False
    url = (job.application_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    if is_placeholder_url(url) or is_placeholder_url(job.source_url):
        return False
    if _looks_closed(job):
        return False
    return True


def _looks_closed(job: NormalizedJob) -> bool:
    haystack = f"{job.title} {job.description_clean}".lower()
    return any(marker in haystack for marker in CLOSED_MARKERS)


def enrich_job(job: NormalizedJob) -> NormalizedJob:
    """Fill in structured fields from the description without inventing data."""
    parsed = parse_job_description(job.description_clean or job.description_raw, job.title, job.location)
    if not job.required_skills:
        job.required_skills = parsed.required_skills
    if not job.preferred_skills:
        job.preferred_skills = parsed.preferred_skills
    if not job.responsibilities:
        job.responsibilities = parsed.responsibilities
    if job.years_experience_min is None:
        job.years_experience_min = parsed.years_experience_min
    if not job.degree_requirement:
        job.degree_requirement = parsed.degree_requirement
    if not job.seniority_level:
        job.seniority_level = parsed.seniority
    if not job.remote_type and parsed.workplace_type != "unknown":
        job.remote_type = parsed.workplace_type
    if not job.work_authorization_notes:
        job.work_authorization_notes = parsed.work_authorization_notes
    if job.salary_min is None:
        job.salary_min = parsed.salary_min
        job.salary_max = parsed.salary_max
        job.salary_currency = parsed.salary_currency
    job.parse_confidence = parsed.confidence
    return job


def normalize_jobs(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Validate, enrich, and deduplicate a batch of jobs."""
    valid = [enrich_job(job) for job in jobs if validate_job(job)]
    return deduplicate(valid)


def deduplicate(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Drop duplicates.

    Two keys are used:
    - (source, external_id): the same posting from the same source.
    - (company, normalized_title, location): the same role surfaced by more than
      one source. The first occurrence (usually the earliest source queried) wins.
    """
    seen_source: set[tuple[str, str]] = set()
    seen_logical: set[tuple[str, str, str]] = set()
    result: list[NormalizedJob] = []
    for job in jobs:
        source_key = (job.source or "", job.external_id or "")
        logical_key = (
            (job.company or "").strip().lower(),
            normalize_title(job.title),
            (job.location or "").strip().lower(),
        )
        if source_key in seen_source or logical_key in seen_logical:
            continue
        seen_source.add(source_key)
        seen_logical.add(logical_key)
        result.append(job)
    return result


def is_fresh(posted_at: datetime | None, days: int = 7, *, include_unknown: bool = False) -> bool:
    """True if posted within `days` (UTC). Unknown dates only pass when opted in.

    We never fabricate a posted date: an unknown date is excluded from the
    "last N days" list unless the caller explicitly includes unknown-date jobs.
    """
    if posted_at is None:
        return include_unknown
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    return posted_at >= datetime.now(UTC) - timedelta(days=days)
