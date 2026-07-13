"""Validation and confidence scoring for a parsed import draft.

Runs after either the AI parser or the deterministic parser. It flags likely
mistakes (empty/fake experience, projects or awards that leaked into the
experience list, missing header fields) and derives a real confidence score from
how complete each section is -- rather than a hard-coded number.
"""

from __future__ import annotations

import re
from typing import Any

# Words that strongly imply a line is really a project/award rather than a job.
PROJECT_HINT_RE = re.compile(r"\b(project|studio|platform|app|prototype|demo)\b", re.IGNORECASE)
AWARD_HINT_RE = re.compile(
    r"\b(award|scholarship|dean'?s list|cum laude|published|publication|honou?r|recipient)\b",
    re.IGNORECASE,
)


def validate_and_score(data: dict[str, Any]) -> tuple[list[str], dict[str, float]]:
    """Return ``(warnings, confidence)`` for a draft dict."""
    warnings: list[str] = []

    basic = data.get("basic_info") or {}
    experience = _as_list(data.get("experience"))
    projects = _as_list(data.get("projects"))
    education = _as_list(data.get("education"))
    awards = _as_list(data.get("awards"))
    skills = data.get("skills") or []
    skill_groups = _as_list(data.get("skill_groups"))

    empty_experience = 0
    title_only = 0
    seen: set[tuple[str, str]] = set()
    duplicate_experience = 0
    misplaced_projects = 0
    misplaced_awards = 0

    for item in experience:
        company = _clean(item.get("company"))
        title = _clean(item.get("title"))
        start = _clean(item.get("start_date"))
        end = _clean(item.get("end_date"))
        current = bool(item.get("currently_working"))
        bullets = item.get("bullets") or []

        if not company and not title:
            empty_experience += 1
        elif title and not company and not start and not end and not current:
            title_only += 1
            # A "title only" line with no company/dates is often a project name
            # or an award that leaked into experience.
            if PROJECT_HINT_RE.search(title):
                misplaced_projects += 1
            elif AWARD_HINT_RE.search(title) and not bullets:
                misplaced_awards += 1

        key = (company.lower(), title.lower())
        if company or title:
            if key in seen:
                duplicate_experience += 1
            seen.add(key)

    if empty_experience:
        warnings.append(
            f"{empty_experience} experience entr{_y(empty_experience)} had no company or title and "
            "were flagged. Remove them before saving."
        )
    if title_only:
        warnings.append(
            f"{title_only} experience entr{_y(title_only)} had a title but no company or dates. "
            "Please double-check them."
        )
    if duplicate_experience:
        warnings.append(
            f"{duplicate_experience} duplicate experience entr{_y(duplicate_experience)} were detected."
        )
    if misplaced_projects:
        warnings.append(
            "Some entries look like projects rather than jobs. Review the Experience section."
        )
    if misplaced_awards:
        warnings.append(
            "Some entries look like awards or publications rather than jobs. Review the Experience section."
        )

    if not _clean(basic.get("full_name")):
        warnings.append("We could not confidently detect your name. Please add it.")

    confidence = _score(basic, experience, projects, education, awards, skills, skill_groups)
    return warnings, confidence


def _score(
    basic: dict[str, Any],
    experience: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    education: list[dict[str, Any]],
    awards: list[dict[str, Any]],
    skills: list[Any],
    skill_groups: list[dict[str, Any]],
) -> dict[str, float]:
    header_fields = ["full_name", "email", "phone"]
    header = _ratio([_clean(basic.get(field)) for field in header_fields])

    exp_scores = []
    for item in experience:
        filled = [
            _clean(item.get("company")),
            _clean(item.get("title")),
            _clean(item.get("start_date")) or bool(item.get("currently_working")),
            bool(item.get("bullets")),
        ]
        exp_scores.append(sum(1 for value in filled if value) / len(filled))
    experience_score = round(sum(exp_scores) / len(exp_scores), 2) if exp_scores else 0.0

    project_score = 1.0 if projects and all(_clean(p.get("name")) for p in projects) else (0.6 if projects else 0.0)
    education_score = (
        1.0 if education and all(_clean(e.get("school")) for e in education) else (0.6 if education else 0.0)
    )
    awards_score = 1.0 if awards else 0.0
    skills_score = 1.0 if (skills or skill_groups) else 0.0

    present = [header, experience_score, education_score, skills_score]
    overall = round(sum(present) / len(present), 2)
    return {
        "overall": overall,
        "header": round(header, 2),
        "skills": skills_score,
        "education": education_score,
        "experience": experience_score,
        "projects": project_score,
        "awards": awards_score,
    }


def _ratio(values: list[Any]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value) / len(values)


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _y(count: int) -> str:
    return "y" if count == 1 else "ies"
