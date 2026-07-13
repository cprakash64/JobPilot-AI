from datetime import datetime

from pydantic import BaseModel, Field


class DiscoverJobsIn(BaseModel):
    posted_within_days: int = Field(default=7, ge=1, le=60)
    include_unknown_dates: bool = False
    include_unknown_location: bool = False


class GenerateMaterialsIn(BaseModel):
    types: list[str] = Field(default_factory=lambda: ["resume", "cover_letter"])


class DocumentUpdateIn(BaseModel):
    markdown: str | None = None
    plain_text: str | None = None
    content: dict | None = None
    title: str | None = None


class JobMatchInfo(BaseModel):
    fit_score: float | None = None
    fit_label: str | None = None
    match_reasons: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    recommended_resume_angle: str | None = None
    confidence: float | None = None
    explanation_source: str | None = None


class JobCardOut(BaseModel):
    id: int
    title: str
    company: str
    source: str | None = None
    location: str | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    seniority_level: str | None = None
    posted_at: datetime | None = None
    discovered_at: datetime
    application_url: str
    source_url: str
    description_clean: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    match: JobMatchInfo | None = None


class JobPostingOut(BaseModel):
    id: int
    title: str
    company: str
    location: str | None = None
    remote_type: str | None = None
    employment_type: str | None = None
    seniority_level: str | None = None
    posted_at: datetime | None = None
    discovered_at: datetime
    application_url: str
    source_url: str
    description_clean: str
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    fit_score: float | None = None
    fit_summary: str | None = None


class JobMatchOut(BaseModel):
    job_id: int
    fit_score: float
    fit_summary: str
    strengths: list[str]
    gaps: list[str]
    risks: list[str]
    recommended_resume_angle: str | None = None


class GeneratedDocumentOut(BaseModel):
    id: int
    job_id: int
    type: str
    format: str
    content: dict
    file_path: str | None = None
    created_at: datetime


class ApplicationTrackerIn(BaseModel):
    status: str
    notes: str | None = None
    follow_up_date: str | None = None


class ApplicationTrackerOut(BaseModel):
    id: int
    job_id: int
    status: str
    notes: str | None = None
    applied_at: datetime | None = None

