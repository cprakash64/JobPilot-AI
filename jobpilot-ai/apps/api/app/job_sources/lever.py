from datetime import UTC, datetime

import httpx

from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html


class LeverAdapter(JobSourceAdapter):
    source_type = "lever"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        url = f"https://api.lever.co/v0/postings/{self.company_slug}?mode=json"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        jobs = []
        for item in response.json():
            created_ms = item.get("createdAt")
            posted_at = (
                datetime.fromtimestamp(created_ms / 1000, tz=UTC)
                if isinstance(created_ms, int | float)
                else None
            )
            if not self.is_recent(posted_at, days):
                continue
            categories = item.get("categories") or {}
            lists = " ".join(section.get("content", "") for section in item.get("lists", []))
            clean = strip_html(f"{item.get('description', '')} {lists}")
            hosted_url = item.get("hostedUrl", url)
            jobs.append(
                NormalizedJob(
                    external_id=item.get("id", hosted_url),
                    title=item.get("text", "Untitled role"),
                    company=self.company_name,
                    location=categories.get("location"),
                    remote_type="remote" if "remote" in (categories.get("location") or "").lower() else None,
                    employment_type=categories.get("commitment"),
                    seniority_level=categories.get("team"),
                    posted_at=posted_at,
                    application_url=hosted_url,
                    source_url=hosted_url,
                    description_raw=item.get("description", ""),
                    description_clean=clean,
                    source=self.source_type,
                    raw=item,
                )
            )
        return jobs

