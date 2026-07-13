"""Ashby public job-board adapter.

Uses only Ashby's public posting API (no auth, no scraping):
    https://api.ashbyhq.com/posting-api/job-board/{org}
"""

from datetime import UTC, datetime

import httpx

from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html


class AshbyAdapter(JobSourceAdapter):
    source_type = "ashby"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.company_slug}?includeCompensation=true"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        jobs: list[NormalizedJob] = []
        for item in response.json().get("jobs", []):
            posted_at = _parse_date(item.get("publishedDate") or item.get("publishedAt"))
            if not self.is_recent(posted_at, days):
                continue
            location = item.get("location") or _address(item)
            description = item.get("descriptionHtml") or item.get("descriptionPlain") or ""
            apply_url = item.get("applyUrl") or item.get("jobUrl") or url
            remote_type = "remote" if item.get("isRemote") else None
            jobs.append(
                NormalizedJob(
                    external_id=str(item.get("id") or apply_url),
                    title=item.get("title", "Untitled role"),
                    company=self.company_name,
                    location=location,
                    remote_type=remote_type,
                    employment_type=item.get("employmentType"),
                    seniority_level=item.get("team") or None,
                    posted_at=posted_at,
                    application_url=apply_url,
                    source_url=item.get("jobUrl") or apply_url,
                    description_raw=description,
                    description_clean=strip_html(description),
                    source=self.source_type,
                    raw=item,
                )
            )
        return jobs


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _address(item: dict) -> str | None:
    address = item.get("address") or {}
    postal = address.get("postalAddress") or {}
    parts = [postal.get("addressLocality"), postal.get("addressRegion"), postal.get("addressCountry")]
    joined = ", ".join(part for part in parts if part)
    return joined or None
