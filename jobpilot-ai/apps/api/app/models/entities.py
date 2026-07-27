from __future__ import annotations

from datetime import date as DateValue
from datetime import datetime as DateTimeValue
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JsonType = JSON().with_variant(JSONB, "postgresql")


class DocumentType(StrEnum):
    resume = "resume"
    cover_letter = "cover_letter"
    application_answers = "application_answers"


class DocumentFormat(StrEnum):
    docx = "docx"
    pdf = "pdf"
    markdown = "markdown"
    json = "json"


class ApplicationStatus(StrEnum):
    saved = "saved"
    ready_to_apply = "ready_to_apply"
    applied = "applied"
    interview = "interview"
    rejected = "rejected"
    offer = "offer"
    applying = "applying"  # assisted auto-apply in progress (not yet submitted)


class ApplicationSessionStatus(StrEnum):
    """Lifecycle of an assisted auto-apply session. The user always submits the
    employer form manually; JobPilot never advances past ``ready_for_review`` on
    the employer's behalf."""

    preparing = "preparing"
    ready = "ready"
    opened = "opened"
    filling = "filling"
    review_required = "review_required"
    ready_for_review = "ready_for_review"
    completed = "completed"
    failed = "failed"
    expired = "expired"
    cancelled = "cancelled"


class ScoreState(StrEnum):
    """Lifecycle of a per-user, per-job fit score.

    ``pending`` and ``scoring`` are transient states shown as "Calculating fit…"
    on the UI; ``scored`` carries a real number; ``failed`` means scoring raised
    and can be retried; ``profile_incomplete`` means the user has not supplied
    enough profile information to be scored yet."""

    pending = "pending"
    scoring = "scoring"
    scored = "scored"
    failed = "failed"
    profile_incomplete = "profile_incomplete"


class IngestionTrigger(StrEnum):
    scheduled = "scheduled"
    manual = "manual"
    startup_recovery = "startup_recovery"
    backfill = "backfill"


class IngestionStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"


class ApplicationActionType(StrEnum):
    session_created = "session_created"
    resume_generated = "resume_generated"
    cover_letter_generated = "cover_letter_generated"
    token_exchanged = "token_exchanged"
    page_opened = "page_opened"
    ats_detected = "ats_detected"
    field_discovered = "field_discovered"
    field_filled = "field_filled"
    field_skipped = "field_skipped"
    document_uploaded = "document_uploaded"
    review_required = "review_required"
    user_modified = "user_modified"
    status_changed = "status_changed"
    application_completed = "application_completed"
    application_failed = "application_failed"
    application_cancelled = "application_cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user")
    demographics: Mapped["SensitiveDemographics | None"] = relationship(back_populates="user")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Backward-compatible DISPLAY value only. The structured parts below are the
    # source of truth; ``full_name`` is recomputed from them whenever they are
    # confirmed (see app.profile.names.compose_full_name).
    full_name: Mapped[str] = mapped_column(String(200), default="")
    # Structured identity — the source of truth for name autofill.
    # ``full_name`` alone is ambiguous for multi-token given names ("Chandra
    # Prakash Pandey" is NOT first="Chandra" last="Prakash Pandey"), so these
    # are never silently derived by splitting full_name; they are set only via
    # explicit user confirmation (``name_confirmed``).
    first_name: Mapped[str | None] = mapped_column(String(120))
    middle_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    # Preferred first/last are separate fields because forms ask for them
    # separately; when blank they fall back to the legal name ONLY where the
    # form's helper text says to (see names.resolve_preferred_names).
    preferred_first_name: Mapped[str | None] = mapped_column(String(120))
    preferred_last_name: Mapped[str | None] = mapped_column(String(120))
    # Legacy free-text "name you go by" — retained so existing profiles keep
    # their value; superseded by preferred_first_name/preferred_last_name.
    preferred_name: Mapped[str | None] = mapped_column(String(120))
    name_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # The address used ON APPLICATIONS — deliberately separate from the account
    # email the user signs in with. Confirmed explicitly; a confirmed value
    # always takes precedence over the account email (see resolve_application_email).
    application_email: Mapped[str | None] = mapped_column(String(320))
    application_email_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    application_email_updated_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    # Write-only employer-account secret. Never serialized by /profile; only a
    # boolean "configured" flag is returned. Ciphertext is decrypted solely
    # for a session-scoped Workday extension response.
    workday_password_ciphertext: Mapped[str | None] = mapped_column(String(1000))
    # Raw user input, kept verbatim as the fallback if parsing was ever wrong.
    phone: Mapped[str | None] = mapped_column(String(50))
    # Structured phone — derived together by app.profile.phone.parse_phone so a
    # form that asks for country and national number separately can be filled
    # without concatenating strings (and without duplicating "+1").
    phone_country_code: Mapped[str | None] = mapped_column(String(8))
    phone_country_iso2: Mapped[str | None] = mapped_column(String(2))
    phone_national_number: Mapped[str | None] = mapped_column(String(32))
    phone_e164: Mapped[str | None] = mapped_column(String(32))
    location_city: Mapped[str | None] = mapped_column(String(120))
    location_state: Mapped[str | None] = mapped_column(String(120))
    location_postal_code: Mapped[str | None] = mapped_column(String(32))
    location_country: Mapped[str | None] = mapped_column(String(120))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    work_authorization: Mapped[str | None] = mapped_column(String(120))
    requires_sponsorship: Mapped[bool] = mapped_column(Boolean, default=False)
    open_to_relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    target_roles: Mapped[list] = mapped_column(JsonType, default=list)
    target_levels: Mapped[list] = mapped_column(JsonType, default=list)
    preferred_locations: Mapped[list] = mapped_column(JsonType, default=list)
    remote_preference: Mapped[str | None] = mapped_column(String(50))
    skills: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")


