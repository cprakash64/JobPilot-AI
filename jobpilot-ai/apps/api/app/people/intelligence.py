from __future__ import annotations

# ruff: noqa: E501
import re
from urllib.parse import urlparse

from app.models.entities import JobPosting
from app.people.schemas import JobPeopleSearchProfile

_ROLE_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("machine_learning", ("machine learning", "ml engineer", "artificial intelligence", " ai ")),
    ("data", ("data scientist", "data engineer", "analytics", "business intelligence")),
    ("software_engineering", ("software", "frontend", "backend", "full stack", "platform", "devops")),
    ("product", ("product manager", "product designer")),
    ("security", ("security", "cybersecurity", "infosec")),
    ("sales", ("sales", "account executive", "business development")),
    ("marketing", ("marketing", "growth", "brand")),
    ("finance", ("finance", "accounting", "financial")),
]

_TITLES = {
    "machine_learning": {
        "recruiter": ["Technical Recruiter", "Engineering Recruiter", "AI Recruiter", "Talent Acquisition Partner", "Senior Technical Recruiter"],
        "manager": ["Machine Learning Engineering Manager", "Engineering Manager", "Applied AI Manager", "Director of Machine Learning", "Head of Machine Learning"],
        "team": ["Machine Learning Engineer", "Applied Scientist", "AI Engineer", "Software Engineer, Machine Learning", "Staff Machine Learning Engineer"],
    },
    "software_engineering": {
        "recruiter": ["Technical Recruiter", "Engineering Recruiter", "Talent Acquisition Partner"],
        "manager": ["Engineering Manager", "Director of Engineering", "Head of Engineering", "Software Engineering Manager"],
        "team": ["Software Engineer", "Senior Software Engineer", "Staff Software Engineer"],
    },
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
    configured = _TITLES.get(role_family or "", {})
    base = re.sub(r"\b(senior|sr\.?|junior|jr\.?|staff|lead|principal)\b", "", job_title, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip()
    recruiters = configured.get("recruiter", ["Recruiter", "Talent Acquisition Partner", "Technical Recruiter"])
    managers = configured.get("manager", [f"{base} Manager", "Department Manager", "Director"])
    team = configured.get("team", [job_title, base, f"Senior {base}"])
    return list(dict.fromkeys(recruiters)), list(dict.fromkeys(managers)), list(dict.fromkeys(team))


def extract_job_people_profile(job: JobPosting) -> JobPeopleSearchProfile:
    family = role_family_for(job.title, job.description_clean)
    recruiters, managers, team = expand_titles(job.title, family)
    department = {
        "machine_learning": "Engineering",
        "software_engineering": "Engineering",
        "data": "Data",
        "product": "Product",
        "security": "Security",
        "sales": "Sales",
        "marketing": "Marketing",
        "finance": "Finance",
    }.get(family or "")
    keywords = list(dict.fromkeys([*(job.required_skills or []), *(job.preferred_skills or [])]))[:20]
    reasons = ["Used the normalized job title and company record."]
    domain = validate_company_domain(job.company_domain)
    if domain:
        reasons.append("Validated the hiring company's professional domain.")
    if family:
        reasons.append("Mapped the role to a deterministic role-family taxonomy.")
    return JobPeopleSearchProfile(
        company_name=job.company.strip(),
        company_domain=domain,
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
