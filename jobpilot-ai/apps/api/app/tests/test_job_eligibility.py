"""Unit + integration tests for the hard eligibility filtering layer."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs import job_ingestion_service
from app.jobs.job_eligibility_service import (
    evaluate_eligibility,
    is_location_eligible,
    is_role_eligible,
    is_seniority_eligible,
    normalize_location,
)
from app.jobs.job_matching_service import JobView, ProfileView
from app.main import app
from app.models import entities  # noqa: F401

US = ["Remote", "United States"]
JUNIOR = ["New Grad", "Entry Level", "Junior", "0-1 years", "1-3 years"]
JUNIOR_TARGETS = ["new grad", "junior"]
ROLES = [
    "Software Engineer", "Backend Engineer", "Frontend Engineer", "Full Stack Engineer",
    "AI Engineer", "Machine Learning Engineer", "NLP Engineer",
]


def loc_ok(location: str, *, include_unknown: bool = False) -> bool:
    return is_location_eligible(location, None, US, include_unknown_location=include_unknown)[0]


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "location",
    ["Remote, United States", "US Remote", "Remote - US", "United States Remote",
     "San Francisco, CA", "New York, NY", "Los Angeles, CA", "US-Remote", "Austin, TX"],
)
def test_us_locations_allowed(location: str) -> None:
    assert loc_ok(location) is True


@pytest.mark.parametrize(
    "location",
    ["Remote, Canada", "Toronto, Canada", "Remote, India", "Bengaluru, India",
     "Remote, EMEA", "Remote, Europe", "London, UK", "Remote in Canada", "CA-Remote"],
)
def test_non_us_locations_excluded(location: str) -> None:
    assert loc_ok(location) is False


def test_ca_remote_is_canada_not_california() -> None:
    info = normalize_location("CA-Remote")
    assert "canada" in info.countries
    assert not info.is_us


def test_city_ca_is_california() -> None:
    assert normalize_location("San Francisco, CA").is_us is True
    assert normalize_location("Los Angeles, CA").is_us is True


def test_unknown_remote_excluded_by_default_included_on_opt_in() -> None:
    assert loc_ok("Remote") is False
    assert loc_ok("Remote", include_unknown=True) is True


def test_worldwide_remote_allowed_for_us_user() -> None:
    assert loc_ok("Remote - Worldwide") is True


def test_no_location_restriction_when_user_has_no_countries() -> None:
    # User selected only "Remote" (no country) -> geography is not hard-filtered.
    assert is_location_eligible("Bengaluru, India", None, ["Remote"])[0] is True


# --------------------------------------------------------------------------- #
# Seniority
# --------------------------------------------------------------------------- #
def sen_ok(title: str, description: str = "") -> bool:
    return is_seniority_eligible(title, description, None, JUNIOR, JUNIOR_TARGETS)[0]


@pytest.mark.parametrize(
    "title",
    ["New Grad Backend Engineer", "Software Engineer I", "Associate Software Engineer",
     "Software Engineer", "Backend Engineer", "Early Career Software Engineer"],
)
def test_junior_titles_allowed(title: str) -> None:
    assert sen_ok(title) is True


@pytest.mark.parametrize(
    "title",
    ["Senior Software Engineer", "Sr Social Measurement Lead", "Staff Engineer",
     "Principal Engineer", "Engineering Manager", "Tech Lead", "Director of Engineering",
     "Distinguished Engineer", "Software Architect"],
)
def test_senior_titles_excluded(title: str) -> None:
    assert sen_ok(title) is False


def test_years_requirement_excludes_junior() -> None:
    assert sen_ok("Backend Engineer", "We require 5+ years of experience") is False
    assert sen_ok("Backend Engineer", "6+ years building distributed systems") is False
    assert sen_ok("Backend Engineer", "0-2 years experience welcome") is True


def test_seniority_not_filtered_when_user_open_to_senior() -> None:
    ok, _, _ = is_seniority_eligible("Senior Software Engineer", "", None, ["Senior", "Mid"], ["senior", "mid"])
    assert ok is True


# --------------------------------------------------------------------------- #
# Role family
# --------------------------------------------------------------------------- #
def role_ok(title: str) -> bool:
    return is_role_eligible(title, ROLES)[0]


@pytest.mark.parametrize(
    "title",
    ["Backend Engineer", "Machine Learning Engineer", "NLP Engineer", "AI Engineer",
     "Frontend Engineer", "Full Stack Engineer", "MLOps Engineer",
     "Machine Learning Operations Engineer", "Senior Software Engineer"],
)
def test_relevant_roles_pass_family(title: str) -> None:
    assert role_ok(title) is True


@pytest.mark.parametrize(
    "title",
    ["Customer Success Engineer", "Technical Partner Manager", "Sr Social Measurement Lead",
     "Design Engineer", "Sales Engineer", "Product Manager", "Program Manager",
     "Recruiter", "Solutions Architect", "Data Scientist"],
)
def test_unrelated_roles_fail_family(title: str) -> None:
    assert role_ok(title) is False


def test_role_not_passed_by_single_matching_skill() -> None:
    profile = ProfileView(target_roles=ROLES, skills=["Go", "SQL", "React", "CI/CD"], remote_preference="everything")
    # A sales role whose description mentions SQL must NOT become eligible.
    job = JobView(title="Sales Engineer", description="Work with SQL, Go, and React dashboards",
                  required_skills=["SQL", "Go", "React"], location="Remote, United States", workplace_type="remote")
    result = evaluate_eligibility(profile, job, target_levels=JUNIOR)
    assert result.eligible is False
    assert any(code.startswith("role") for code in result.reason_codes)


# --------------------------------------------------------------------------- #
# Combined
# --------------------------------------------------------------------------- #
def _profile() -> ProfileView:
    return ProfileView(
        target_roles=ROLES,
        skills=["Python", "React", "Go", "SQL"],
        preferred_locations=US,
        remote_preference="everything",
        seniority_targets=JUNIOR_TARGETS,
        target_levels=JUNIOR,
        has_degree=True,
        requires_sponsorship=False,
    )


def test_eligible_ml_job_passes_all_flags() -> None:
    job = JobView(title="Machine Learning Engineer", description="Build ML with Python",
                  required_skills=["Python"], location="Remote, United States", workplace_type="remote")
    result = evaluate_eligibility(_profile(), job)
    assert result.eligible is True
    assert all(result.flags[f] for f in ["role_match", "seniority_match", "location_match", "workplace_match"])


def test_senior_canada_job_fails_multiple() -> None:
    job = JobView(title="Senior Backend Engineer", description="", location="Toronto, Canada", workplace_type="onsite")
    result = evaluate_eligibility(_profile(), job)
    assert result.eligible is False
    assert result.flags["location_match"] is False
    assert result.flags["seniority_match"] is False


# --------------------------------------------------------------------------- #
# Integration through the discovery endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": "elig@example.com", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    client.put(
        "/profile",
        headers=headers,
        json={
            "full_name": "T", "target_roles": ROLES, "target_levels": JUNIOR,
            "preferred_locations": US, "remote_preference": "everything",
            "skills": ["Python", "React", "Go", "SQL"], "requires_sponsorship": False,
        },
    )
    return headers


def _job(i: int, title: str, location: str, skills=("Python",)) -> NormalizedJob:
    url = f"https://job-boards.greenhouse.io/acme/{i}"
    return NormalizedJob(
        external_id=str(i), title=title, company="Acme", location=location,
        remote_type="remote" if "remote" in location.lower() else "onsite",
        employment_type="ft", seniority_level=None, posted_at=datetime.now(UTC) - timedelta(days=2),
        application_url=url, source_url=url, description_raw="",
        description_clean=f"{title}. Requirements: {', '.join(skills)}.", source="greenhouse",
        required_skills=list(skills),
    )


def _install_sources(monkeypatch: pytest.MonkeyPatch, jobs: list[NormalizedJob]) -> None:
    class Fake(JobSourceAdapter):
        source_type = "greenhouse"

        def __init__(self) -> None:
            super().__init__("fake", "Acme")

        async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
            return list(jobs)

    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: [Fake()])


def _all_jobs() -> list[NormalizedJob]:
    return [
        _job(1, "Backend Engineer", "Remote, United States"),
        _job(2, "Machine Learning Engineer", "San Francisco, CA"),
        _job(3, "NLP Engineer", "New York, NY"),
        _job(4, "Senior Software Engineer", "Remote, United States"),
        _job(5, "Staff Engineer", "Austin, TX"),
        _job(6, "Backend Engineer", "Toronto, Canada"),
        _job(7, "Software Engineer", "Bengaluru, India"),
        _job(8, "Customer Success Engineer", "Remote, United States"),
        _job(9, "Technical Partner Manager", "Remote, United States"),
        _job(10, "Sr Social Measurement Lead", "Remote, United States"),
        _job(11, "Design Engineer", "Remote, United States"),
        _job(12, "Backend Engineer", "Remote, EMEA"),
    ]


def test_discovery_returns_only_eligible_jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _headers(client)
    _install_sources(monkeypatch, _all_jobs())

    body = client.post("/jobs/discover", headers=headers, json={"posted_within_days": 7}).json()
    titles = {job["title"] for job in body["jobs"]}
    locations = {job["location"] for job in body["jobs"]}

    # Eligible roles/levels/locations only.
    assert titles == {"Backend Engineer", "Machine Learning Engineer", "NLP Engineer"}
    # No Canada/India/EMEA.
    assert not any("Canada" in loc or "India" in loc or "EMEA" in loc for loc in locations)
    # No senior/staff titles.
    assert not any(word in t for t in titles for word in ["Senior", "Staff", "Sr "])
    # No unrelated roles.
    for bad in ["Customer Success Engineer", "Technical Partner Manager", "Sr Social Measurement Lead", "Design Engineer"]:
        assert bad not in titles

    # Debug counts add up.
    d = body["discovery"]
    assert d["eligible"] == 3
    assert d["excluded_location"] >= 3
    assert d["excluded_role"] >= 1


def test_eligible_jobs_are_scored(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _headers(client)
    _install_sources(monkeypatch, _all_jobs())
    body = client.post("/jobs/discover", headers=headers, json={}).json()
    assert body["jobs"], "expected eligible jobs"
    assert all(job["match"] and job["match"]["fit_score"] is not None for job in body["jobs"])


def test_include_ineligible_returns_excluded_with_reasons(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _headers(client)
    _install_sources(monkeypatch, _all_jobs())
    client.post("/jobs/discover", headers=headers, json={}).json()

    default = client.get("/jobs", headers=headers).json()["jobs"]
    with_ineligible = client.get("/jobs", headers=headers, params={"include_ineligible": "true"}).json()["jobs"]
    assert len(with_ineligible) > len(default)
    excluded = [j for j in with_ineligible if j["eligibility"] and not j["eligibility"]["eligible"]]
    assert excluded and all(j["eligibility"]["reasons"] for j in excluded)
