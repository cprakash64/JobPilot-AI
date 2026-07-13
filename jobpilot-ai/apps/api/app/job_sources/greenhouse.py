import html
from datetime import UTC, datetime

import httpx

from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html


class GreenhouseAdapter(JobSourceAdapter):
    source_type = "greenhouse"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.company_slug}/jobs?content=true"
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        jobs = []
        for item in response.json().get("jobs", []):
            # Use first_published (the real posting date). Never fall back to
            # updated_at, which would make an edited old job look freshly posted.
            posted_at = _parse_date(item.get("first_published"))
            if not self.is_recent(posted_at, days):
                continue
            location = (item.get("location") or {}).get("name")
            # Greenhouse returns HTML-entity-encoded content; unescape then strip.
            raw_content = html.unescape(item.get("content") or "")
            jobs.append(
                NormalizedJob(
                    external_id=str(item["id"]),
                    title=item.get("title", "Untitled role"),
                    company=self.company_name,
                    location=location,
                    remote_type="remote" if location and "remote" in location.lower() else None,
                    employment_type=None,
                    seniority_level=None,
                    posted_at=posted_at,
                    application_url=item.get("absolute_url", url),
                    source_url=item.get("absolute_url", url),
                    description_raw=raw_content,
                    description_clean=strip_html(raw_content),
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
