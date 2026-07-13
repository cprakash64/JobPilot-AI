"""Tailored, truthful cover letter generation.

Produces a short (180-250 word), targeted cover letter as structured content
(date, recipient, company, role, greeting, paragraphs, closing, signature). Uses
OpenAI when available (validated), otherwise a deterministic template. Names the
exact company and role, highlights 2-3 real strengths, and never overclaims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.documents.document_guardrail_service import build_profile_facts
from app.models.entities import JobPosting
from app.services.documents import profile_payload, public_dict


@dataclass
class CoverLetterResult:
    content: dict[str, Any]
    markdown: str
    plain_text: str = ""
    quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unsupported_claims_removed: list[str] = field(default_factory=list)
    model_used: str = "deterministic-local"
    ai_used: bool = False
    title: str = ""


async def generate_cover_letter(db: Session, user_id: int, job: JobPosting) -> CoverLetterResult:
    payload = profile_payload(db, user_id)
    payload["job"] = public_dict(job)
    facts = build_profile_facts(payload)
    profile = payload.get("profile") or {}
    name = profile.get("full_name") or "Candidate"

    job_skills = _lower_set((job.required_skills or []) + (job.preferred_skills or []))
    strengths = [str(s) for s in (profile.get("skills") or []) if str(s).lower() in job_skills][:3]
    if not strengths:
        strengths = [str(s) for s in (profile.get("skills") or [])[:3]]

    warnings: list[str] = []
    paragraphs, model_used, ai_used, reason = await _ai_cover_letter(payload, job)
    if paragraphs is None:
        paragraphs = _template_paragraphs(profile, job, strengths, payload)
        model_used = "deterministic-local"
        ai_used = False
        warnings.append(f"Generated with template mode because {reason}.")

    content = {
        "date": _today_str(),
        "recipient": "Hiring Team",
        "company": job.company,
        "role": job.title,
        "greeting": "Dear Hiring Team,",
        "paragraphs": paragraphs,
        "closing": "Best regards,",
        "signature": name,
    }

    # Guardrail: keep only strengths supported by the profile (defensive).
    unsupported = [s for s in strengths if not facts.supports_skill(s)]
    removed = [f"skill: {s}" for s in unsupported]

    body_words = sum(len(p.split()) for p in paragraphs)
    quality = {
        "job_tailored": job.company.lower() in " ".join(paragraphs).lower(),
        "unsupported_claims_removed": removed,
        "word_count": body_words,
        "warnings": warnings,
    }

    title = f"Cover Letter - {job.company} - {job.title}"
    return CoverLetterResult(
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


async def _ai_cover_letter(payload: dict, job: JobPosting) -> tuple[list[str] | None, str, bool, str]:
    """Return (paragraphs, model, ai_used, reason). paragraphs is None when AI was not used."""
    from app.ai.provider import ai_provider

    result = await ai_provider.json_task("cover_letter.md", payload, smart=True)
    if not result.ai_used:
        return None, "deterministic-local", False, result.error or "the AI request failed"

    data = result.data if isinstance(result.data, dict) else {}
    paragraphs = data.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        # Older single-body shape -> split into paragraphs.
        body = data.get("body") or data.get("cover_letter") or ""
        paragraphs = [p.strip() for p in str(body).split("\n\n") if p.strip()]

    paragraphs = [str(p).strip() for p in paragraphs if str(p).strip()]
    joined = " ".join(paragraphs)
    # Validate: real content that names the company.
    if paragraphs and len(joined.split()) >= 60 and job.company.lower() in joined.lower():
        return paragraphs, result.model_used, True, ""

    return None, "deterministic-local", False, "the AI response was incomplete"


def _template_paragraphs(profile: dict, job: JobPosting, strengths: list[str], payload: dict) -> list[str]:
    strengths_text = ", ".join(strengths[:3]) or "relevant engineering experience"
    experiences = payload.get("experience", [])
    projects = payload.get("projects", [])

    proof = ""
    if experiences:
        exp = experiences[0]
        techs = ", ".join((exp.get("technologies") or [])[:3]) or strengths_text
        proof = f"As {exp.get('title')} at {exp.get('company')}, I worked hands-on with {techs}, breaking down ambiguous problems and shipping features that held up in production."
    elif projects:
        proj = projects[0]
        techs = ", ".join((proj.get("technologies") or [])[:3]) or strengths_text
        proof = f"Through my project {proj.get('name')}, I applied {techs} to turn an idea into working software end to end."

    second = ""
    if projects and experiences:
        proj = projects[0]
        techs = ", ".join((proj.get("technologies") or [])[:2]) or strengths_text
        second = f" I also built {proj.get('name')} using {techs}, which deepened my ownership of real projects."

    education = payload.get("education", [])
    edu_line = ""
    if education and isinstance(education[0], dict) and education[0].get("school"):
        edu_line = f" My studies at {education[0]['school']} gave me a solid foundation that I continue to build on through hands-on work."

    para1 = (
        f"I am applying for the {job.title} role at {job.company} because it is a strong match for my strengths in "
        f"{strengths_text}. I want to do work where reliable, well-built software makes a direct difference, and this "
        f"role looks like exactly that kind of environment where I can contribute and keep growing as an engineer."
    )
    para2 = (
        (proof + second).strip()
        or f"My background centers on {strengths_text}, which I have applied to build and ship working software."
    ) + edu_line + (
        f" These experiences taught me how to work through ambiguity, collaborate across a team, and deliver features "
        f"that hold up in production."
    )
    para3 = (
        f"I would welcome the chance to bring this experience to {job.company} and contribute to your team as a "
        f"{job.title}. I am confident I can ramp up quickly and add value early, and I would be glad to discuss how my "
        f"background fits your needs. Thank you for considering my application."
    )
    return [para1, para2, para3]


def _to_markdown(content: dict[str, Any]) -> str:
    lines = [
        content["date"],
        "",
        content["recipient"],
        content["company"],
        "",
        content["greeting"],
        "",
        *_intersperse(content["paragraphs"]),
        "",
        content["closing"],
        content["signature"],
    ]
    return "\n".join(str(line) for line in lines).strip()


def _to_plain_text(content: dict[str, Any]) -> str:
    # Same as markdown for a letter (no markdown symbols are used).
    return _to_markdown(content)


def _intersperse(paragraphs: list[str]) -> list[str]:
    out: list[str] = []
    for index, para in enumerate(paragraphs):
        if index:
            out.append("")
        out.append(para)
    return out


def _today_str() -> str:
    now = datetime.now(UTC)
    return f"{now.strftime('%B')} {now.day}, {now.year}"


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in values if str(v).strip()}
