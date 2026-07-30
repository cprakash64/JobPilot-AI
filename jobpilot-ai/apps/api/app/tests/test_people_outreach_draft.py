"""Outreach drafting for people the UI is allowed to display.

Every person discovered through PDL lands on
``exact_company_current_but_unverified_freshness`` — PDL supplies no
independent freshness verification — yet the outreach gate accepted only
``confirmed_exact_company_verified``. So every rendered "Create LinkedIn draft"
button answered 409 and the UI showed a generic red failure.

Draft generation itself is deterministic: it composes verified fields with a
template and makes no model call, so there is no model failure to fall back
from.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.entities import (
    JobPeopleCandidate,
    JobPosting,
    ProfessionalPerson,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
)
from app.people.employment_validation import EMPLOYMENT_VALIDATION_VERSION
from app.people.schemas import OutreachDraftRequest
from app.people.scoring import SCORING_VERSION
from app.people.security import encrypt_email
from app.people.service import DISPLAYABLE_EMPLOYMENT_STATUSES, outreach_draft

LINKEDIN_URL = "https://www.linkedin.com/in/morgan-manager"


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def _outreach_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "people_outreach_drafting_enabled", True)


def _scenario(
    db: Session,
    *,
    employment_status: str = "exact_company_current_but_unverified_freshness",
    linkedin_url: str | None = LINKEDIN_URL,
    verified_email: str | None = None,
    category: str = "potential_hiring_manager",
):
    user = User(email="draft@example.com", hashed_password=hash_password("password123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserProfile(user_id=user.id, full_name="Sam Candidate", skills=["Python"]))
    job = JobPosting(
        external_id="l3harris-1",
        title="Software Engineer",
        company="L3Harris Technologies",
        company_domain="l3harris.example",
        location="Palm Bay, Florida, United States",
        employment_type="full-time",
        seniority_level="mid",
        application_url="https://simplify.jobs/p/l3harris-1",
        source_url="https://simplify.jobs/p/l3harris-1",
        description_raw="Build software.",
        description_clean="Build backend software services with Python.",
        required_skills=["Python"],
        raw_json={},
        hash_for_deduplication="l" * 64,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    person = ProfessionalPerson(
        canonical_full_name="Morgan Manager",
        normalized_full_name="morgan manager",
        current_title="Engineering Manager",
        normalized_title="engineering manager",
        current_company_name="L3Harris Technologies",
        current_company_domain="l3harris.example",
        linkedin_url=linkedin_url,
        linkedin_url_normalized=linkedin_url,
        email_verification_status="verified" if verified_email else "not_requested",
        professional_email_ciphertext=(
            encrypt_email(verified_email) if verified_email else None
        ),
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    candidate = JobPeopleCandidate(
        job_id=job.id,
        person_id=person.id,
        candidate_category=category,
        category_score=82,
        data_confidence=0.8,
        current_employment_confidence=0.78,
        employment_validation_status=employment_status,
        employment_validation_version=EMPLOYMENT_VALIDATION_VERSION,
        employment_validation_checked_at=datetime.now(UTC),
        recommendation_reasons=[],
        recommendation_limitations=[],
        scoring_version=SCORING_VERSION,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    recommendation = UserJobPeopleRecommendation(
        user_id=user.id,
        job_id=job.id,
        job_people_candidate_id=candidate.id,
        personalized_reasons=[],
    )
    db.add(recommendation)
    db.commit()
    db.refresh(recommendation)
    return user, job, recommendation


def _request(message_type: str = "linkedin_message") -> OutreachDraftRequest:
    return OutreachDraftRequest(
        draft_type="potential_hiring_manager_introduction",
        message_type=message_type,
    )


@pytest.mark.parametrize("status", sorted(DISPLAYABLE_EMPLOYMENT_STATUSES))
def test_every_displayable_person_can_be_drafted_for(db: Session, status: str) -> None:
    """A person the card renders must be a person the draft button works on."""

    user, job, recommendation = _scenario(db, employment_status=status)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["body"]
    assert draft["character_count"] == len(draft["body"])
    assert draft["sent"] is False


def test_pdl_freshness_status_no_longer_returns_409(db: Session) -> None:
    """The exact live failure: L3Harris managers 409'd on every draft."""

    user, job, recommendation = _scenario(
        db, employment_status="exact_company_current_but_unverified_freshness"
    )
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert "Morgan" in draft["body"]
    assert draft["generation_path"] == "deterministic_template"


