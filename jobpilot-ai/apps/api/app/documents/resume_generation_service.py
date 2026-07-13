"""Tailored, truthful resume generation.

Builds a one-page, ATS-friendly resume from the user's saved profile and a
specific job. When OpenAI is configured it generates a tailored resume (summary,
skill ordering, rephrased bullets) and the guardrail strips anything the profile
does not support; entities (companies/titles/dates/schools) are always anchored
to the profile so nothing is invented. Without OpenAI — or if the AI call fails —
it falls back to a fully deterministic build and reports the exact reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.document_guardrail_service import (
    ProfileFacts,
    build_profile_facts,
    missing_job_skills,
    validate_resume,
)
from app.documents.resume_compaction_service import compact_resume, group_skills
from app.models.entities import JobMatch, JobPosting
from app.services.documents import profile_payload, public_dict


@dataclass
class ResumeResult:
    content: dict[str, Any]
    markdown: str
    plain_text: str = ""
    quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unsupported_claims_removed: list[str] = field(default_factory=list)
    model_used: str = "deterministic-local"
    ai_used: bool = False
    title: str = ""


async def generate_resume(db: Session, user_id: int, job: JobPosting) -> ResumeResult:
    payload = profile_payload(db, user_id)
    payload["job"] = public_dict(job)
    facts = build_profile_facts(payload)

    job_skills = _lower_set((job.required_skills or []) + (job.preferred_skills or []))
    # Deterministic base is always built first: it guarantees every real
    # experience/project/education entry is present with verbatim bullets. The
    # "working" dict uses flat skills + company/title so the guardrail can run.
    working = _build_resume(payload, job, facts, job_skills)

    warnings: list[str] = []
    model_used = "deterministic-local"
    ai_used = False

    result = await _ai_resume(db, payload, job, user_id)
    if result.ai_used:
        working = _merge_ai_resume(working, result.data, facts)
        model_used = result.model_used
        ai_used = True
    else:
        warnings.append(f"Generated with template mode because {result.error}.")

    working, removed = validate_resume(working, facts)

    # Collapse legacy/import duplicates and enforce one-page limits before we
    # write anything. This runs after the guardrail so only truthful content is
    # ranked, and dedupes even when the saved profile carries bad duplicates.
    # Projects are ranked against the broader job keyword set (title + JD tokens),
    # not just declared skills, so relevant projects are kept for the resume.
    working = compact_resume(working, job_skills, job_keywords=_job_keywords(job, job_skills))

    # Missing job-required skills are reported, never inserted into the resume.
    missing = missing_job_skills(list(job.required_skills or []), facts)

    # Transform the guardrailed working dict into the ATS-friendly structured
    # response shape (grouped skills, experience dates, full_name).
    content = _structured_content(working, job, job_skills)
    content["missing_skills"] = missing

    quality = _quality(content, removed, missing, job, job_skills)
    if missing:
        warnings.append("Missing skills the job asks for (not added to your resume): " + ", ".join(missing[:8]))
    quality["warnings"] = warnings

    title = f"Tailored Resume - {job.company} - {job.title}"
    return ResumeResult(
        content=content,
        markdown=_to_markdown(content),
        plain_text=_to_plain_text(content),
        quality=quality,
        warnings=warnings,
        unsupported_claims_removed=removed,
        model_used=model_used,
        ai_used=ai_used,
        title=title,
    )


def _structured_content(working: dict[str, Any], job: JobPosting, job_skills: set[str]) -> dict[str, Any]:
    """Convert the guardrailed working dict into the response content schema."""
    header = working.get("header") or {}
    return {
        "header": {
            "full_name": header.get("name") or header.get("full_name") or "",
            "email": header.get("email") or "",
            "phone": header.get("phone") or "",
            "location": header.get("location") or "",
            "links": [str(u) for u in (header.get("links") or []) if u],
        },
        "summary": working.get("summary") or "",
        "skills": group_skills(working.get("skills") or [], job_skills),
        "experience": [
            {
                "title": item.get("title") or "",
                "company": item.get("company") or "",
                "location": item.get("location") or "",
                "dates": _dates(item.get("start_date"), item.get("end_date")),
                "bullets": [str(b) for b in (item.get("bullets") or []) if str(b).strip()],
            }
            for item in working.get("experience") or []
        ],
        "projects": [
            {
                "name": item.get("name") or "",
                "technologies": [str(t) for t in (item.get("technologies") or []) if str(t).strip()],
                "bullets": [str(b) for b in (item.get("bullets") or []) if str(b).strip()],
            }
            for item in working.get("projects") or []
        ],
        "education": [_education_entry(item) for item in working.get("education") or []],
        "awards": [_award_entry(item) for item in working.get("awards") or []],
        "certifications": [_award_entry(item) for item in working.get("certifications") or []],
    }


def _education_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"school": str(item), "degree": "", "dates": "", "details": ""}
    degree = ", ".join(filter(None, [item.get("degree"), item.get("major") and f"in {item.get('major')}"]))
    details = []
    if item.get("gpa"):
        details.append(f"GPA {item['gpa']}")
    if item.get("honors"):
        details.append(", ".join(str(h) for h in item["honors"]))
    return {
        "school": item.get("school") or "",
        "degree": degree or (item.get("degree") or ""),
        "dates": _dates(item.get("start_date"), item.get("end_date")),
        "details": "; ".join(details),
    }


def _award_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"name": str(item), "issuer": "", "date": ""}
    return {"name": item.get("name") or "", "issuer": item.get("issuer") or "", "date": str(item.get("date") or item.get("issue_date") or "")}


def _dates(start: Any, end: Any) -> str:
    start_s, end_s = _date_str(start), _date_str(end)
    if start_s and end_s:
        return f"{start_s} – {end_s}"
    if start_s and not end_s:
        return f"{start_s} – Present"
    return end_s or start_s or ""


def _quality(content: dict, removed: list[str], missing: list[str], job: JobPosting, job_skills: set[str]) -> dict[str, Any]:
    # Tailored if any of the user's real skills overlap the job's skills.
    resume_skills = {i.lower() for g in content["skills"] for i in g["items"]}
    tailored = bool(job_skills & resume_skills) or bool(content["summary"] and job.title.split()[0].lower() in content["summary"].lower())
    return {
        "ats_friendly": True,  # single-column, standard sections, no tables/graphics/icons
        "job_tailored": bool(tailored),
        "unsupported_claims_removed": removed,
        "missing_job_skills_not_claimed": missing,
        "estimated_page_count": _estimate_pages(content),
        "warnings": [],
    }


def _estimate_pages(content: dict) -> int:
    lines = 6  # header + summary
    lines += 1 + sum(1 + len(e.get("bullets") or []) for e in content["experience"])
    lines += 1 + sum(1 + len(p.get("bullets") or []) for p in content["projects"])
    lines += len(content["education"]) + len(content["awards"])
    return max(1, (lines // 45) + 1) if lines > 45 else 1


def _build_resume(payload: dict, job: JobPosting, facts: ProfileFacts, job_skills: set[str]) -> dict[str, Any]:
    profile = payload.get("profile") or {}

    # Skills: job-relevant first, then the rest of the user's real skills.
    all_skills = [str(s) for s in (profile.get("skills") or [])]
    relevant = [s for s in all_skills if s.lower() in job_skills]
    others = [s for s in all_skills if s.lower() not in job_skills]
    ordered_skills = relevant + others

    experience = [
        {
            "company": item.get("company"),
            "title": item.get("title"),
            "location": item.get("location"),
            "start_date": _date_str(item.get("start_date")),
            "end_date": _date_str(item.get("end_date")),
            "bullets": list(item.get("bullets") or []),
            "technologies": list(item.get("technologies") or []),
        }
        for item in payload.get("experience", [])
    ]

    # Projects reordered by relevance to the job (count of job-skill overlaps).
    projects = sorted(
        (
            {
                "name": item.get("name"),
                "subtitle": item.get("description"),
                "bullets": list(item.get("bullets") or ([item["description"]] if item.get("description") else [])),
                "technologies": list(item.get("technologies") or []),
            }
            for item in payload.get("projects", [])
        ),
        key=lambda p: _relevance(p, job_skills),
        reverse=True,
    )

    header = {
        "name": profile.get("full_name", ""),
        "email": profile.get("email", "") or "",
        "phone": profile.get("phone", "") or "",
        "location": ", ".join(
            filter(None, [profile.get("location_city"), profile.get("location_state"), profile.get("location_country")])
        ),
        "links": [u for u in [profile.get("linkedin_url"), profile.get("github_url"), profile.get("portfolio_url")] if u],
        "work_authorization": profile.get("work_authorization") or "",
    }

    return {
        "header": header,
        "summary": _default_summary(profile, job, relevant),
        "skills": ordered_skills,
        "experience": experience,
        "projects": projects,
        "education": payload.get("education", []),
        "awards": payload.get("awards", []),
        "certifications": payload.get("certifications", []),
    }


def _default_summary(profile: dict, job: JobPosting, relevant_skills: list[str]) -> str:
    strengths = ", ".join(relevant_skills[:4]) if relevant_skills else ", ".join(str(s) for s in (profile.get("skills") or [])[:4])
    name = profile.get("full_name") or "Candidate"
    if strengths:
        return f"{name} targeting {job.title} roles, with hands-on experience in {strengths}."
    return f"{name} targeting {job.title} roles with relevant project and engineering experience."


async def _ai_resume(db: Session, payload: dict, job: JobPosting, user_id: int):
    """Call OpenAI to tailor the resume. Returns the provider AIResult."""
    from app.ai.provider import ai_provider

    match = db.scalar(select(JobMatch).where((JobMatch.user_id == user_id) & (JobMatch.job_id == job.id)))
    context = {
        "profile": payload.get("profile"),
        "experience": payload.get("experience"),
        "projects": payload.get("projects"),
        "education": payload.get("education"),
        "awards": payload.get("awards"),
        "job": {
            "title": job.title,
            "company": job.company,
            "description": (job.description_clean or "")[:4000],
            "required_skills": job.required_skills or [],
        },
        "match_reasons": (match.strengths if match else []) or [],
        "missing_skills": (match.gaps if match else []) or [],
        "recommended_resume_angle": (match.recommended_resume_angle if match else "") or "",
    }
    return await ai_provider.json_task("tailor_resume.md", context, smart=True)


def _merge_ai_resume(base: dict[str, Any], data: dict[str, Any], facts: ProfileFacts) -> dict[str, Any]:
    """Apply AI text (summary, skill order, bullet phrasing) onto the truthful
    deterministic base. Entities (companies/titles/dates/schools) always come
    from the base; the guardrail then removes any unsupported claim."""
    if not isinstance(data, dict):
        return base
    merged = dict(base)

    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        merged["summary"] = summary.strip()

    # Reorder base skills using the AI ordering, but never add skills the profile
    # does not have (unknown AI skills are dropped).
    ai_skills = [str(s) for s in (data.get("skills") or []) if isinstance(s, str)]
    if ai_skills:
        base_lower = {s.lower(): s for s in base.get("skills", [])}
        ordered = [base_lower[s.lower()] for s in ai_skills if s.lower() in base_lower]
        ordered += [s for s in base.get("skills", []) if s not in ordered]
        merged["skills"] = ordered

    merged["experience"] = _merge_bullets(base.get("experience", []), data.get("experience"), key="company")
    merged["projects"] = _merge_bullets(base.get("projects", []), data.get("projects"), key="name")
    return merged


def _merge_bullets(base_entries: list[dict], ai_entries: Any, *, key: str) -> list[dict]:
    if not isinstance(ai_entries, list):
        return base_entries
    ai_by_name = {
        str(entry.get(key, "")).lower(): entry
        for entry in ai_entries
        if isinstance(entry, dict) and entry.get(key)
    }
    merged = []
    for entry in base_entries:
        ai_entry = ai_by_name.get(str(entry.get(key, "")).lower())
        new_entry = dict(entry)
        if ai_entry and isinstance(ai_entry.get("bullets"), list):
            bullets = [str(b).strip() for b in ai_entry["bullets"] if str(b).strip()]
            if bullets:
                new_entry["bullets"] = bullets  # guardrail removes any invented metric
        merged.append(new_entry)
    return merged


def _relevance(project: dict, job_skills: set[str]) -> int:
    tech = _lower_set(project.get("technologies") or [])
    text = " ".join(str(b) for b in project.get("bullets") or []).lower()
    return len(tech & job_skills) + sum(1 for s in job_skills if s in text)


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in values if str(v).strip()}


_KEYWORD_STOPWORDS = {"and", "the", "for", "with", "engineer", "developer", "intern", "senior", "junior", "staff"}


def _job_keywords(job: JobPosting, job_skills: set[str]) -> set[str]:
    """Broader relevance vocabulary for ranking projects: declared job skills
    plus meaningful tokens from the job title (role family, e.g. "backend",
    "ml", "data"). Kept lowercase to match the compaction service."""
    keywords = set(job_skills)
    for token in re.findall(r"[a-z0-9+#]+", (job.title or "").lower()):
        if len(token) > 2 and token not in _KEYWORD_STOPWORDS:
            keywords.add(token)
    return keywords


def _date_str(value: Any) -> str:
    return str(value) if value else ""


def _skills_line(content: dict[str, Any]) -> str:
    return " | ".join(
        (f"{g['category']}: " if g.get("category") else "") + ", ".join(g.get("items") or [])
        for g in content.get("skills") or []
        if g.get("items")
    )


def _to_markdown(content: dict[str, Any]) -> str:
    """Markdown (used for DOCX/PDF fallback and download). Operates on the
    structured content so it stays in sync with the preview."""
    header = content.get("header") or {}
    lines: list[str] = []
    if header.get("full_name"):
        lines.append(f"# {header['full_name']}")
    contact = " | ".join(
        filter(None, [header.get("email"), header.get("phone"), header.get("location"), *(header.get("links") or [])])
    )
    if contact:
        lines.append(contact)
    if content.get("summary"):
        lines += ["", "## Professional Summary", content["summary"]]
    if _skills_line(content):
        lines += ["", "## Core Skills", _skills_line(content)]
    if content.get("experience"):
        lines += ["", "## Professional Experience"]
        for exp in content["experience"]:
            head = " — ".join(filter(None, [exp.get("title"), exp.get("company")]))
            meta = " | ".join(filter(None, [exp.get("location"), exp.get("dates")]))
            lines.append(f"**{head}**" + (f" ({meta})" if meta else ""))
            for bullet in exp.get("bullets") or []:
                lines.append(f"- {bullet}")
    if content.get("projects"):
        lines += ["", "## Selected Projects"]
        for proj in content["projects"]:
            tech = ", ".join(proj.get("technologies") or [])
            lines.append(f"**{proj.get('name')}**" + (f" ({tech})" if tech else ""))
            for bullet in proj.get("bullets") or []:
                lines.append(f"- {bullet}")
    if content.get("education"):
        lines += ["", "## Education"]
        for edu in content["education"]:
            parts = filter(None, [edu.get("school"), edu.get("degree"), edu.get("dates"), edu.get("details")])
            lines.append(f"- {', '.join(parts)}")
    awards = (content.get("awards") or []) + (content.get("certifications") or [])
    if awards:
        lines += ["", "## Awards & Certifications"]
        for award in awards:
            name = award.get("name") if isinstance(award, dict) else str(award)
            if name:
                lines.append(f"- {name}")
    return "\n".join(lines).strip()


def _to_plain_text(content: dict[str, Any]) -> str:
    """Clean plain text with no markdown symbols (used for copy + .txt export)."""
    header = content.get("header") or {}
    lines: list[str] = []
    if header.get("full_name"):
        lines.append(header["full_name"])
    contact = " | ".join(
        filter(None, [header.get("email"), header.get("phone"), header.get("location"), *(header.get("links") or [])])
    )
    if contact:
        lines.append(contact)

    def section(title: str) -> None:
        lines.extend(["", title.upper()])

    if content.get("summary"):
        section("Professional Summary")
        lines.append(content["summary"])
    if _skills_line(content):
        section("Core Skills")
        lines.append(_skills_line(content))
    if content.get("experience"):
        section("Professional Experience")
        for exp in content["experience"]:
            head = " — ".join(filter(None, [exp.get("title"), exp.get("company")]))
            meta = " | ".join(filter(None, [exp.get("location"), exp.get("dates")]))
            lines.append(head + (f"  ({meta})" if meta else ""))
            for bullet in exp.get("bullets") or []:
                lines.append(f"  • {bullet}")
    if content.get("projects"):
        section("Selected Projects")
        for proj in content["projects"]:
            tech = ", ".join(proj.get("technologies") or [])
            lines.append(proj.get("name", "") + (f"  ({tech})" if tech else ""))
            for bullet in proj.get("bullets") or []:
                lines.append(f"  • {bullet}")
    if content.get("education"):
        section("Education")
        for edu in content["education"]:
            lines.append(", ".join(filter(None, [edu.get("school"), edu.get("degree"), edu.get("dates"), edu.get("details")])))
    awards = (content.get("awards") or []) + (content.get("certifications") or [])
    if awards:
        section("Awards & Certifications")
        for award in awards:
            name = award.get("name") if isinstance(award, dict) else str(award)
            if name:
                lines.append(f"  • {name}")
    return "\n".join(lines).strip()
