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

    full_name: str = ""
    phone: str | None = None
    location_city: str | None = None
    location_state: str | None = None
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


class SensitiveDemographicsIn(BaseModel):
    gender: str | None = PreferNot
    veteran_status: str | None = PreferNot
    disability_status: str | None = PreferNot
    ethnicity: str | None = PreferNot
    hispanic_latino_status: str | None = PreferNot
    consent_to_store: bool = False


class SensitiveDemographicsOut(SensitiveDemographicsIn):
    id: int
    user_id: int


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
