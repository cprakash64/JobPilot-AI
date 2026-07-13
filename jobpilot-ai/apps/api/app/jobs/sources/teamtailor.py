"""Teamtailor adapter (config-gated).

Uses the official Teamtailor API, which requires a per-company API key and a
version header. If no token is configured, the adapter returns no jobs (it is
skipped) rather than scraping. Docs: https://docs.teamtailor.com/
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob, strip_html
from app.jobs.sources._http import get_json

DEFAULT_VERSION = "20210218"


class TeamtailorAdapter(JobSourceAdapter):
    source_type = "teamtailor"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        token = self.config.get("token") or self.config.get("api_key")
        if not token:
            # No credentials -> skip cleanly (never scrape).
            return []
        base = self.config.get("base_url", "https://api.teamtailor.com/v1")
        version = self.config.get("version", DEFAULT_VERSION)
        headers = {"Authorization": f"Token token={token}", "X-Api-Version": version}
        data = await get_json(
            f"{base}/jobs",
            headers=headers,
            params={"filter[status]": "published", "page[size]": settings.job_discovery_max_jobs_per_source},
        )
        entries = data.get("data", []) if isinstance(data, dict) else []
        jobs: list[NormalizedJob] = []
        for item in entries:
            job = self._normalize(item)
            if job is not None and self.is_recent(job.posted_at, days):
                jobs.append(job)
        return jobs

    def _normalize(self, item: dict) -> NormalizedJob | None:
        attrs = item.get("attributes") or {}
        title = attrs.get("title")
        apply_url = attrs.get("apply-url") or attrs.get("careers-url") or (item.get("links") or {}).get("careersite-job-url")
        if not title or not apply_url:
            return None
        description = attrs.get("body") or ""
        remote_status = str(attrs.get("remote-status") or "").lower()
        remote_type = "remote" if remote_status in {"fully", "hybrid"} else None
        return NormalizedJob(
            external_id=str(item.get("id") or apply_url),
            title=title,
            company=self.company_name,
            location=attrs.get("location") or None,
            remote_type=remote_type,
            employment_type=attrs.get("employment-type"),
            seniority_level=attrs.get("employment-level"),
            posted_at=_parse_date(attrs.get("created-at") or attrs.get("start-date")),
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
