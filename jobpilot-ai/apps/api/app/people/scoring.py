from __future__ import annotations

# ruff: noqa: E501
import re
from datetime import UTC, datetime

from app.people.schemas import JobPeopleSearchProfile, PeopleCategory, ProviderPerson
from app.people.title_ontology import TITLE_ONTOLOGY_VERSION, normalize_title, title_similarity

SCORING_VERSION = f"people-v2:{TITLE_ONTOLOGY_VERSION}"
WEIGHTS = {
    "likely_recruiter": {"title": 42, "role": 13, "company": 25, "department": 3, "location": 2, "quality": 15},
    "potential_hiring_manager": {"department": 16, "title": 30, "company": 25, "role": 18, "location": 2, "seniority": 4, "quality": 5},
    "potential_referrer": {"role": 32, "school": 15, "employer": 10, "company": 23, "location": 3, "seniority": 7, "quality": 10},
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
            best = max(best, len(tokens & other) / max(1, len(tokens | other)))
    return min(1.0, best)


def company_match_strength(person: ProviderPerson, profile: JobPeopleSearchProfile) -> tuple[float, str]:
    person_domain = (person.current_company_domain or "").lower()
    if profile.company_domain and person_domain == profile.company_domain:
        return profile.domain_confidence or 1.0, "canonical_domain"
    if profile.parent_company_domain and person_domain == profile.parent_company_domain:
        return 0.42, "related_parent_domain"
    alias_similarity = _similar(
        person.current_company_name,
        [profile.company_name, *profile.company_aliases],
    )
    if alias_similarity >= 0.74:
        return 0.72 * alias_similarity, "company_alias"
    return min(0.35, alias_similarity), "weak_company_name"


def _freshness(person: ProviderPerson) -> float:
    checked = (
        person.employment_verified_at
        or person.provider_employment_updated_at
        or person.provider_record_observed_at
        or person.source_last_updated_at
    )
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
    company, _company_kind = company_match_strength(person, profile)
    location = _similar(person.location, [profile.location or ""]) if profile.location else 0.5
    quality = (1.0 if person.linkedin_url else 0.5) * 0.4 + _freshness(person) * 0.6
    if category == "likely_recruiter":
        parts = {
            "title": title_similarity(person.current_title, profile.recruiter_titles),
            "role": title_similarity(person.current_title, [profile.role_family or "", *profile.keywords]),
            "company": company,
            "department": _similar(person.department, [profile.department or ""]),
            "location": location,
            "quality": quality,
        }
    elif category == "potential_hiring_manager":
        parts = {
            "department": _similar(person.department, [profile.department or ""]),
            "title": title_similarity(person.current_title, profile.hiring_manager_titles),
            "company": company,
            "role": title_similarity(person.current_title, [profile.role_family or "", *profile.team_member_titles]),
            "location": location,
            "seniority": (
                1.0
                if normalize_title(person.seniority) in {"manager", "director", "head", "vp"}
                or any(
                    token in normalize_title(person.current_title).split()
                    for token in ("manager", "director", "head", "vp", "lead")
                )
                else 0.2
            ),
            "quality": quality,
        }
    else:
        parts = {
            "role": title_similarity(person.current_title, profile.team_member_titles),
            "school": 1.0 if shared_school else 0,
            "employer": 1.0 if shared_employer else 0,
            "company": company,
            "location": location,
            "seniority": 0.8 if normalize_text(person.seniority) not in {"c suite", "executive"} else 0,
            "quality": quality,
        }
    return round(sum(WEIGHTS[category][key] * parts[key] for key in parts), 1)


def candidate_rejection_reasons(
    category: PeopleCategory,
    person: ProviderPerson,
    profile: JobPeopleSearchProfile,
    *,
    relevance: float,
    data_confidence: float,
    relevance_threshold: float,
    confidence_threshold: float,
) -> list[str]:
    reasons: list[str] = []
    company_strength, company_kind = company_match_strength(person, profile)
    expected_titles = (
        profile.recruiter_titles
        if category == "likely_recruiter"
        else profile.hiring_manager_titles
        if category == "potential_hiring_manager"
        else profile.team_member_titles
    )
    role_similarity = title_similarity(person.current_title, expected_titles)
    normalized_seniority = normalize_title(person.seniority)
    if company_strength < 0.4:
        reasons.append("company_domain_mismatch")
    elif company_kind == "related_parent_domain":
        reasons.append("weak_company_confidence")
    if role_similarity < (0.42 if category == "potential_referrer" else 0.5):
        reasons.append("weak_role_similarity")
        reasons.append("title_mismatch")
    if category == "potential_hiring_manager" and not (
        normalized_seniority in {"manager", "director", "head", "vp"}
        or any(
            token in normalize_title(person.current_title).split()
            for token in ("manager", "director", "head", "vp", "lead")
        )
    ):
        reasons.append("seniority_mismatch")
    if _freshness(person) < 0.45:
        reasons.append("stale_employment")
    if relevance < relevance_threshold:
        reasons.append("below_relevance_threshold")
    if data_confidence < confidence_threshold:
        reasons.append("below_confidence_threshold")
    return list(dict.fromkeys(reasons))


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
    employment_validation_status: str | None = None,
) -> tuple[list[str], list[str]]:
    reasons = [
        (
            "Current employment verified by multiple professional data sources."
            if employment_validation_status
            == "confirmed_exact_company_verified"
            else "Currently listed by a professional data source at the hiring company."
        )
    ]
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
    if employment_validation_status != "confirmed_exact_company_verified":
        limitations.append("Current employment has not been independently re-verified.")
    _, company_kind = company_match_strength(person, profile)
    if company_kind == "related_parent_domain":
        limitations.append(
            "This person is listed at a related parent-domain organization, not the exact hiring company."
        )
    return reasons[:3], limitations[:2]
