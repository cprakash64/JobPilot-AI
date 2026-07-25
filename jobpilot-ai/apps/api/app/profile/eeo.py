"""Canonical EEO/demographic vocabulary.

The bug this replaces: one shared option list — "Prefer not to answer / Yes /
No / Another option" — was reused for five questions that mean entirely
different things, so a *gender* could be stored as "yes" and a race/ethnicity as
"another_option". Neither string carries any demographic meaning.

Rules encoded here:

* Every question has its OWN closed vocabulary.
* Every question includes ``prefer_not_to_answer``.
* Nothing is ever inferred. A value that is not in the vocabulary is invalid —
  it is never coerced onto the nearest-looking canonical value.
* Race/ethnicity is a LIST: people identify with more than one category, and
  collapsing that loses information. ``prefer_not_to_answer`` is exclusive.
* "Gender identity" is deliberately not called "sex". They are different
  questions, and the backend models neither as the other.

This data is voluntary, is stored only with explicit consent, and is never used
for scoring, ranking, or document generation.
"""

from __future__ import annotations

PREFER_NOT = "prefer_not_to_answer"

# --------------------------------------------------------------------------- #
# Gender identity (NOT sex)
# --------------------------------------------------------------------------- #
GENDER_IDENTITY: dict[str, str] = {
    "woman": "Woman",
    "man": "Man",
    "non_binary": "Non-binary",
    "self_describe": "Self-describe",
    PREFER_NOT: "Prefer not to answer",
}

# --------------------------------------------------------------------------- #
# Veteran status — "not a protected veteran" and "not a veteran" are different
# answers and must stay distinguishable.
# --------------------------------------------------------------------------- #
VETERAN_STATUS: dict[str, str] = {
    "protected_veteran": "I am a protected veteran",
    "not_protected_veteran": "I am not a protected veteran",
    "not_a_veteran": "I am not a veteran",
    PREFER_NOT: "Prefer not to answer",
}

DISABILITY_STATUS: dict[str, str] = {
    "yes": "Yes, I have a disability or have had one in the past",
    "no": "No, I do not have a disability and have not had one in the past",
    PREFER_NOT: "Prefer not to answer",
}

HISPANIC_OR_LATINO: dict[str, str] = {
    "yes": "Yes",
    "no": "No",
    PREFER_NOT: "Prefer not to answer",
}

RACE_ETHNICITY: dict[str, str] = {
    "american_indian_or_alaska_native": "American Indian or Alaska Native",
    "asian": "Asian",
    "black_or_african_american": "Black or African American",
    "native_hawaiian_or_other_pacific_islander": "Native Hawaiian or Other Pacific Islander",
    "white": "White",
    "another_race_or_ethnicity": "Another race or ethnicity",
    PREFER_NOT: "Prefer not to answer",
}

FIELD_VOCABULARIES: dict[str, dict[str, str]] = {
    "gender_identity": GENDER_IDENTITY,
    "veteran_status": VETERAN_STATUS,
    "disability_status": DISABILITY_STATUS,
    "hispanic_or_latino": HISPANIC_OR_LATINO,
    "race_ethnicity": RACE_ETHNICITY,
}

# Fields whose stored answer is a list rather than a single value.
MULTI_VALUE_FIELDS: frozenset[str] = frozenset({"race_ethnicity"})

# Free-text companions, only meaningful when their trigger option is selected.
SELF_DESCRIPTION_FIELDS: dict[str, tuple[str, str]] = {
    # self_description_column: (source_field, option that enables it)
    "gender_self_description": ("gender_identity", "self_describe"),
    "race_self_description": ("race_ethnicity", "another_race_or_ethnicity"),
}


class InvalidDemographicValue(ValueError):
    """A value outside the field's closed vocabulary. Never auto-corrected."""


def is_valid(field: str, value: str | None) -> bool:
    vocabulary = FIELD_VOCABULARIES.get(field)
    if vocabulary is None:
        return False
    return value in vocabulary


