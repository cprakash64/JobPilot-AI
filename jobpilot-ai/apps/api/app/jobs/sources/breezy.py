"""Breezy HR adapter.

Uses the public published-positions JSON endpoint (no auth):
    https://{company}.breezy.hr/json
An optional token in the source config is sent as a bearer header if present.
No scraping.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html
from app.jobs.sources._http import get_json


class BreezyAdapter(JobSourceAdapter):
    source_type = "breezy"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        url = f"https://{self.company_slug}.breezy.hr/json"
        token = self.config.get("token")
        headers = {"Authorization": f"Bearer {token}"} if token else None
        data = await get_json(url, headers=headers)
        positions = data if isinstance(data, list) else data.get("positions", []) if isinstance(data, dict) else []
        jobs: list[NormalizedJob] = []
        for item in positions[: settings.job_discovery_max_jobs_per_source]:
            job = self._normalize(item)
            if job is not None and self.is_recent(job.posted_at, days):
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> NormalizedJob | None:
        title = item.get("name")
        apply_url = item.get("url")
        if not title or not apply_url:
            return None
        location = item.get("location") or {}
        parts = [location.get("city"), location.get("state"), (location.get("country") or {}).get("name") if isinstance(location.get("country"), dict) else location.get("country")]
        location_str = location.get("name") or ", ".join(p for p in parts if p)
        remote_type = "remote" if location.get("is_remote") else None
        description = item.get("description") or ""
        employment = item.get("type")
        return NormalizedJob(
            external_id=str(item.get("id") or apply_url),
            title=title,
            company=self.company_name,
            location=location_str or None,
            remote_type=remote_type,
            employment_type=employment.get("name") if isinstance(employment, dict) else employment,
            seniority_level=(item.get("experience") or {}).get("name") if isinstance(item.get("experience"), dict) else None,
            posted_at=_parse_date(item.get("published_date") or item.get("creation_date")),
            application_url=apply_url,
            source_url=apply_url,
            description_raw=description,
            description_clean=strip_html(description),
            source=self.source_type,
            raw=item,
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None