class SensitiveDemographics(Base):
    __tablename__ = "sensitive_demographics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Typed, per-question answers. Vocabularies live in app/profile/eeo.py —
    # each question has its OWN closed option set, because reusing one
    # Yes/No/Another list across five different questions is what allowed a
    # gender to be stored as "yes".
    gender_identity: Mapped[str | None] = mapped_column(String(64))
    gender_self_description: Mapped[str | None] = mapped_column(String(200))
    veteran_status: Mapped[str | None] = mapped_column(String(120))
    disability_status: Mapped[str | None] = mapped_column(String(120))
    hispanic_or_latino: Mapped[str | None] = mapped_column(String(64))
    # A list: people identify with more than one category, and collapsing that
    # to a single column is what produced the meaningless "another_option".
    race_ethnicity: Mapped[list | None] = mapped_column(JsonType)
    race_self_description: Mapped[str | None] = mapped_column(String(200))
    # True when migration 0015 could not carry a legacy value across, so the UI
    # asks the user to re-answer instead of showing a silently emptied form.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # Legacy free-text columns, retained (nulled by 0015) so no data is lost in
    # place; nothing reads them any more.
    gender: Mapped[str | None] = mapped_column(String(120))
    ethnicity: Mapped[str | None] = mapped_column(String(120))
    hispanic_latino_status: Mapped[str | None] = mapped_column(String(120))
    consent_to_store: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="demographics")


class Education(Base):
    __tablename__ = "education"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    school: Mapped[str] = mapped_column(String(200))
    degree: Mapped[str | None] = mapped_column(String(160))
    major: Mapped[str | None] = mapped_column(String(160))
    minor: Mapped[str | None] = mapped_column(String(160))
    start_date: Mapped[DateValue | None] = mapped_column(Date)
    end_date: Mapped[DateValue | None] = mapped_column(Date)
    gpa: Mapped[str | None] = mapped_column(String(20))
    # The scale is required to interpret and safely convert a GPA for employer
    # dropdowns. Existing profiles are the common US 4.0 scale by default.
    gpa_scale: Mapped[str] = mapped_column(String(10), default="4.0")
    honors: Mapped[list] = mapped_column(JsonType, default=list)
    coursework: Mapped[list] = mapped_column(JsonType, default=list)


