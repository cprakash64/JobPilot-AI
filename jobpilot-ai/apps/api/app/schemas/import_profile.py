from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TextSourceType = Literal["resume_text", "linkedin_text", "other_text"]
FileSourceType = Literal["resume", "linkedin_pdf", "auto"]
DraftSourceType = Literal["resume", "linkedin_pdf", "resume_text", "linkedin_text", "other_text", "unknown"]
ImportSection = Literal[
    "basic_info",
    "job_targets",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "awards",
    "links",
]


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class ImportProfileTextRequest(BaseModel):
    source_type: TextSourceType = "other_text"
    text: str = Field(min_length=20, max_length=50000)


class BasicInfoDraft(BaseModel):
    full_name: str = ""
    headline: str = ""
    phone: str = ""
    email: str = ""
    location_city: str = ""
    location_state: str = ""
    location_country: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    work_authorization_status: str = ""
    requires_sponsorship: bool | None = None


class JobTargetsDraft(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    target_levels: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    work_preference: str = ""

    @field_validator("target_roles", "target_levels", "preferred_locations", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> list[str]:
        return _list(value)


class EducationDraft(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    minor: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""
    honors: list[str] = Field(default_factory=list)
    coursework: list[str] = Field(default_factory=list)


class ExperienceDraft(BaseModel):
    company: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    currently_working: bool = False
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    measurable_impact: list[str] = Field(default_factory=list)


class ProjectDraft(BaseModel):
    name: str = ""
    subtitle: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""


class SkillGroupDraft(BaseModel):
    category: str = ""
    items: list[str] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def normalize_items(cls, value: Any) -> list[str]:
        return _list(value)


class ConfidenceDraft(BaseModel):
    overall: float = 0.0
    header: float = 0.0
    skills: float = 0.0
    education: float = 0.0
    experience: float = 0.0
    projects: float = 0.0
    awards: float = 0.0

    @field_validator("*", mode="before")
    @classmethod
    def coerce_score(cls, value: Any) -> float:
        if value is None or value == "":
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


class CertificationDraft(BaseModel):
    name: str = ""
    issuer: str = ""
    issue_date: str = ""
    expiration_date: str = ""
    credential_url: str = ""


class AwardDraft(BaseModel):
    name: str = ""
    issuer: str = ""
    date: str = ""
    description: str = ""


class LinksDraft(BaseModel):
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    other_links: list[str] = Field(default_factory=list)


class ImportProfileDraft(BaseModel):
    basic_info: BasicInfoDraft = Field(default_factory=BasicInfoDraft)
    summary: str = ""
    job_targets: JobTargetsDraft = Field(default_factory=JobTargetsDraft)
    education: list[EducationDraft] = Field(default_factory=list)
    experience: list[ExperienceDraft] = Field(default_factory=list)
    projects: list[ProjectDraft] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    skill_groups: list[SkillGroupDraft] = Field(default_factory=list)
    certifications: list[CertificationDraft] = Field(default_factory=list)
    awards: list[AwardDraft] = Field(default_factory=list)
    links: LinksDraft = Field(default_factory=LinksDraft)
    raw_text_preview: str = ""
    confidence: ConfidenceDraft = Field(default_factory=ConfidenceDraft)
    confidence_warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_type: DraftSourceType = "unknown"
    low_confidence_fields: list[str] = Field(default_factory=list)

    @field_validator("skills", "confidence_warnings", "missing_fields", "low_confidence_fields", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        return _list(value)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()


class ImportProfileResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    draft: ImportProfileDraft
    model_used: str
    document_kind: DraftSourceType = "unknown"
    compliance_notice: str


class ImportProfileApplyRequest(BaseModel):
    sections: list[ImportSection] = Field(min_length=1)
    draft: ImportProfileDraft
    overwrite: bool = False
