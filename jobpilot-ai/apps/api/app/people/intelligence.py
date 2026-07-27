from __future__ import annotations

# ruff: noqa: E501
import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CompanyBranding, JobPosting
from app.people.schemas import CompanyIdentity, JobPeopleSearchProfile
from app.people.title_ontology import (
    is_early_career_job,
    manager_title_groups,
    recruiter_title_groups,
    team_titles,
)

_ROLE_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("machine_learning", ("machine learning", "ml engineer", "artificial intelligence", " ai ")),
    ("embedded_systems", ("embedded", "firmware", "rtos", "microcontroller")),
    ("data", ("data scientist", "data engineer", "analytics", "business intelligence")),
    ("software_engineering", ("software", "frontend", "backend", "full stack", "platform", "devops")),
    ("product", ("product manager", "product designer")),
    ("security", ("security", "cybersecurity", "infosec")),
    ("sales", ("sales", "account executive", "business development")),
    ("marketing", ("marketing", "growth", "brand")),
    ("finance", ("finance", "accounting", "financial")),
    ("healthcare", ("healthcare", "clinical", "medical device", "biomedical")),
]

_NON_EMPLOYER_HOSTS = {
    "simplify.jobs", "github.com", "linkedin.com", "indeed.com", "glassdoor.com",
    "job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co",
    "jobs.ashbyhq.com", "myworkdayjobs.com",
}


def validate_company_domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").strip(".")
    if not host or host == "localhost" or "." not in host or len(host) > 253:
        return None
    if any(part in {"linkedin", "facebook", "gmail", "yahoo", "outlook"} for part in host.split(".")):
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host:
        return None
    return host[4:] if host.startswith("www.") else host


def role_family_for(title: str, description: str = "") -> str | None:
    haystack = f" {title} {description[:4000]} ".lower()
    for family, phrases in _ROLE_FAMILIES:
        if any(phrase in haystack for phrase in phrases):
            return family
    return None


def expand_titles(job_title: str, role_family: str | None) -> tuple[list[str], list[str], list[str]]:
    base = re.sub(r"\b(senior|sr\.?|junior|jr\.?|staff|lead|principal)\b", "", job_title, flags=re.I)
    base = re.sub(r"\b(intern(ship)?|new grad(uate)?)\b", "", base, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip()
    recruiters = [
        title
        for group in recruiter_title_groups(early_career=is_early_career_job(job_title))
        for title in group.titles
    ]
    managers = [
        title for group in manager_title_groups(role_family, base) for title in group.titles
    ]
    team = team_titles(role_family, job_title)
    return list(dict.fromkeys(recruiters)), list(dict.fromkeys(managers)), list(dict.fromkeys(team))


def _normalized_company(value: str | None) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return re.sub(
        r"\b(incorporated|inc|llc|ltd|limited|corporation|corp|company|co)\b",
        "",
        value,
    ).strip()


def _url_domain(value: str | None) -> str | None:
    domain = validate_company_domain(value)
    if not domain:
        return None
    if domain in _NON_EMPLOYER_HOSTS or any(domain.endswith(f".{host}") for host in _NON_EMPLOYER_HOSTS):
        return None
    return domain


def _related_parent_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = domain.split(".")
    # This is evidence of a related domain scope, not proof of legal ownership.
    if len(parts) == 3 and len(parts[-1]) >= 2 and parts[0] not in {"www", "jobs", "careers"}:
        return ".".join(parts[-2:])
    return None


def resolve_company_identity(db: Session | None, job: JobPosting) -> CompanyIdentity:
    aliases = [job.company.strip()]
    canonical_name = job.company.strip()
    domain = validate_company_domain(job.company_domain)
    confidence = 0.92 if domain else 0.0
    evidence = "job_company_record" if domain else "normalized_company_name"

    branding = None
    if db is not None:
        branding = db.scalar(
            select(CompanyBranding).where(
                CompanyBranding.normalized_key == _normalized_company(job.company)
            )
        )
    if branding:
        if branding.canonical_name:
            aliases.append(branding.canonical_name)
            canonical_name = branding.canonical_name
        branding_domain = validate_company_domain(branding.domain)
        if branding_domain:
            domain = branding_domain
            confidence = 0.96 if branding.source in {"catalog", "ats", "curated"} else 0.82
            evidence = f"company_branding_{branding.source}"

    official_application_domain = _url_domain(job.application_url)
    if official_application_domain and not domain:
        domain = official_application_domain
        confidence = 0.8
        evidence = "official_application_hostname"

    raw = job.raw_json if isinstance(job.raw_json, dict) else {}
    for value in raw.get("company_aliases", []) if isinstance(raw.get("company_aliases"), list) else []:
        if isinstance(value, str) and value.strip():
            aliases.append(value.strip())
    parent_name = raw.get("parent_company") if isinstance(raw.get("parent_company"), str) else None
    parent_domain = validate_company_domain(
        raw.get("parent_company_domain")
        if isinstance(raw.get("parent_company_domain"), str)
        else None
    )
    if not parent_domain:
        parent_domain = _related_parent_domain(domain)

    return CompanyIdentity(
        canonical_name=canonical_name,
        canonical_domain=domain,
        aliases=list(dict.fromkeys(alias for alias in aliases if alias))[:20],
        parent_name=parent_name,
        parent_domain=parent_domain if parent_domain != domain else None,
        domain_confidence=confidence,
        evidence_source=evidence,
    )


def extract_job_people_profile(
    job: JobPosting, db: Session | None = None
) -> JobPeopleSearchProfile:
    family = role_family_for(job.title, job.description_clean)
    recruiters, managers, team = expand_titles(job.title, family)
    identity = resolve_company_identity(db, job)
    department = {
        "machine_learning": "Engineering",
        "software_engineering": "Engineering",
        "data": "Data",
        "product": "Product",
        "security": "Security",
        "sales": "Sales",
        "marketing": "Marketing",
        "finance": "Finance",
        "embedded_systems": "Engineering",
        "healthcare": "Clinical Engineering",
    }.get(family or "")
    keywords = list(dict.fromkeys([*(job.required_skills or []), *(job.preferred_skills or [])]))[:20]
    reasons = ["Used the normalized job title and company record."]
    domain = identity.canonical_domain
    if domain:
        reasons.append("Validated the hiring company's professional domain.")
    if family:
        reasons.append("Mapped the role to a deterministic role-family taxonomy.")
    return JobPeopleSearchProfile(
        company_name=identity.canonical_name,
        company_domain=domain,
        company_aliases=identity.aliases,
        parent_company_name=identity.parent_name,
        parent_company_domain=identity.parent_domain,
        domain_confidence=identity.domain_confidence,
        company_evidence_source=identity.evidence_source,
        job_title=job.title.strip(),
        role_family=family,
        department=department,
        seniority=job.seniority_level,
        location=job.location,
        employment_type=job.employment_type,
        keywords=[str(v)[:100] for v in keywords if str(v).strip()],
        recruiter_titles=recruiters,
        hiring_manager_titles=managers,
        team_member_titles=team,
        extraction_confidence=0.9 if domain and family else 0.72 if family else 0.58,
        extraction_reasons=reasons,
    )