class Experience(Base):
    __tablename__ = "experience"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[DateValue | None] = mapped_column(Date)
    end_date: Mapped[DateValue | None] = mapped_column(Date)
    currently_working: Mapped[bool] = mapped_column(Boolean, default=False)
    bullets: Mapped[list] = mapped_column(JsonType, default=list)
    technologies: Mapped[list] = mapped_column(JsonType, default=list)
    measurable_impact: Mapped[list] = mapped_column(JsonType, default=list)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    bullets: Mapped[list] = mapped_column(JsonType, default=list)
    technologies: Mapped[list] = mapped_column(JsonType, default=list)
    links: Mapped[list] = mapped_column(JsonType, default=list)
    start_date: Mapped[DateValue | None] = mapped_column(Date)
    end_date: Mapped[DateValue | None] = mapped_column(Date)


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str | None] = mapped_column(String(200))
    issue_date: Mapped[DateValue | None] = mapped_column(Date)
    expiration_date: Mapped[DateValue | None] = mapped_column(Date)
    credential_url: Mapped[str | None] = mapped_column(String(500))


class Award(Base):
    __tablename__ = "awards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str | None] = mapped_column(String(200))
    date: Mapped[DateValue | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)


class CompanyBranding(Base):
    """Normalized company branding, persisted SEPARATELY from individual job
    rows so every job posting from the same employer reuses one resolved
    logo instead of re-resolving (and re-fetching) per job. See
    app/jobs/company_logo_service.py for the resolution pipeline."""

    __tablename__ = "company_branding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Normalized (lowercased, suffix-stripped) company name — the reuse key.
    normalized_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(String(1000))
    # "ats" | "catalog" | "curated" | "discovered" | "none"
    source: Mapped[str] = mapped_column(String(20), default="none")
    # "resolved" | "unresolved" | "failed"
    resolution_status: Mapped[str] = mapped_column(String(20), default="unresolved")
    last_verified_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    type: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_api: Mapped[bool] = mapped_column(Boolean, default=True)
    terms_notes: Mapped[str | None] = mapped_column(Text)


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("job_sources.id", ondelete="SET NULL"))
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    company_domain: Mapped[str | None] = mapped_column(String(255))
    company_logo_url: Mapped[str | None] = mapped_column(String(1000))
    location: Mapped[str | None] = mapped_column(String(255), index=True)
    remote_type: Mapped[str | None] = mapped_column(String(80))
    employment_type: Mapped[str | None] = mapped_column(String(80))
    seniority_level: Mapped[str | None] = mapped_column(String(80))
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(10))
    posted_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True), index=True)
    discovered_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Updated on every run where the posting is still present on the source; used
    # to expire jobs that disappear from the official board (see Part 9).
    last_seen_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    application_url: Mapped[str] = mapped_column(String(1000))
    source_url: Mapped[str] = mapped_column(String(1000))
    description_raw: Mapped[str] = mapped_column(Text, default="")
    description_clean: Mapped[str] = mapped_column(Text, default="")
    required_skills: Mapped[list] = mapped_column(JsonType, default=list)
    preferred_skills: Mapped[list] = mapped_column(JsonType, default=list)
    responsibilities: Mapped[list] = mapped_column(JsonType, default=list)
    years_experience_min: Mapped[float | None] = mapped_column(Float)
    degree_requirement: Mapped[str | None] = mapped_column(String(200))
    work_authorization_notes: Mapped[str | None] = mapped_column(Text)
    parse_confidence: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    hash_for_deduplication: Mapped[str] = mapped_column(String(64), index=True)


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_job_match_user_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    # fit_score/fit_summary are nullable now: a match row can exist in a pending,
    # scoring, failed or profile_incomplete state before any number is computed.
    fit_score: Mapped[float | None] = mapped_column(Float)
    fit_label: Mapped[str | None] = mapped_column(String(40))
    fit_summary: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[list] = mapped_column(JsonType, default=list)
    gaps: Mapped[list] = mapped_column(JsonType, default=list)
    risks: Mapped[list] = mapped_column(JsonType, default=list)
    recommended_resume_angle: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    explanation_source: Mapped[str | None] = mapped_column(String(40))
    # --- Scoring lifecycle (see ScoreState) ---
    score_state: Mapped[str] = mapped_column(
        String(30), default=ScoreState.pending.value, server_default=ScoreState.pending.value, index=True
    )
    # Bumped when the scoring algorithm changes so stale scores can be refreshed.
    score_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scoring_error_code: Mapped[str | None] = mapped_column(String(60))
    scoring_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scored_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    # Hash of the user profile fields that affect a score; lets us skip rescoring
    # when nothing score-relevant changed.
    profile_version: Mapped[str | None] = mapped_column(String(64))
    # Hash of score-relevant job content; a change triggers a rescore.
    job_content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfessionalPerson(Base):
    """Provider-evidenced professional identity. Never populated by an LLM."""

    __tablename__ = "professional_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    current_company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_company_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    current_title: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    seniority: Mapped[str | None] = mapped_column(String(80))
    professional_location: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(1000))
    linkedin_url_normalized: Mapped[str | None] = mapped_column(String(1000), unique=True)
    professional_email_ciphertext: Mapped[str | None] = mapped_column(String(2000))
    professional_email_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    email_verification_status: Mapped[str] = mapped_column(
        String(30), default="not_requested", server_default="not_requested"
    )
    email_verified_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    employment_last_verified_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfessionalPersonSource(Base):
    __tablename__ = "professional_person_sources"
    __table_args__ = (
        UniqueConstraint("provider", "provider_person_id", name="uq_people_source_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("professional_people.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_person_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_profile_url: Mapped[str | None] = mapped_column(String(1000))
    source_last_updated_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    normalized_evidence: Mapped[dict] = mapped_column(JsonType, default=dict)
    field_provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    # Only allowlisted, redacted diagnostics; full provider payloads are never stored.
    redacted_payload: Mapped[dict] = mapped_column(JsonType, default=dict)


class JobPeopleCandidate(Base):
    __tablename__ = "job_people_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", "person_id", "candidate_category", name="uq_job_person_category"),
        Index("ix_job_people_fresh", "job_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("professional_people.id", ondelete="CASCADE"), index=True
    )
    candidate_category: Mapped[str] = mapped_column(String(40), index=True)
    category_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    current_employment_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation_reasons: Mapped[list] = mapped_column(JsonType, default=list)
    recommendation_limitations: Mapped[list] = mapped_column(JsonType, default=list)
    scoring_version: Mapped[str] = mapped_column(String(40), index=True)
    discovered_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), index=True)


