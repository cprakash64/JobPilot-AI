"""Recruitee public offers adapter.

Uses the public company offers endpoint (no auth):
    https://{company}.recruitee.com/api/offers/
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html


class RecruiteeAdapter(JobSourceAdapter):
    source_type = "recruitee"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        from app.jobs.sources._http import get_json

        url = f"https://{self.company_slug}.recruitee.com/api/offers/"
        data = await get_json(url)
        offers = data.get("offers", []) if isinstance(data, dict) else []
        jobs: list[NormalizedJob] = []
        for item in offers[: settings.job_discovery_max_jobs_per_source]:
            job = self._normalize(item)
            if job is not None and self.is_recent(job.posted_at, days):
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> NormalizedJob | None:
        title = item.get("title")
        apply_url = item.get("careers_apply_url") or item.get("careers_url")
        if not title or not apply_url:
            return None
        parts = [item.get("city"), item.get("state_name") or item.get("state_code"), item.get("country")]
        location = item.get("location") or ", ".join(p for p in parts if p)
        remote_type = "remote" if item.get("remote") else None
        description = item.get("description") or ""
        return NormalizedJob(
            external_id=str(item.get("id") or apply_url),
            title=title,
            company=self.company_name,
            location=location or None,
            remote_type=remote_type,
            employment_type=item.get("employment_type_code") or item.get("category_code"),
            seniority_level=item.get("experience_code"),
            posted_at=_parse_date(item.get("published_at") or item.get("created_at")),
            application_url=apply_url,
            source_url=item.get("careers_url") or apply_url,
            description_raw=description,
            description_clean=strip_html(description),
            source=self.source_type,
            raw=item,
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None
