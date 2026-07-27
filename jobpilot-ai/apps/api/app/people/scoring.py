from __future__ import annotations

# ruff: noqa: E501
import re
from datetime import UTC, datetime

from app.people.schemas import JobPeopleSearchProfile, PeopleCategory, ProviderPerson

SCORING_VERSION = "people-v1"
WEIGHTS = {
    "likely_recruiter": {"title": 35, "role": 20, "company": 20, "department": 10, "location": 5, "quality": 10},
    "potential_hiring_manager": {"department": 30, "title": 25, "company": 20, "role": 15, "location": 5, "quality": 5},
    "potential_referrer": {"role": 30, "school": 20, "employer": 15, "company": 15, "location": 5, "seniority": 5, "quality": 10},
}


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _similar(value: str | None, choices: list[str]) -> float:
    tokens = set(normalize_text(value).split())
    if not tokens:
        return 0
    best = 0.0
    for choice in choices:
        other = set(normalize_text(choice).split())
        if other:
            best = max(best, len(tokens & other) / max(1, min(len(tokens), len(other))))
    return min(1.0, best)


def _freshness(person: ProviderPerson) -> float:
    checked = person.employment_verified_at or person.source_last_updated_at
    if not checked:
        return 0.35
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    days = max(0, (datetime.now(UTC) - checked).days)
    if days <= 90:
        return 1.0
    if days <= 365:
        return 0.75
    if days <= 730:
        return 0.45
    return 0.15


def score_candidate(
    category: PeopleCategory,
    person: ProviderPerson,
    profile: JobPeopleSearchProfile,
    *,
    shared_school: bool = False,
    shared_employer: bool = False,
) -> float:
    company = 1.0 if (
        profile.company_domain and person.current_company_domain == profile.company_domain
    ) else _similar(person.current_company_name, [profile.company_name])
    location = _similar(person.location, [profile.location or ""]) if profile.location else 0.5
    quality = (1.0 if person.linkedin_url else 0.5) * 0.4 + _freshness(person) * 0.6
    if category == "likely_recruiter":
        parts = {
            "title": _similar(person.current_title, profile.recruiter_titles),
            "role": _similar(person.current_title, [profile.role_family or "", *profile.keywords]),
            "company": company,
            "department": _similar(person.department, [profile.department or ""]),
            "location": location,
            "quality": quality,
        }
    elif category == "potential_hiring_manager":
        parts = {
            "department": _similar(person.department, [profile.department or ""]),
            "title": _similar(person.current_title, profile.hiring_manager_titles),
            "company": company,
            "role": _similar(person.current_title, [profile.role_family or "", *profile.team_member_titles]),
            "location": location,
            "quality": quality,
        }
    else:
        parts = {
            "role": _similar(person.current_title, profile.team_member_titles),
            "school": 1.0 if shared_school else 0,
            "employer": 1.0 if shared_employer else 0,
            "company": company,
            "location": location,
            "seniority": 0.8 if normalize_text(person.seniority) not in {"c suite", "executive"} else 0,
            "quality": quality,
        }
    return round(sum(WEIGHTS[category][key] * parts[key] for key in parts), 1)


def confidence(person: ProviderPerson, *, corroborating_sources: int = 1, conflicts: int = 0) -> float:
    value = 0.25
    value += 0.25 if person.linkedin_url else 0
    value += 0.25 * _freshness(person)
    value += 0.15 if person.current_company_domain else 0
    value += min(0.1, max(0, corroborating_sources - 1) * 0.05)
    value -= conflicts * 0.15
    return round(max(0, min(1, value)), 2)


def confidence_label(value: float) -> str:
    if value >= 0.78:
        return "high"
    if value >= 0.55:
        return "moderate"
    return "limited"


def explanations(
    category: PeopleCategory,
    person: ProviderPerson,
    profile: JobPeopleSearchProfile,
    *,
    shared_school: str | None = None,
    shared_employer: str | None = None,
) -> tuple[list[str], list[str]]:
    reasons = ["Currently listed by a professional data source at the hiring company."]
    limitations: list[str] = []
    if category == "likely_recruiter":
        reasons.append("Has a recruiting or talent-acquisition title relevant to this role.")
        limitations.append("Recruiting responsibility for this specific opening has not been confirmed.")
    elif category == "potential_hiring_manager":
        reasons.append("Has managerial seniority in a function related to the advertised role.")
        limitations.append("Exact hiring responsibility and team membership have not been confirmed.")
    else:
        reasons.append("Works in a role closely related to the advertised function.")
        limitations.append("Willingness to provide a referral has not been established.")
    if shared_school:
        reasons.append(f"Attended the same school: {shared_school}.")
    if shared_employer:
        reasons.append(f"Previously worked at the same employer: {shared_employer}.")
    if not person.employment_verified_at:
        limitations.append("Current employment has not been independently re-verified.")
    return reasons[:3], limitations[:2]
