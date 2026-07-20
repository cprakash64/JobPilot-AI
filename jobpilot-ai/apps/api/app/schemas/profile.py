from __future__ import annotations

from datetime import date as Date
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_validator

PreferNot = "Prefer not to answer"

WORK_AUTHORIZATION_STATUSES = {
    "authorized_us",
    "authorized_other_country",
    "need_sponsorship_now",
    "need_sponsorship_future",
    "student_visa",
    "opt_cpt",
    "not_authorized",
    "prefer_not_to_say",
    "other",
}

SPONSORSHIP_SUGGESTED_STATUSES = {
    "need_sponsorship_now",
    "need_sponsorship_future",
    "student_visa",
    "opt_cpt",
}

WORK_PREFERENCES = {"everything", "remote", "hybrid", "onsite"}


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class UserProfileIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # NOTE: the structured name parts (first/middle/last, preferred first/last)
    # and name_confirmed are deliberately NOT part of this general "replace the
    # profile" model — PUT /profile is a full overwrite, and any caller that
    # doesn't know about structured-name confirmation (e.g. a plain
    # profile-details form) would otherwise reset name_confirmed to False on
    # every unrelated save. They are set only via the dedicated confirm-name
    # routes (ProfileNameIn, below) and are readable via UserProfileOut.
    #
    # ``phone`` here is the raw user input; the structured phone columns are
    # re-derived from it on save (see _phone_columns in routes/profile.py).
    full_name: str = ""
    preferred_name: str | None = None
    # The address used on applications; separate from the login identity.
    application_email: str | None = None
    phone: str | None = None
    location_city: str | None = None
    location_state: str | None = None
    location_postal_code: str | None = None
    location_country: str | None = None
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    work_authorization: str | None = Field(
        default=None,
        validation_alias=AliasChoices("work_authorization", "work_authorization_status"),
    )
    requires_sponsorship: bool = False
    open_to_relocation: bool = False
    target_roles: list[str] = Field(default_factory=list)
    target_levels: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: str | None = Field(
        default="everything",
        validation_alias=AliasChoices("remote_preference", "work_preference", "job_preference"),
    )
    skills: list[str] = Field(default_factory=list)

    @field_validator("application_email")
    @classmethod
    def validate_application_email(cls, value: str | None) -> str | None:
        """An application email must be able to receive employer messages.

        RFC-reserved example/test domains are rejected outright: an application
        carrying one looks complete but is undeliverable, which is worse than
        leaving the field blank.
        """
        from app.profile.emails import (
            RESERVED_EMAIL_MESSAGE,
            is_reserved_email_domain,
        )

        address = (value or "").strip()
        if not address:
            return None
        if "@" not in address or address.startswith("@") or address.endswith("@"):
            raise ValueError("Enter a valid email address.")
        if is_reserved_email_domain(address):
            raise ValueError(RESERVED_EMAIL_MESSAGE)
        return address

    @field_validator("target_roles", "target_levels", "preferred_locations", "skills", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        return _normalize_list(value)

    @field_validator("work_authorization")
    @classmethod
    def validate_work_authorization(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value not in WORK_AUTHORIZATION_STATUSES:
            raise ValueError("Unsupported work authorization status")
        return value

    @field_validator("remote_preference")
    @classmethod
    def validate_work_preference(cls, value: str | None) -> str:
        normalized = value or "everything"
        if normalized not in WORK_PREFERENCES:
            raise ValueError("Unsupported work preference")
        return normalized


class UserProfileOut(UserProfileIn):
    id: int
    user_id: int
    work_authorization_status: str | None = None
    work_preference: str = "everything"
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_first_name: str | None = None
    preferred_last_name: str | None = None
    name_confirmed: bool = False
    # Legacy aliases, still emitted so older clients keep reading a name.
    given_name: str | None = None
    family_name: str | None = None
    # Structured phone (see app.profile.phone); ``phone`` stays the raw value.
    phone_country_code: str | None = None
    phone_country_iso2: str | None = None
    phone_national_number: str | None = None
    phone_e164: str | None = None
    application_email_confirmed: bool = False
    # Where the application email actually came from, for the readiness UI.
    application_email_source: str | None = None


class ProfileNameIn(BaseModel):
    """Explicit structured-name confirmation. Never inferred/auto-applied —
    see confirm_name() in answer_vault_service.

    ``given_name``/``family_name`` remain accepted as aliases so extensions and
    clients built against the previous release keep working.
    """

    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(
        min_length=1, max_length=120,
        validation_alias=AliasChoices("first_name", "given_name"),
    )
    last_name: str = Field(
        min_length=1, max_length=120,
        validation_alias=AliasChoices("last_name", "family_name"),
    )
    middle_name: str | None = Field(default=None, max_length=120)
    preferred_first_name: str | None = Field(default=None, max_length=120)
    preferred_last_name: str | None = Field(default=None, max_length=120)


class SensitiveDemographicsIn(BaseModel):
    """Voluntary EEO answers.

    Every field defaults to None — "not answered" — never to a preselected
    value. Each is validated against its OWN closed vocabulary in
    app.profile.eeo; nothing is coerced onto a nearest-looking option.
    """

    gender_identity: str | None = None
    gender_self_description: str | None = Field(default=None, max_length=200)
    veteran_status: str | None = None
    disability_status: str | None = None
    hispanic_or_latino: str | None = None
    race_ethnicity: list[str] | None = None
    race_self_description: str | None = Field(default=None, max_length=200)
    consent_to_store: bool = False

    @field_validator("gender_identity", "veteran_status", "disability_status", "hispanic_or_latino")
    @classmethod
    def validate_single_choice(cls, value: str | None, info) -> str | None:
        from app.profile.eeo import InvalidDemographicValue, validate_single

        try:
            return validate_single(info.field_name, value)
        except InvalidDemographicValue as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("race_ethnicity")
    @classmethod
    def validate_race(cls, value: list[str] | None) -> list[str] | None:
        from app.profile.eeo import InvalidDemographicValue, validate_multi

        try:
            return validate_multi("race_ethnicity", value)
        except InvalidDemographicValue as exc:
            raise ValueError(str(exc)) from exc


class SensitiveDemographicsOut(SensitiveDemographicsIn):
    id: int
    user_id: int
    needs_review: bool = False


class EducationIn(BaseModel):
    school: str
    degree: str | None = None
    major: str | None = None
    minor: str | None = None
    start_date: Date | None = None
    end_date: Date | None = None
    gpa: str | None = None
    honors: list[str] = Field(default_factory=list)
    coursework: list[str] = Field(default_factory=list)


class ExperienceIn(BaseModel):
    company: str
    title: str
    location: str | None = None
    start_date: Date | None = None
    end_date: Date | None = None
    currently_working: bool = False
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    measurable_impact: list[str] = Field(default_factory=list)


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    start_date: Date | None = None
    end_date: Date | None = None


class CertificationIn(BaseModel):
    name: str
    issuer: str | None = None
    issue_date: Date | None = None
    expiration_date: Date | None = None
    credential_url: str | None = None


class AwardIn(BaseModel):
    name: str
    issuer: str | None = None
    date: Date | None = None
    description: str | None = None


class CareerProfileIn(BaseModel):
    education: list[EducationIn] = Field(default_factory=list)
    experience: list[ExperienceIn] = Field(default_factory=list)
    projects: list[ProjectIn] = Field(default_factory=list)
    certifications: list[CertificationIn] = Field(default_factory=list)
    awards: list[AwardIn] = Field(default_factory=list)


class ProfileImportIn(BaseModel):
    text: str = Field(min_length=20, max_length=50000)
    source: str = "pasted_text"


class ImportedField(BaseModel):
    value: Any = None
    confidence: str = "low"


class ProfileImportDraft(BaseModel):
    basic_info: dict[str, Any] = Field(default_factory=dict)
    job_targets: dict[str, Any] = Field(default_factory=dict)
    education: list[dict[str, Any]] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[dict[str, Any]] = Field(default_factory=list)
    awards: list[dict[str, Any]] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)
    low_confidence_fields: list[str] = Field(default_factory=list)
