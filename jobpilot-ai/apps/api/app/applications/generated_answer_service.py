"""Generate editable, job-specific written application answers.

The extension remains the only component that touches employer-page controls.
This service prepares a grounded draft using the configured OpenAI GPT model;
the extension inserts it and marks it for review, and never submits it.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.provider import ai_provider


async def generate_written_application_answers(
    *,
    profile_payload: dict[str, Any],
    job_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    company = _text(job_payload.get("company")) or "the company"
    title = _text(job_payload.get("title")) or "this role"
    payload = {
        "questions": [
            {
                "canonical_key": "custom_motivation",
                "question": f"Why are you interested in {company}?",
            }
        ],
        "job": {
            "company": company,
            "title": title,
            "description": _text(
                job_payload.get("description_clean")
                or job_payload.get("description_raw")
            )[:7000],
            "responsibilities": _string_list(job_payload.get("responsibilities"))[:10],
            "required_skills": _string_list(job_payload.get("required_skills"))[:15],
            "preferred_skills": _string_list(job_payload.get("preferred_skills"))[:12],
        },
        # Send only career facts needed to ground the response. Contact details,
        # work authorization, demographics, and other unrelated PII stay out.
        "candidate": _career_facts(profile_payload),
        "constraints": {
            "word_count": "200-260",
            "tone": "natural, specific, concise, first-person",
            "must_be_editable": True,
            "do_not_invent": True,
        },
    }
    result = await ai_provider.json_task("application_answers.md", payload, smart=True)
    answer = _extract_answer(result.data, "custom_motivation")
    if not answer:
        answer = _local_motivation(payload)
    if not answer:
        return []
    return [
        {
            "canonical_key": "custom_motivation",
            "value": answer,
            "display_value": answer,
            "source": (
                f"openai:{result.model_used}"
                if result.ai_used
                else "grounded_template"
            ),
            "confidence": 0.9 if result.ai_used else 0.82,
            "sensitive": False,
            # It is intentionally inserted into the page but remains an
            # editable review item; generated prose is never auto-submitted.
            "requires_review": True,
            "verified": False,
        }
    ]


def _career_facts(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    return {
        "target_roles": _string_list(profile.get("target_roles"))[:8],
        "skills": _string_list(profile.get("skills"))[:24],
        "experience": [
            _only(item, "company", "title", "bullets", "technologies")
            for item in _dict_list(payload.get("experience"))[:5]
        ],
        "projects": [
            _only(item, "name", "bullets", "technologies")
            for item in _dict_list(payload.get("projects"))[:5]
        ],
        "education": [
            _only(item, "school", "degree", "major")
            for item in _dict_list(payload.get("education"))[:3]
        ],
    }


def _only(item: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {
        key: value
        for key in keys
        if (value := item.get(key)) not in (None, "", [])
    }


def _extract_answer(data: dict[str, Any], key: str) -> str:
    answers = data.get("answers")
    value: Any = None
    if isinstance(answers, dict):
        value = answers.get(key)
        if isinstance(value, dict):
            value = value.get("answer") or value.get("value")
    elif isinstance(answers, list):
        for item in answers:
            if not isinstance(item, dict):
                continue
            if item.get("canonical_key") == key:
                value = item.get("answer") or item.get("value")
                break
    return _clean_generated_text(value)


def _clean_generated_text(value: Any) -> str:
    text = _text(value)
    text = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:answer|response)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:3000].strip()


def _local_motivation(payload: dict[str, Any]) -> str:
    """Grounded fallback for development or a temporary provider outage."""
    job = payload.get("job") or {}
    candidate = payload.get("candidate") or {}
    company = _text(job.get("company")) or "the company"
    title = _text(job.get("title")) or "this role"
    skills = _string_list(candidate.get("skills"))
    relevant = skills[:3]
    skill_phrase = ", ".join(relevant) if relevant else "building practical software"
    return (
        f"I’m interested in the {title} opportunity at {company} because it "
        f"connects directly with the work I enjoy most: {skill_phrase}. "
        "I like roles where I can turn a real operational need into dependable "
        "software, learn from the people using it, and keep improving the result. "
        f"The scope of this position feels like a strong match for that approach, "
        f"and I’d be excited to bring my experience to the {company} team."
    )


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _text(value: Any) -> str:
    return str(value or "").strip()