class UserJobPeopleRecommendation(Base):
    __tablename__ = "user_job_people_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_id", "job_people_candidate_id", name="uq_user_job_people_candidate"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    job_people_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("job_people_candidates.id", ondelete="CASCADE"), index=True
    )
    relationship_type: Mapped[str | None] = mapped_column(String(40))
    shared_school: Mapped[str | None] = mapped_column(String(255))
    shared_employer: Mapped[str | None] = mapped_column(String(255))
    connection_strength: Mapped[float] = mapped_column(Float, default=0)
    personalized_reasons: Mapped[list] = mapped_column(JsonType, default=list)
    personalized_score: Mapped[float] = mapped_column(Float, default=0)
    viewed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    contacted_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    suppressed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PeopleDiscoveryRun(Base):
    __tablename__ = "people_discovery_runs"
    __table_args__ = (Index("ix_people_run_cache", "job_id", "query_fingerprint", "completed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    provider: Mapped[str] = mapped_column(String(40))
    query_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    records_searched: Mapped[int] = mapped_column(Integer, default=0)
    records_enriched: Mapped[int] = mapped_column(Integer, default=0)
    provider_credits_used: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(60))
    safe_failure_message: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))


class PeopleRecommendationFeedback(Base):
    __tablename__ = "people_recommendation_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("user_job_people_recommendations.id", ondelete="CASCADE"), index=True
    )
    relevance_rating: Mapped[str | None] = mapped_column(String(30))
    employment_current_rating: Mapped[str | None] = mapped_column(String(30))
    information_correct_rating: Mapped[str | None] = mapped_column(String(30))
    contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    received_response: Mapped[bool] = mapped_column(Boolean, default=False)
    incorrect_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionRun(Base):
    """One execution of the discovery/ingestion pipeline (scheduled or manual).

    A single source failing never fails the run: per-source outcomes are tallied
    and the run is marked ``partial`` when some sources fail but others succeed."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    started_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    sources_attempted: Mapped[int] = mapped_column(Integer, default=0)
    sources_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_fetched: Mapped[int] = mapped_column(Integer, default=0)
    jobs_inserted: Mapped[int] = mapped_column(Integer, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0)
    jobs_skipped: Mapped[int] = mapped_column(Integer, default=0)
    jobs_expired: Mapped[int] = mapped_column(Integer, default=0)
    scoring_tasks_queued: Mapped[int] = mapped_column(Integer, default=0)
    # Safe, summarized error strings only — never full payloads or secrets.
    errors: Mapped[list] = mapped_column(JsonType, default=list)
    detail: Mapped[dict] = mapped_column(JsonType, default=dict)


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), index=True)
    format: Mapped[DocumentFormat] = mapped_column(Enum(DocumentFormat), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[dict] = mapped_column(JsonType, default=dict)
    content_markdown: Mapped[str | None] = mapped_column(Text)
    plain_text: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[dict] = mapped_column(JsonType, default=dict)
    source_profile_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    job_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    format_version: Mapped[str] = mapped_column(String(20), default="v1")
    file_path: Mapped[str | None] = mapped_column(String(1000))
    docx_file_path: Mapped[str | None] = mapped_column(String(1000))
    pdf_file_path: Mapped[str | None] = mapped_column(String(1000))
    model_used: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(50), default="v1")
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApplicationTracker(Base):
    __tablename__ = "application_tracker"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_tracker_user_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus), default=ApplicationStatus.saved)
    applied_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    follow_up_date: Mapped[DateValue | None] = mapped_column(Date)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    metadata_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationSession(Base):
    """A secure, user-owned, short-lived assisted-apply session for one job.

    Holds snapshots + references to the tailored documents and the safe answers
    the extension is allowed to auto-fill. Private storage paths are never stored
    here; documents are reached through authenticated, ownership-checked routes.
    """

    __tablename__ = "application_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    status: Mapped[ApplicationSessionStatus] = mapped_column(
        Enum(ApplicationSessionStatus), default=ApplicationSessionStatus.preparing, index=True
    )
    source_url: Mapped[str] = mapped_column(String(1000))
    ats_type: Mapped[str | None] = mapped_column(String(40))
    profile_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    job_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    # The profile revision these answers were built from. NULL (or a mismatch
    # with the profile's current revision) means the snapshot is stale and must
    # be rebuilt before the extension is allowed to use it.
    profile_revision: Mapped[str | None] = mapped_column(String(32))
    answers_refreshed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    tailored_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_documents.id", ondelete="SET NULL")
    )
    tailored_cover_letter_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_documents.id", ondelete="SET NULL")
    )
    generated_answers: Mapped[list] = mapped_column(JsonType, default=list)
    unresolved_questions: Mapped[list] = mapped_column(JsonType, default=list)
    warnings: Mapped[list] = mapped_column(JsonType, default=list)
    # One-time launch handoff: we store only the SHA-256 of the launch token and
    # invalidate it on first exchange. The raw token is returned once to the web app.
    launch_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    launch_token_used: Mapped[bool] = mapped_column(Boolean, default=False)
    tracker_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_tracker.id", ondelete="SET NULL")
    )
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))


class ApplicationAuditLog(Base):
    """Append-only trail of important actions taken within an apply session."""

    __tablename__ = "application_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("application_sessions.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(60), index=True)
    field_key: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationAnswer(Base):
    """A verified, reusable answer to a common application question.

    Sensitive/consequential answers are never auto-filled unless the user has
    explicitly verified them and enabled auto-fill (``allow_auto_fill``)."""

    __tablename__ = "application_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "canonical_key", "company_key", name="uq_answer_user_key_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    canonical_key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[str | None] = mapped_column(Text)
    display_value: Mapped[str | None] = mapped_column(Text)
    # "global" (reusable everywhere), "company" (scoped to one employer via
    # ``company_key``), or "sensitive" (reusable only after explicit consent —
    # gated the same way as any other sensitive key, see canonical.py).
    # "application"-scoped answers are never persisted here at all.
    scope: Mapped[str] = mapped_column(String(20), default="global")
    # Normalized employer name when scope == "company"; "" (not NULL) for every
    # other scope so the unique constraint below reliably prevents duplicate
    # global rows across both SQLite and Postgres.
    company_key: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(40), default="user")
    is_user_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_required: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_auto_fill: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    last_verified_at: Mapped[DateTimeValue | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeValue] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTimeValue] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