def test_conflicting_employment_is_still_blocked(db: Session) -> None:
    user, job, recommendation = _scenario(
        db, employment_status="conflicting_current_employment"
    )
    with pytest.raises(HTTPException) as raised:
        outreach_draft(db, user, job.id, recommendation.id, _request())
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "PEOPLE_EMPLOYMENT_REVALIDATION_REQUIRED"


def test_revalidation_flag_is_still_blocked(db: Session) -> None:
    user, job, recommendation = _scenario(db)
    person = db.get(ProfessionalPerson, 1)
    person.employment_revalidation_required = True
    db.commit()
    with pytest.raises(HTTPException) as raised:
        outreach_draft(db, user, job.id, recommendation.id, _request())
    assert raised.value.status_code == 409


def test_draft_carries_the_verified_linkedin_url(db: Session) -> None:
    user, job, recommendation = _scenario(db, linkedin_url=LINKEDIN_URL)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["linkedin_url"] == LINKEDIN_URL
    assert draft["linkedin_available"] is True


def test_draft_never_invents_a_linkedin_url(db: Session) -> None:
    user, job, recommendation = _scenario(db, linkedin_url=None)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["linkedin_url"] is None
    assert draft["linkedin_available"] is False


def test_unsafe_stored_profile_url_is_not_handed_off(db: Session) -> None:
    user, job, recommendation = _scenario(
        db, linkedin_url="http://phishing.example/in/morgan-manager"
    )
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["linkedin_url"] is None


def test_email_draft_produces_subject_and_body_separately(db: Session) -> None:
    user, job, recommendation = _scenario(
        db, verified_email="morgan@l3harris.example"
    )
    draft = outreach_draft(db, user, job.id, recommendation.id, _request("email"))
    assert draft["subject"]
    assert draft["subject"] != draft["body"]
    assert job.title in draft["subject"]
    assert draft["email_available"] is True
    assert draft["professional_email"] == "morgan@l3harris.example"


def test_email_availability_is_false_without_a_verified_address(db: Session) -> None:
    user, job, recommendation = _scenario(db, verified_email=None)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request("email"))
    assert draft["email_available"] is False
    assert draft["professional_email"] is None


def test_linkedin_message_has_no_subject(db: Session) -> None:
    user, job, recommendation = _scenario(db)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["subject"] is None


def test_connection_note_is_bounded(db: Session) -> None:
    user, job, recommendation = _scenario(db)
    draft = outreach_draft(
        db, user, job.id, recommendation.id, _request("linkedin_connection_note")
    )
    assert len(draft["body"]) <= 300


def test_draft_claims_nothing_about_hiring_ownership(db: Session) -> None:
    """The template must never assert the contact owns the opening."""

    user, job, recommendation = _scenario(db)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    body = draft["body"].lower()
    for claim in (
        "you are hiring",
        "you're hiring",
        "your team is hiring",
        "you manage this role",
        "you own this req",
        "you can refer me",
        "you reviewed my",
    ):
        assert claim not in body
    # The uncertainty is recorded rather than glossed over.
    assert "team_membership_unconfirmed" in draft["omitted_uncertain_facts"]


def test_draft_is_grounded_in_recorded_facts_only(db: Session) -> None:
    user, job, recommendation = _scenario(db)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert f"job:{job.title}" in draft["facts_used"]
    assert f"company:{job.company}" in draft["facts_used"]
    assert draft["assumptions"] == []
    assert draft["requires_manual_review"] is True


def test_draft_carries_a_template_version_for_client_caching(db: Session) -> None:
    from app.people.service import OUTREACH_TEMPLATE_VERSION

    user, job, recommendation = _scenario(db)
    draft = outreach_draft(db, user, job.id, recommendation.id, _request())
    assert draft["template_version"] == OUTREACH_TEMPLATE_VERSION
    assert draft["recipient_name"] == "Morgan Manager"
    assert draft["recipient_category"] == "potential_hiring_manager"


def test_logs_carry_no_message_body_or_email_address(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    user, job, recommendation = _scenario(
        db, verified_email="morgan.private@l3harris.example"
    )
    with caplog.at_level(logging.INFO):
        draft = outreach_draft(
            db, user, job.id, recommendation.id, _request("email")
        )
    assert "morgan.private@l3harris.example" not in caplog.text
    assert draft["body"] not in caplog.text
    # Counts and availability flags are fine; content is not.
    assert "linkedin_available" in caplog.text
