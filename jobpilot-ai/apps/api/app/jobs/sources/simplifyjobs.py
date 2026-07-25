"""SimplifyJobs public listings and verified-company metadata adapter.

SimplifyJobs and Pitt CSC maintain machine-readable new-grad and internship
lists in public GitHub repositories. The records include their discovery date
and, importantly for JobPilot, a direct employer/ATS application URL. We read
the published JSON and the public, server-rendered Simplify company profile.
The profile's verified official website is used for branding; a logo URL is
never guessed from a Simplify UUID.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs.company_logo_service import clean_company_domain, normalize_company_key
from app.jobs.sources._http import get_json, get_text

_ALLOWED_REPOSITORIES = {
    "New-Grad-Positions": "entry",
    "Summer2026-Internships": "internship",
}
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_PROFILE_CONCURRENCY = 8


def normalize_simplify_company_url(value: object) -> str:
    """Return a canonical public Simplify company-profile URL or ``""``."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "simplify.jobs"
        or not path.startswith("/c/")
        or len(path.split("/")) not in {3, 4}
    ):
        return ""
    return f"https://simplify.jobs{path}"


def parse_verified_company_domain(
    html: str, *, expected_company_name: str
) -> str:
    """Extract the official domain from a matching, verified Simplify profile.

    The third-party logo itself is intentionally ignored. It can be stale or
    misassigned; the verified official website is the identity anchor.
    """
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return ""
    try:
        payload = json.loads(match.group(1))
        company = payload["props"]["pageProps"]["company"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(company, dict) or company.get("verified") is not True:
        return ""
    if normalize_company_key(str(company.get("name") or "")) != normalize_company_key(
        expected_company_name
    ):
        return ""
    return clean_company_domain(str(company.get("url") or ""))


async def _verified_domain_for_company(company_url: object, company_name: str) -> str:
    profile_url = normalize_simplify_company_url(company_url)
    if not profile_url:
        return ""
    try:
        html = await get_text(profile_url, max_bytes=1024 * 1024)
    except Exception:  # noqa: BLE001 - profile enrichment must not fail discovery
        return ""
    return parse_verified_company_domain(html, expected_company_name=company_name)


def fetch_verified_company_domain(company_url: object, company_name: str) -> str:
    """Synchronous profile enrichment for the offline logo backfill command."""
    profile_url = normalize_simplify_company_url(company_url)
    if not profile_url:
        return ""
    try:
        with httpx.Client(
            timeout=settings.job_discovery_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = client.get(profile_url)
            response.raise_for_status()
            content = response.content
    except (httpx.HTTPError, ValueError):
        return ""
    if len(content) > 1024 * 1024:
        return ""
    html = content.decode(response.encoding or "utf-8", errors="replace")
    return parse_verified_company_domain(html, expected_company_name=company_name)


class SimplifyJobsAdapter(JobSourceAdapter):
    source_type = "simplifyjobs"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        if self.company_slug not in _ALLOWED_REPOSITORIES:
            return []
        url = (
            "https://raw.githubusercontent.com/SimplifyJobs/"
            f"{self.company_slug}/dev/.github/scripts/listings.json"
        )
        data = await get_json(url)
        if not isinstance(data, list):
            return []

        jobs: list[NormalizedJob] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            job = self._normalize(item)
            if job is not None:
                jobs.append(job)
        jobs = [
            job
            for job in jobs
            if job.posted_at is not None and self.is_recent(job.posted_at, days)
        ]
        jobs.sort(key=lambda job: job.posted_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        jobs = jobs[: settings.job_discovery_max_jobs_per_source]
        await self._enrich_verified_domains(jobs)
        return jobs

    async def _enrich_verified_domains(self, jobs: list[NormalizedJob]) -> None:
        semaphore = asyncio.Semaphore(_PROFILE_CONCURRENCY)
        cache: dict[tuple[str, str], str] = {}

        async def resolve(job: NormalizedJob) -> None:
            company_url = normalize_simplify_company_url(job.raw.get("company_url"))
            if not company_url:
                return
            cache_key = (company_url, normalize_company_key(job.company))
            if cache_key not in cache:
                async with semaphore:
                    if cache_key not in cache:
                        cache[cache_key] = await _verified_domain_for_company(
                            company_url, job.company
                        )
            job.company_domain = cache[cache_key]

        await asyncio.gather(*(resolve(job) for job in jobs))

    def _normalize(self, item: dict) -> NormalizedJob | None:
        if item.get("active") is not True or item.get("is_visible") is not True:
            return None
        external_id = str(item.get("id") or "").strip()
        company = str(item.get("company_name") or "").strip()
        title = str(item.get("title") or "").strip()
        application_url = str(item.get("url") or "").strip()
        if (
            not external_id
            or not company
            or not title
            or not application_url.startswith(("http://", "https://"))
        ):
            return None

        locations = item.get("locations") or []
        if not isinstance(locations, list):
            locations = [locations]
        location = "; ".join(str(value).strip() for value in locations if str(value).strip())
        level = _ALLOWED_REPOSITORIES[self.company_slug]
        sponsorship = str(item.get("sponsorship") or "").strip()
        degrees = item.get("degrees") or []
        if not isinstance(degrees, list):
            degrees = [degrees]

        return NormalizedJob(
            external_id=external_id,
            title=title,
            company=company,
            location=location or None,
            remote_type="remote" if "remote" in location.lower() else None,
            employment_type="Internship" if level == "internship" else "Full-time",
            seniority_level=level,
            posted_at=_parse_timestamp(item.get("date_posted")),
            application_url=application_url,
            source_url=f"https://github.com/SimplifyJobs/{self.company_slug}",
            description_raw="",
            description_clean="",
            source=self.source_type,
            degree_requirement=", ".join(str(value) for value in degrees if value) or None,
            work_authorization_notes=(
                sponsorship if sponsorship and sponsorship != "Other" else None
            ),
            raw=item,
        )

def _parse_timestamp(value: object) -> datetime | None:
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:  # tolerate millisecond timestamps
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
