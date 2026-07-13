"""Workable adapter.

Uses the public published-jobs widget API by default:
    https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
If a per-company API token is supplied in the source config, uses the official
SPI endpoint https://{account}.workable.com/spi/v3/jobs with a Bearer token.
No scraping, no login.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html
from app.jobs.sources._http import get_json


class WorkableAdapter(JobSourceAdapter):
    source_type = "workable"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        token = self.config.get("token") or self.config.get("api_key")
        if token:
            url = f"https://{self.company_slug}.workable.com/spi/v3/jobs"
            data = await get_json(url, headers={"Authorization": f"Bearer {token}"})
        else:
            url = f"https://apply.workable.com/api/v1/widget/accounts/{self.company_slug}"
            data = await get_json(url, params={"details": "true"})
        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
        jobs: list[NormalizedJob] = []
        for item in raw_jobs[: settings.job_discovery_max_jobs_per_source]:
            job = self._normalize(item)
            if job is not None and self.is_recent(job.posted_at, days):
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> NormalizedJob | None:
        title = item.get("title") or item.get("full_title")
        apply_url = item.get("application_url") or item.get("url") or item.get("shortlink")
        if not title or not apply_url:
            return None
        parts = [item.get("city") or item.get("location", {}).get("city") if isinstance(item.get("location"), dict) else item.get("city"),
                 item.get("country") or (item.get("location", {}) or {}).get("country") if isinstance(item.get("location"), dict) else item.get("country")]
        location = item.get("location_str") or ", ".join(p for p in parts if p)
        workplace = (item.get("workplace") or item.get("remote") or "")
        remote_type = "remote" if str(workplace).lower() in {"remote", "true"} else None
        description = item.get("description") or item.get("full_description") or ""
        return NormalizedJob(
            external_id=str(item.get("id") or item.get("shortcode") or apply_url),
            title=title,
            company=self.company_name,
            location=location or None,
            remote_type=remote_type,
            employment_type=item.get("employment_type") or item.get("type"),
            seniority_level=item.get("experience"),
            posted_at=_parse_date(item.get("published_on") or item.get("created_at")),
            application_url=apply_url,
            source_url=item.get("url") or apply_url,
            description_raw=description,
            description_clean=strip_html(description),
            source=self.source_type,
            raw=item,
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value if "T" in str(value) else f"{value}T00:00:00+00:00"
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None
