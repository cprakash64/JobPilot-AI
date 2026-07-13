"""Turn a saved user profile into structured job-search criteria.

Deterministic by design: given a profile (target roles, skills, experience,
levels, location/remote preference, sponsorship), it produces the search
criteria the ingestion layer uses to query sources and the matcher uses to
score. An optional AI hook can expand role queries when OPENAI is configured,
but the deterministic expansion always runs so discovery works without AI.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.entities import Experience, UserProfile

# Seniority ladder used to keep new-grad users away from senior roles and to map
# free-text levels onto a small controlled vocabulary.
SENIORITY_ORDER = ["intern", "new grad", "junior", "mid", "senior", "staff", "principal", "lead"]

# Skill/role -> extra role-title queries. Kept small, generic, and additive; this
# is NOT tied to any single user's profile.
ROLE_EXPANSION: dict[str, list[str]] = {
    "machine learning": ["Machine Learning Engineer", "AI Engineer", "Applied AI Engineer", "ML Engineer"],
    "ml": ["Machine Learning Engineer", "ML Engineer"],
    "ai": ["AI Engineer", "Applied AI Engineer", "Software Engineer, AI"],
    "computer vision": ["Computer Vision Engineer", "Perception Engineer"],
    "cv": ["Computer Vision Engineer"],
    "nlp": ["NLP Engineer", "Machine Learning Engineer, NLP"],
    "rag": ["AI Engineer", "Applied AI Engineer"],
    "llm": ["AI Engineer", "Applied AI Engineer"],
    "mlops": ["MLOps Engineer", "ML Platform Engineer"],
    "pytorch": ["Machine Learning Engineer", "Deep Learning Engineer"],
    "tensorflow": ["Machine Learning Engineer"],
    "data": ["Data Engineer", "Data Scientist"],
    "backend": ["Backend Engineer", "Software Engineer"],
    "frontend": ["Frontend Engineer", "Software Engineer"],
    "full stack": ["Full Stack Engineer", "Software Engineer"],
    "devops": ["DevOps Engineer", "Platform Engineer"],
    "security": ["Security Engineer", "Security Analyst"],
}

SKILL_TO_ROLE_HINT = {
    "python": "Software Engineer",
    "react": "Frontend Engineer",
    "typescript": "Frontend Engineer",
    "fastapi": "Backend Engineer",
    "sql": "Data Engineer",
}


class SearchCriteria(BaseModel):
    role_queries: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_preference: str = ""
    seniority_targets: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    sponsorship_required: bool | None = None

    def is_empty(self) -> bool:
        return not (self.role_queries or self.skills)


def build_search_criteria(
    profile: UserProfile | None,
    experiences: list[Experience] | None = None,
) -> SearchCriteria:
    if profile is None:
        return SearchCriteria()

    target_roles = _clean_list(profile.target_roles)
    skills = _clean_list(profile.skills)
    experiences = experiences or []
    experience_titles = _clean_list([exp.title for exp in experiences])

    role_queries = list(target_roles)
    # Seed from experience titles (e.g. an ML Engineer's past roles) and expand.
    role_queries.extend(experience_titles)
    role_queries.extend(_expand_roles(target_roles + experience_titles + skills))
    # If the user gave skills but no roles at all, fall back to skill-based roles.
    if not role_queries:
        role_queries.extend(_skill_role_hints(skills))

    seniority_targets = _seniority_targets(profile.target_levels, experiences)

    return SearchCriteria(
        role_queries=_dedupe(role_queries)[:12],
        skills=skills,
        locations=_clean_list(profile.preferred_locations),
        remote_preference=(profile.remote_preference or "").strip(),
        seniority_targets=seniority_targets,
        excluded_terms=_excluded_terms(seniority_targets),
        sponsorship_required=profile.requires_sponsorship if profile.requires_sponsorship else None,
    )


def _expand_roles(sources: list[str]) -> list[str]:
    expanded: list[str] = []
    haystack = " ".join(sources).lower()
    for key, roles in ROLE_EXPANSION.items():
        if key in haystack:
            expanded.extend(roles)
    return expanded


def _skill_role_hints(skills: list[str]) -> list[str]:
    hints = [SKILL_TO_ROLE_HINT[skill.lower()] for skill in skills if skill.lower() in SKILL_TO_ROLE_HINT]
    return hints or (["Software Engineer"] if skills else [])


def _seniority_targets(levels: Any, experiences: list[Experience]) -> list[str]:
    targets = _map_levels(_clean_list(levels))
    if targets:
        return targets
    # Infer from years of experience when explicit levels are missing.
    years = _estimate_years(experiences)
    if years is None:
        return []
    if years < 1:
        return ["new grad", "junior"]
    if years < 3:
        return ["junior", "mid"]
    if years < 6:
        return ["mid", "senior"]
    return ["senior", "staff"]


def _map_levels(levels: list[str]) -> list[str]:
    mapped: list[str] = []
    for level in levels:
        low = level.lower()
        if "intern" in low:
            mapped.append("intern")
        elif "new grad" in low or "entry" in low or "graduate" in low:
            mapped.append("new grad")
        elif "junior" in low or "associate" in low or low.endswith(" i"):
            mapped.append("junior")
        elif "mid" in low:
            mapped.append("mid")
        elif "staff" in low:
            mapped.append("staff")
        elif "principal" in low:
            mapped.append("principal")
        elif "lead" in low:
            mapped.append("lead")
        elif "senior" in low or "sr" in low:
            mapped.append("senior")
    return _dedupe(mapped)


def _estimate_years(experiences: list[Experience]) -> float | None:
    if not experiences:
        return None
    total_days = 0
    counted = False
    for exp in experiences:
        if exp.start_date is None:
            continue
        end = exp.end_date
        from datetime import date

        end_date = end or date.today()
        total_days += max(0, (end_date - exp.start_date).days)
        counted = True
    if not counted:
        return None
    return round(total_days / 365.0, 1)


def _excluded_terms(seniority_targets: list[str]) -> list[str]:
    """Down-rank/exclude clearly-too-senior roles for early-career users."""
    junior_only = seniority_targets and set(seniority_targets) <= {"intern", "new grad", "junior"}
    if junior_only:
        return ["Staff", "Principal", "Director", "VP", "Head of", "Lead"]
    return []


def _clean_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return _dedupe(str(item).strip() for item in value if str(item).strip())


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result
