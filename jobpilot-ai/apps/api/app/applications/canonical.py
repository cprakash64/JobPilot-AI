"""Canonical answer keys and the sensitive-field policy.

The extension and backend share this vocabulary. Sensitive / consequential
answers are never auto-filled, never inferred, and never sent to an LLM unless
the user explicitly asks — the user answers those directly on the employer page.
"""

from __future__ import annotations

# Canonical keys for reusable, non-sensitive application answers.
CANONICAL_KEYS: tuple[str, ...] = (
    "first_name",
    "middle_name",
    "last_name",
    "full_name",
    "preferred_name",
    "preferred_first_name",
    "preferred_last_name",
    "pronouns",
    "email",
    # Phone is published in several shapes so a form that splits country from
    # national number can be filled without re-concatenating (see
    # app.profile.phone): "phone" is E.164, the others are the split parts.
    "phone",
    "phone_country",
    "phone_country_iso2",
    "phone_national",
    "address",
    "city",
    "state",
    "postal_code",
    "country",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "current_company",
    "current_title",
    "work_authorization_us",
    "sponsorship_required_now",
    "sponsorship_required_future",
    "willing_to_relocate",
    "relocation_locations",
    "preferred_workplace",
    "salary_expectation",
    "available_start_date",
    "years_of_experience",
    "education",
    "employment_history",
    "skills",
    # Company-scoped by default — never reused across employers (see
    # ``default_scope_for_key``).
    "referral_source",
    "previously_employed",
    "relatives_employed",
    "previously_interviewed",
)

# Canonical keys whose saved answers default to "company" scope: reusable at
# THIS employer only, never auto-applied to a different one. Everything else
# defaults to "global" scope (reusable everywhere once confirmed).
COMPANY_SCOPED_KEYS: frozenset[str] = frozenset(
    {
        "referral_source",
        "previously_employed",
        "relatives_employed",
        "previously_interviewed",
    }
)

VALID_SCOPES: frozenset[str] = frozenset({"global", "company", "application", "sensitive"})

# Sensitive / consequential canonical keys. These are NEVER auto-filled, guessed,
# inferred, or generated. They are surfaced to the user as "review required" so
# the user answers them directly on the employer form.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "race",
        "ethnicity",
        "gender",
        "disability_status",
        "veteran_status",
        "sexual_orientation",
        "religion",
        "criminal_history",
        "legal_attestation",
        "security_clearance",
        "export_control",
        "salary_history",
        "government_demographic",
    }
)

# Human-readable reason shown in the review panel for a sensitive field.
SENSITIVE_CATEGORY_LABEL: dict[str, str] = {
    "race": "Demographic (voluntary EEO)",
    "ethnicity": "Demographic (voluntary EEO)",
    "gender": "Demographic (voluntary EEO)",
    "disability_status": "Disability (voluntary)",
    "veteran_status": "Veteran status (voluntary)",
    "sexual_orientation": "Demographic (voluntary)",
    "religion": "Demographic (voluntary)",
    "criminal_history": "Criminal-history disclosure",
    "legal_attestation": "Legal attestation",
    "security_clearance": "Security-clearance claim",
    "export_control": "Export-control eligibility",
    "salary_history": "Salary history",
    "government_demographic": "Government demographic question",
}

# Keys whose answers, while auto-fillable, are consequential enough to always
# require the user to have verified them (work authorization, sponsorship, etc.).
VERIFICATION_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "work_authorization_us",
        "sponsorship_required_now",
        "sponsorship_required_future",
    }
)


def is_sensitive_key(canonical_key: str) -> bool:
    return canonical_key in SENSITIVE_KEYS


def is_verification_required(canonical_key: str) -> bool:
    return canonical_key in VERIFICATION_REQUIRED_KEYS


def default_scope_for_key(canonical_key: str) -> str:
    if is_sensitive_key(canonical_key):
        return "sensitive"
    if canonical_key in COMPANY_SCOPED_KEYS:
        return "company"
    return "global"


def normalize_company_key(company: str | None) -> str:
    """Normalize an employer name for scope matching (case/whitespace only —
    never used for anything but comparing "same employer or not")."""
    return (company or "").strip().lower()


def sensitive_reason(canonical_key: str) -> str:
    return SENSITIVE_CATEGORY_LABEL.get(canonical_key, "Sensitive question — please answer directly")
