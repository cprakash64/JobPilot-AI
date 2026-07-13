from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256


@dataclass
class NormalizedJob:
    external_id: str
    title: str
    company: str
    location: str | None
    remote_type: str | None
    employment_type: str | None
    seniority_level: str | None
    posted_at: datetime | None
    application_url: str
    source_url: str
    description_raw: str
    description_clean: str
    source: str = ""
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    years_experience_min: float | None = None
    degree_requirement: str | None = None
    work_authorization_notes: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    parse_confidence: float | None = None
    # Optional company branding, when an ATS exposes it. The API resolves a logo
    # from the curated domain map at serialize time when these are absent.
    company_domain: str = ""
    company_logo_url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def workplace_type(self) -> str:
        """Normalized remote/hybrid/onsite/unknown workplace type."""
        return normalize_workplace_type(self.remote_type, self.location)

    @property
    def dedupe_hash(self) -> str:
        raw = f"{self.company}|{normalize_title(self.title)}|{self.location}|{self.application_url}".lower()
        return sha256(raw.encode("utf-8")).hexdigest()


def normalize_title(title: str | None) -> str:
    """Lowercase, collapse whitespace, drop trailing parenthetical noise for dedupe."""
    import re

    value = re.sub(r"\s+", " ", (title or "").strip().lower())
    value = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", value)
    return value.strip()


def normalize_workplace_type(remote_type: str | None, location: str | None) -> str:
    text = f"{remote_type or ''} {location or ''}".lower()
    if "hybrid" in text:
        return "hybrid"
    if "remote" in text:
        return "remote"
    if any(word in text for word in ["on-site", "onsite", "in office", "in-office"]):
        return "onsite"
    if remote_type in {"remote", "hybrid", "onsite"}:
        return remote_type
    if location and location.strip():
        return "onsite"
    return "unknown"


class JobSourceAdapter(ABC):
    source_type: str

    def __init__(
        self,
        company_slug: str,
        company_name: str | None = None,
        config: dict | None = None,
    ) -> None:
        self.company_slug = company_slug
        self.company_name = company_name or company_slug
        # Optional per-source configuration (API tokens, base URLs, region, etc.)
        # supplied by the catalog for ATSes that require it (Workable/Teamtailor).
        self.config = config or {}

    @abstractmethod
    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        raise NotImplementedError

    def is_recent(self, posted_at: datetime | None, days: int = 7) -> bool:
        if posted_at is None:
            return True
        return posted_at >= datetime.now(UTC) - timedelta(days=days)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()