def validate_single(field: str, value: str | None) -> str | None:
    """Validate a single-value answer. ``None``/blank means "not answered"."""
    if value is None or value == "":
        return None
    if field in MULTI_VALUE_FIELDS:
        raise InvalidDemographicValue(f"{field} expects a list of values")
    if not is_valid(field, value):
        # Deliberately does NOT name the offending value: it is sensitive.
        raise InvalidDemographicValue(f"{field} received a value outside its allowed options")
    return value


def validate_multi(field: str, values: list[str] | None) -> list[str] | None:
    """Validate a multi-select answer (race/ethnicity).

    ``prefer_not_to_answer`` is mutually exclusive: combining it with a real
    category is contradictory, so it is rejected rather than silently resolved.
    """
    if values is None:
        return None
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    if field not in MULTI_VALUE_FIELDS:
        raise InvalidDemographicValue(f"{field} expects a single value")

    unknown = [v for v in cleaned if not is_valid(field, v)]
    if unknown:
        raise InvalidDemographicValue(
            f"{field} received {len(unknown)} value(s) outside its options"
        )

    deduped = list(dict.fromkeys(cleaned))
    if PREFER_NOT in deduped and len(deduped) > 1:
        raise InvalidDemographicValue(
            "'Prefer not to answer' cannot be combined with other race/ethnicity selections"
        )
    return deduped


def display_label(field: str, value: str) -> str:
    """Human-readable label for a canonical value (UI and ATS matching)."""
    return FIELD_VOCABULARIES.get(field, {}).get(value, value)


# --------------------------------------------------------------------------- #
# Legacy cleanup (migration 0015)
# --------------------------------------------------------------------------- #
# Values that were already meaningful under the old shared option list and can
# be carried across unchanged. Everything else is quarantined — "yes" is not a
# gender, and "another_option" is not a race.
VALID_LEGACY: dict[str, set[str]] = {
    "gender": {PREFER_NOT},
    "veteran_status": {PREFER_NOT},
    "disability_status": {PREFER_NOT},
    "hispanic_latino_status": {"yes", "no", PREFER_NOT},
    "ethnicity": {PREFER_NOT},
}


def normalize_legacy(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_")


def classify_legacy_row(row: dict, *, consented: bool) -> dict:
    """Decide what happens to one pre-0015 demographics row.

    Returns ``{"action": "delete"}`` when the data was retained without
    consent, or ``{"action": "quarantine", ...}`` with the values that may be
    carried across plus whether the user must be asked to re-answer.

    Nothing is ever reinterpreted: a legacy value survives only if it is
    already a valid canonical answer for that question.
    """
    normalized = {field: normalize_legacy(row.get(field)) for field in VALID_LEGACY}
    carried = {
        field: (value if value in VALID_LEGACY[field] else None)
        for field, value in normalized.items()
    }

    had_value = any(normalized.values())
    dropped = any(normalized[field] and carried[field] is None for field in normalized)

    if not consented:
        # This data should not have been retained at all without consent.
        return {"action": "delete"}

    return {
        "action": "quarantine",
        "gender_identity": carried["gender"],
        "veteran_status": carried["veteran_status"],
        "disability_status": carried["disability_status"],
        "hispanic_or_latino": carried["hispanic_latino_status"],
        # Race never had a usable vocabulary, so only an explicit decline
        # carries across; "another_option" is NOT a race.
        "race_ethnicity": [PREFER_NOT] if carried["ethnicity"] == PREFER_NOT else None,
        "needs_review": bool(had_value and dropped),
    }


def has_any_answer(values: dict) -> bool:
    """True when the payload carries at least one actual demographic answer.

    Used to enforce "saving non-empty EEO data requires explicit consent" —
    consent is about STORING data, so an all-empty payload is not a violation.
    """
    for field in FIELD_VOCABULARIES:
        value = values.get(field)
        if isinstance(value, list):
            if [v for v in value if v]:
                return True
        elif value:
            return True
    for column in SELF_DESCRIPTION_FIELDS:
        if (values.get(column) or "").strip():
            return True
    return False
