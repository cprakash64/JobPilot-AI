from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.models.entities import (
    Award,
    Certification,
    DocumentFormat,
    DocumentType,
    Education,
    Experience,
    GeneratedDocument,
    JobPosting,
    Project,
    UserProfile,
)


def public_dict(model: Any) -> dict[str, Any]:
    return {
        column.name: getattr(model, column.name)
        for column in model.__table__.columns
        if column.name not in {"hashed_password"}
    }


def profile_payload(db: Session, user_id: int) -> dict[str, Any]:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    return {
        "profile": public_dict(profile) if profile else {},
        "education": [public_dict(item) for item in db.scalars(select(Education).where(Education.user_id == user_id))],
        "experience": [public_dict(item) for item in db.scalars(select(Experience).where(Experience.user_id == user_id))],
        "projects": [public_dict(item) for item in db.scalars(select(Project).where(Project.user_id == user_id))],
        "certifications": [public_dict(item) for item in db.scalars(select(Certification).where(Certification.user_id == user_id))],
        "awards": [public_dict(item) for item in db.scalars(select(Award).where(Award.user_id == user_id))],
    }


def supported_terms(payload: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    profile = payload.get("profile") or {}
    for value in profile.get("skills") or []:
        terms.add(str(value).lower())
    for section in ["education", "experience", "projects", "certifications", "awards"]:
        for item in payload.get(section, []):
            for value in item.values():
                if isinstance(value, str):
                    terms.add(value.lower())
                elif isinstance(value, list):
                    terms.update(str(entry).lower() for entry in value)
    return terms


def hallucination_check(resume: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    supported = supported_terms(payload)
    unsupported_skills = [
        skill
        for skill in resume.get("skills", [])
        if str(skill).lower() not in supported
    ]
    return {"unsupported_claims": unsupported_skills, "passed": not unsupported_skills}


def build_truthful_resume(payload: dict[str, Any], job: JobPosting) -> dict[str, Any]:
    profile = payload.get("profile") or {}
    supported_skill_set = {str(skill) for skill in profile.get("skills") or []}
    job_skill_set = set((job.required_skills or []) + (job.preferred_skills or []))
    selected_skills = sorted(supported_skill_set & job_skill_set) or sorted(supported_skill_set)
    return {
        "header": {
            "name": profile.get("full_name", ""),
            "email": "",
            "location": ", ".join(
                filter(
                    None,
                    [
                        profile.get("location_city"),
                        profile.get("location_state"),
                        profile.get("location_country"),
                    ],
                )
            ),
            "links": [profile.get("linkedin_url"), profile.get("github_url"), profile.get("portfolio_url")],
        },
        "summary": f"Candidate targeting {job.title} roles with supported experience in {', '.join(selected_skills[:4])}.",
        "skills": selected_skills,
        "experience": [
            {
                "company": item.get("company"),
                "title": item.get("title"),
                "bullets": item.get("bullets") or [],
                "technologies": item.get("technologies") or [],
            }
            for item in payload.get("experience", [])
        ],
        "projects": [
            {
                "name": item.get("name"),
                "bullets": item.get("bullets") or [item.get("description")],
                "technologies": item.get("technologies") or [],
            }
            for item in payload.get("projects", [])
        ],
        "education": payload.get("education", []),
        "certifications": payload.get("certifications", []),
        "awards": payload.get("awards", []),
    }


def write_docx(content: dict[str, Any], path: Path) -> None:
    """Compatibility helper for callers that still pass legacy resume content."""
    from app.documents.store import _normalize_resume_content, _render_docx

    _render_docx(_normalize_resume_content(content), True, path)


def write_pdf(content: dict[str, Any], path: Path) -> None:
    """Compatibility helper that preserves formatting instead of dumping dicts."""
    from app.documents.store import _normalize_resume_content, _render_pdf

    _render_pdf(_normalize_resume_content(content), True, path)


async def generate_document(db: Session, user_id: int, job_id: int, doc_type: DocumentType) -> GeneratedDocument:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise ValueError("Job not found")
    payload = profile_payload(db, user_id)
    payload["job"] = public_dict(job)
    if doc_type == DocumentType.resume:
        content = build_truthful_resume(payload, job)
        check = hallucination_check(content, payload)
        content["hallucination_check"] = check
        model_used = "deterministic-local"
    elif doc_type == DocumentType.cover_letter:
        from app.ai.provider import ai_provider

        result = await ai_provider.json_task("cover_letter.md", payload, smart=True)
        content = result.data
        model_used = result.model_used
    else:
        from app.ai.provider import ai_provider

        result = await ai_provider.json_task("application_answers.md", payload, smart=True)
        content = result.data
        model_used = result.model_used
    record = GeneratedDocument(
        user_id=user_id,
        job_id=job_id,
        type=doc_type,
        format=DocumentFormat.json,
        content=content,
        model_used=model_used,
    )
    db.add(record)
    record_audit(db, user_id, "document_generated", {"job_id": job_id, "type": doc_type.value})
    db.commit()
    db.refresh(record)
    return record


def export_document_file(record: GeneratedDocument, fmt: DocumentFormat) -> str:
    """Compatibility wrapper around the canonical structured exporter.

    The old implementation here drew ``str(dict)`` values onto a canvas, which
    is the raw-text PDF users saw. Keeping one renderer guarantees that manual
    downloads, legacy API clients, and extension uploads receive the same
    formatted, ATS-safe document.
    """
    from app.documents.store import export_document

    return export_document(record, fmt)
