"""SmartRecruiters public postings adapter.

Uses the public postings API (no auth):
    https://api.smartrecruiters.com/v1/companies/{companyId}/postings
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs.sources._http import get_json


class SmartRecruitersAdapter(JobSourceAdapter):
    source_type = "smartrecruiters"

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        base = f"https://api.smartrecruiters.com/v1/companies/{self.company_slug}/postings"
        max_jobs = settings.job_discovery_max_jobs_per_source
        jobs: list[NormalizedJob] = []
        offset = 0
        while len(jobs) < max_jobs:
            data = await get_json(base, params={"limit": 100, "offset": offset})
            content = data.get("content", []) if isinstance(data, dict) else []
            if not content:
                break
            for item in content:
                job = self._normalize(item)
                if job is not None and self.is_recent(job.posted_at, days):
                    jobs.append(job)
            offset += len(content)
            if offset >= int(data.get("totalFound", 0)) or len(content) < 100:
                break
        return jobs[:max_jobs]

    def _normalize(self, item: dict) -> NormalizedJob | None:
        posting_id = item.get("id")
        title = item.get("name")
        if not posting_id or not title:
            return None
        location = item.get("location") or {}
        parts = [location.get("city"), location.get("region"), _country(location.get("country"))]
        location_str = location.get("fullLocation") or ", ".join(p for p in parts if p)
        remote_type = "remote" if location.get("remote") else ("hybrid" if location.get("hybrid") else None)
        apply_url = f"https://jobs.smartrecruiters.com/{self.company_slug}/{posting_id}"
        return NormalizedJob(
            external_id=str(posting_id),
            title=title,
            company=self.company_name,
            location=location_str or None,
            remote_type=remote_type,
            employment_type=_employment(item.get("typeOfEmployment")),
            seniority_level=(item.get("experienceLevel") or {}).get("id") if isinstance(item.get("experienceLevel"), dict) else None,
            posted_at=_parse_date(item.get("releasedDate")),
            application_url=apply_url,
            source_url=apply_url,
            description_raw="",
            description_clean="",
            source=self.source_type,
            raw=item,
        )


def _country(code: str | None) -> str | None:
    if not code:
        return None
    return "United States" if code.lower() == "us" else code.upper()


def _employment(value) -> str | None:
    if isinstance(value, dict):
        return value.get("label") or value.get("id")
    return value


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None
