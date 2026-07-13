"""Normalization glue between the parsers and the import draft schema.

``normalize_import_draft`` accepts whatever the AI provider returned. If the AI
is unavailable (or returned no usable structure) it falls back to the
deterministic :mod:`resume_parser_service`. Either way it strips sensitive
fields, flattens grouped skills, and runs :mod:`import_validation_service` so the
draft carries honest warnings and confidence scores.
"""

from typing import Any

from app.schemas.import_profile import ImportProfileDraft
from app.services.import_validation_service import validate_and_score
from app.services.resume_parser_service import parse_resume_text

SENSITIVE_KEYS = {
    "gender",
    "ethnicity",
    "veteran_status",
    "disability_status",
    "hispanic_latino_status",
}

AI_UNAVAILABLE_WARNING = (
    "AI parsing is unavailable. We extracted basic fields using a simple parser."
)


def normalize_import_draft(
    data: dict[str, Any],
    raw_text: str,
    source_type: str,
    model_used: str,
) -> ImportProfileDraft:
    ai_available = model_used != "deterministic-local" and not _looks_like_fallback(data)
    if not ai_available:
        # No usable AI output: parse deterministically from the extracted text.
        data = parse_resume_text(raw_text)

    clean_sensitive_fields(data)
    _flatten_skill_groups(data)

    warnings, confidence = validate_and_score(data)
    combined_warnings = unique_strings([*data.get("confidence_warnings", []), *warnings])
    if not ai_available:
        combined_warnings = unique_strings([*combined_warnings, AI_UNAVAILABLE_WARNING])
    data["confidence_warnings"] = combined_warnings
    data["confidence"] = confidence

    data.setdefault("raw_text_preview", raw_text[:500])
    data.setdefault("source_type", source_type)
    data.setdefault("missing_fields", [])
    data.setdefault("low_confidence_fields", [])

    draft = ImportProfileDraft.model_validate(data)
    if not draft.raw_text_preview:
        draft.raw_text_preview = raw_text[:500]
    draft.source_type = source_type  # type: ignore[assignment]
    draft.low_confidence_fields = sorted(
        set([*draft.low_confidence_fields, *draft.confidence_warnings])
    )
    return draft


def _looks_like_fallback(data: dict[str, Any]) -> bool:
    """The AI provider's local fallback returns only ``{"summary": ...}``."""
    if not data:
        return True
    return set(data.keys()) <= {"summary"}


def _flatten_skill_groups(data: dict[str, Any]) -> None:
    """Ensure a flat ``skills`` list exists even when only groups were returned."""
    groups = data.get("skill_groups") or []
    if not data.get("skills") and groups:
        flat: list[str] = []
        for group in groups:
            if isinstance(group, dict):
                flat.extend(group.get("items") or [])
        data["skills"] = unique_strings(flat)


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def clean_sensitive_fields(data: Any) -> None:
    if isinstance(data, dict):
        for key in list(data):
            if key in SENSITIVE_KEYS:
                data.pop(key, None)
            else:
                clean_sensitive_fields(data[key])
    elif isinstance(data, list):
        for item in data:
            clean_sensitive_fields(item)
