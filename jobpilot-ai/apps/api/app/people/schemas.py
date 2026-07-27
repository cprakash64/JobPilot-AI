from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PeopleCategory = Literal["likely_recruiter", "potential_hiring_manager", "potential_referrer"]
EmailStatus = Literal[
    "not_requested", "searching", "verified", "accept_all", "risky", "unknown",
    "not_found", "provider_error",
]


class CompanyIdentity(BaseModel):
    canonical_name: str
    canonical_domain: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    parent_name: str | None = None
    parent_domain: str | None = None
    domain_confidence: float = Field(ge=0, le=1)
    evidence_source: str


class JobPeopleSearchProfile(BaseModel):
    company_name: str
    company_domain: str | None = None
    company_aliases: list[str] = Field(default_factory=list, max_length=20)
    parent_company_name: str | None = None
    parent_company_domain: str | None = None
    domain_confidence: float = Field(default=0, ge=0, le=1)
    company_evidence_source: str = "unresolved"
    job_title: str
    role_family: str | None = None
    department: str | None = None
    subdepartment: str | None = None
    seniority: str | None = None
    location: str | None = None
    employment_type: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=30)
    recruiter_titles: list[str] = Field(default_factory=list, max_length=20)
    hiring_manager_titles: list[str] = Field(default_factory=list, max_length=20)
    team_member_titles: list[str] = Field(default_factory=list, max_length=20)
    extraction_confidence: float = Field(ge=0, le=1)
    extraction_reasons: list[str] = Field(default_factory=list, max_length=10)


class PeopleSearchQuery(BaseModel):
    category: PeopleCategory
    company_name: str
    company_domain: str | None
    titles: list[str]
    title_group: str = "primary"
    company_aliases: list[str] = Field(default_factory=list, max_length=20)
    seniorities: list[str] = Field(default_factory=list, max_length=10)
    location_filter_mode: Literal["none", "soft", "hard"] = "soft"
    company_match_kind: Literal["canonical", "related"] = "canonical"
    role_family: str | None = None
    department: str | None = None
    location: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class PersonEnrichmentRequest(BaseModel):
    provider_person_id: str


class ProviderPerson(BaseModel):
    provider: str
    provider_person_id: str
    full_name: str
    current_company_name: str
    current_company_domain: str | None = None
    current_title: str
    department: str | None = None
    seniority: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    source_profile_url: str | None = None
    source_last_updated_at: datetime | None = None
    employment_verified_at: datetime | None = None
    education: list[str] = Field(default_factory=list)
    previous_employers: list[str] = Field(default_factory=list)
    evidence: dict[str, object] = Field(default_factory=dict)
    field_provenance: dict[str, str] = Field(default_factory=dict)


class ProviderUsage(BaseModel):
    provider: str
    credits_used: int = 0
    requests: int = 0


class WorkEmailRequest(BaseModel):
    full_name: str
    company_domain: str
    provider_person_id: str | None = None


class WorkEmailResult(BaseModel):
    status: EmailStatus
    email: str | None = None
    professional: bool = False
    provider: str


class EmailVerificationResult(BaseModel):
    status: EmailStatus
    provider: str
    verified_at: datetime | None = None


class FeedbackRequest(BaseModel):
    relevance_rating: Literal["relevant", "irrelevant", "unsure"] | None = None
    employment_current_rating: Literal["current", "stale", "unsure"] | None = None
    information_correct_rating: Literal["correct", "incorrect", "unsure"] | None = None
    contacted: bool = False
    received_response: bool = False
    incorrect_reason: str | None = Field(default=None, max_length=500)


class OutreachDraftRequest(BaseModel):
    draft_type: Literal[
        "recruiter_introduction", "referral_request", "shared_school",
        "shared_previous_employer", "potential_hiring_manager_introduction",
        "follow_up", "thank_you",
    ]
    user_details: str | None = Field(default=None, max_length=1000)
