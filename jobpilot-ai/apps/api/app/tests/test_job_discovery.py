"""Tests for profile-based job discovery, freshness, dedup, and fit scoring."""

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
from app.jobs.job_description_parser import parse_job_description
from app.jobs.job_matching_service import JobView, ProfileView, score_job
from app.jobs.job_normalization_service import deduplicate, is_fresh, normalize_jobs
from app.jobs.job_search_criteria_service import build_search_criteria
from app.main import app
from app.models import entities  # noqa: F401


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def signup(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/signup", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def ml_profile_payload() -> dict:
    return {
        "full_name": "Test Candidate",
        "target_roles": ["Machine Learning Engineer"],
        "target_levels": ["Mid"],
        "preferred_locations": ["Remote"],
        "remote_preference": "remote",
        "skills": ["Python", "FastAPI", "PyTorch", "Computer Vision", "NLP", "RAG", "OpenAI", "PostgreSQL"],
        "requires_sponsorship": False,
    }


# --------------------------------------------------------------------------- #
# Fake source
# --------------------------------------------------------------------------- #
def _job(external_id, title, skills, days, *, company="Acme", location="Remote", source="greenhouse",
         posted=True, url=None):
    posted_at = (datetime.now(UTC) - timedelta(days=days)) if posted else None
    url = url or f"https://job-boards.greenhouse.io/{company.lower()}/{external_id}"
    return NormalizedJob(
        external_id=str(external_id),
        title=title,
        company=company,
        location=location,
        remote_type="remote" if location and "remote" in location.lower() else "onsite",
        employment_type="full-time",
        seniority_level=None,
        posted_at=posted_at,
        application_url=url,
        source_url=url,
        description_raw="",
        description_clean=(
            f"{title}. Responsibilities: Build and ship models. "
            f"Requirements: {', '.join(skills)}. {'5+ years experience.' if 'Senior' in title else ''}"
        ),
        source=source,
        required_skills=skills,
    )


class FakeSource(JobSourceAdapter):
    source_type = "greenhouse"

    def __init__(self, jobs):
        super().__init__("fake", "Acme")
        self._jobs = jobs

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        return list(self._jobs)


class BrokenSource(JobSourceAdapter):
    source_type = "lever"

    def __init__(self):
        super().__init__("broken", "BrokenCo")

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        raise RuntimeError("network down")


def default_jobs():
    return [
        _job(1, "Machine Learning Engineer", ["Python", "PyTorch", "NLP"], 2),
        _job(1, "Machine Learning Engineer", ["Python", "PyTorch", "NLP"], 2),  # exact dup
        _job(2, "Machine Learning Engineer", ["Python", "PyTorch", "NLP"], 2, source="lever"),  # logical dup
        _job(3, "Warehouse Associate", ["Forklift", "Packing"], 1, location="Dallas, TX"),
        _job(4, "NLP Engineer", ["Python", "NLP", "RAG"], 30),  # stale
        _job(5, "AI Engineer", ["Python", "OpenAI"], 3),
        _job(6, "Senior Staff ML Engineer", ["Python", "PyTorch"], 1),  # too senior
        _job(7, "ML Engineer", ["Python"], 1, posted=False),  # unknown date
    ]


# --------------------------------------------------------------------------- #
# Unit: criteria
# --------------------------------------------------------------------------- #
def test_profile_builds_ml_role_queries(client: TestClient) -> None:
    headers = signup(client, "crit@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    # Build criteria directly from the model to assert expansion.
    from app.models.entities import UserProfile
    from sqlalchemy import select

    override = app.dependency_overrides[get_db]
    gen = override()
    db = next(gen)
    profile = db.scalar(select(UserProfile))
    criteria = build_search_criteria(profile, [])
    lowered = [r.lower() for r in criteria.role_queries]
    assert "machine learning engineer" in lowered
    assert any("ai engineer" in r for r in lowered)
    assert any("computer vision" in r for r in lowered) or any("nlp engineer" in r for r in lowered)
    assert "Python" in criteria.skills


def test_empty_profile_produces_empty_criteria() -> None:
    criteria = build_search_criteria(None, [])
    assert criteria.is_empty()
    assert criteria.role_queries == []


# --------------------------------------------------------------------------- #
# Unit: freshness + dedup + parsing
# --------------------------------------------------------------------------- #
def test_freshness_excludes_old_and_unknown_by_default():
    now = datetime.now(UTC)
    assert is_fresh(now - timedelta(days=3), 7) is True
    assert is_fresh(now - timedelta(days=10), 7) is False
    assert is_fresh(None, 7) is False
    assert is_fresh(None, 7, include_unknown=True) is True


def test_deduplicate_removes_exact_and_logical_duplicates():
    jobs = normalize_jobs(default_jobs())
    titles = [j.title for j in jobs]
    # Two ML Engineer entries (same source id, and cross-source logical dup) collapse to one.
    assert titles.count("Machine Learning Engineer") == 1


def test_description_parser_extracts_skills_and_responsibilities():
    parsed = parse_job_description(
        "Responsibilities: Build ML pipelines and deploy models. "
        "Requirements: 3+ years experience. Python, PyTorch and PostgreSQL required. "
        "Bachelor's degree in Computer Science. No visa sponsorship.",
        title="Machine Learning Engineer",
        location="Remote",
    )
    assert "Python" in parsed.required_skills
    assert "PyTorch" in parsed.required_skills
    assert parsed.responsibilities
    assert parsed.years_experience_min == 3
    assert parsed.degree_requirement is not None
    assert parsed.work_authorization_notes == "No visa sponsorship"
    assert parsed.confidence > 0


# --------------------------------------------------------------------------- #
# Unit: scoring
# --------------------------------------------------------------------------- #
def ml_view() -> ProfileView:
    return ProfileView(
        target_roles=["Machine Learning Engineer"],
        skills=["Python", "PyTorch", "NLP", "RAG", "OpenAI", "PostgreSQL"],
        experience_titles=["Machine Learning Engineer"],
        preferred_locations=["Remote"],
        remote_preference="remote",
        seniority_targets=["mid", "senior"],
        has_degree=True,
        requires_sponsorship=False,
    )


def test_relevant_job_ranks_above_irrelevant():
    view = ml_view()
    relevant = score_job(
        view,
        JobView(title="Machine Learning Engineer", required_skills=["Python", "PyTorch", "NLP"],
                workplace_type="remote", location="Remote"),
    )
    irrelevant = score_job(
        view,
        JobView(title="Warehouse Associate", required_skills=["Forklift", "Packing"],
                workplace_type="onsite", location="Dallas, TX"),
    )
    assert relevant.fit_score > irrelevant.fit_score
    assert relevant.fit_label == "Strong fit"
    assert "Forklift" in irrelevant.missing_skills


def test_location_conflict_lowers_score():
    view = ml_view()
    remote = score_job(view, JobView(title="ML Engineer", required_skills=["Python"], workplace_type="remote"))
    onsite = score_job(view, JobView(title="ML Engineer", required_skills=["Python"], workplace_type="onsite", location="NYC"))
    assert onsite.fit_score < remote.fit_score
    assert any("remote" in r.lower() for r in onsite.risk_factors)


def test_seniority_mismatch_lowers_score():
    junior = ProfileView(target_roles=["Machine Learning Engineer"], skills=["Python", "PyTorch"],
                         seniority_targets=["new grad", "junior"], remote_preference="everything", has_degree=True)
    senior_job = JobView(title="Staff Machine Learning Engineer", required_skills=["Python", "PyTorch"],
                         seniority="staff", workplace_type="remote")
    result = score_job(junior, senior_job)
    assert result.fit_score <= 60
    assert any("seniority" in r.lower() for r in result.risk_factors)


def test_sponsorship_conflict_is_flagged():
    view = ml_view()
    view.requires_sponsorship = True
    job = JobView(title="Machine Learning Engineer", required_skills=["Python", "PyTorch"],
                  workplace_type="remote", work_authorization_notes="No visa sponsorship")
    result = score_job(view, job)
    assert any("sponsor" in r.lower() for r in result.risk_factors)


def test_missing_skills_detected():
    view = ProfileView(skills=["Python"], target_roles=["ML Engineer"], remote_preference="everything")
    result = score_job(view, JobView(title="ML Engineer", required_skills=["Python", "Kubernetes", "Go"]))
    assert "Kubernetes" in result.missing_skills
    assert "Go" in result.missing_skills


# --------------------------------------------------------------------------- #
# Endpoint: discovery
# --------------------------------------------------------------------------- #
def _patch_sources(monkeypatch, jobs, *, add_broken=False):
    sources = [FakeSource(jobs)]
    if add_broken:
        sources.append(BrokenSource())
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: sources)


def test_discover_returns_fresh_ranked_jobs_for_user(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "disc@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs(), add_broken=True)

    response = client.post("/jobs/discover", headers=headers, json={"posted_within_days": 7})
    assert response.status_code == 200
    body = response.json()
    titles = [job["title"] for job in body["jobs"]]

    # Stale (30d) and unknown-date jobs excluded; duplicates collapsed.
    assert "NLP Engineer" not in titles
    assert "ML Engineer" not in titles  # unknown-date one
    assert titles.count("Machine Learning Engineer") == 1
    # Ranked: relevant ML/AI roles rank above the warehouse role.
    assert body["jobs"][0]["match"]["fit_score"] >= body["jobs"][-1]["match"]["fit_score"]
    assert body["jobs"][0]["match"]["fit_label"] in {"Strong fit", "Good fit"}
    # Broken source is reported, not fatal.
    assert any("BrokenCo" in warning for warning in body["discovery"]["source_warnings"])
    # Every job carries an official application URL and source.
    assert all(job["application_url"].startswith("http") for job in body["jobs"])
    assert all(job["source"] for job in body["jobs"])


def test_discover_include_unknown_dates_opt_in(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "unknown@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())

    response = client.post(
        "/jobs/discover", headers=headers, json={"posted_within_days": 7, "include_unknown_dates": True}
    )
    titles = [job["title"] for job in response.json()["jobs"]]
    assert "ML Engineer" in titles  # the unknown-date job now included


def test_discover_auto_scores_other_active_users(client: TestClient, monkeypatch) -> None:
    """When user A triggers discovery, newly ingested jobs are automatically
    scored for every other active user too — user B should NOT have to click
    "Refresh matches" to get scores (this is the core fix)."""
    a = signup(client, "usera@example.com")
    b = signup(client, "userb@example.com")
    client.put("/profile", headers=a, json=ml_profile_payload())
    client.put("/profile", headers=b, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())

    client.post("/jobs/discover", headers=a, json={})

    # User B never ran discovery, yet their jobs are scored automatically.
    b_jobs = client.get("/jobs", headers=b).json()["jobs"]
    assert b_jobs, "shared postings should be visible"
    assert any(
        job["match"] and job["match"]["score_state"] == "scored" for job in b_jobs
    ), "user B's jobs should be auto-scored without a manual refresh"
    # User A also has match scores.
    a_jobs = client.get("/jobs", headers=a).json()["jobs"]
    assert any(job["match"] and job["match"]["fit_score"] is not None for job in a_jobs)


def test_discover_marks_profile_incomplete_users(client: TestClient, monkeypatch) -> None:
    """A user with no scorable profile gets a profile_incomplete placeholder
    (not a silent 'Not scored') when jobs are ingested."""
    a = signup(client, "hasprofile@example.com")
    b = signup(client, "noprofile@example.com")  # never sets a profile
    client.put("/profile", headers=a, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())

    client.post("/jobs/discover", headers=a, json={})

    b_jobs = client.get("/jobs", headers=b).json()["jobs"]
    assert b_jobs
    assert all(
        job["match"] and job["match"]["score_state"] == "profile_incomplete" for job in b_jobs
    )


def test_discover_does_not_crash_without_openai(client: TestClient, monkeypatch) -> None:
    # No OPENAI_API_KEY is configured in tests; discovery must still work end to end.
    from app.ai.provider import ai_provider

    assert ai_provider.client is None
    headers = signup(client, "noai@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())
    response = client.post("/jobs/discover", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["jobs"]


def test_list_jobs_filters(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "filter@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())
    client.post("/jobs/discover", headers=headers, json={})

    # Filter by role text.
    ml_only = client.get("/jobs", headers=headers, params={"role": "warehouse"}).json()["jobs"]
    assert all("warehouse" in job["title"].lower() for job in ml_only)

    # Minimum fit score filters out low-fit jobs.
    high = client.get("/jobs", headers=headers, params={"min_fit_score": 70}).json()["jobs"]
    assert all(job["match"]["fit_score"] >= 70 for job in high)
    assert not any(job["title"] == "Warehouse Associate" for job in high)


def test_save_job_creates_tracker(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "save@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())
    jobs = client.post("/jobs/discover", headers=headers, json={}).json()["jobs"]
    job_id = jobs[0]["id"]

    response = client.post(f"/jobs/{job_id}/save", headers=headers)
    assert response.status_code == 200
    assert response.json()["tracker"]["status"] == "saved"
    tracker = client.get("/jobs/tracker/all", headers=headers).json()["applications"]
    assert any(row["job_id"] == job_id for row in tracker)


def test_tracker_returns_job_details_and_hides_application_stage_jobs(
    client: TestClient, monkeypatch
) -> None:
    headers = signup(client, "tracked-details@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())
    jobs = client.post("/jobs/discover", headers=headers, json={}).json()["jobs"]
    job = jobs[0]

    # Saving is a bookmark, so the job remains discoverable.
    client.post(f"/jobs/{job['id']}/save", headers=headers)
    saved_jobs = client.get("/jobs", headers=headers).json()["jobs"]
    assert any(row["id"] == job["id"] for row in saved_jobs)

    tracked = client.get("/jobs/tracker/all", headers=headers).json()["applications"]
    tracked_row = next(row for row in tracked if row["job_id"] == job["id"])
    assert tracked_row["job"]["title"] == job["title"]
    assert tracked_row["job"]["company"] == job["company"]
    assert tracked_row["job"]["application_url"] == job["application_url"]

    # Starting an application moves it out of discovery to prevent reapplying.
    response = client.put(
        f"/jobs/{job['id']}/tracker",
        headers=headers,
        json={"status": "applying"},
    )
    assert response.status_code == 200
    assert response.json()["tracker"]["job_id"] == job["id"]
    assert response.json()["tracker"]["status"] == "applying"
    remaining = client.get("/jobs", headers=headers).json()["jobs"]
    assert all(row["id"] != job["id"] for row in remaining)


def test_post_apply_status_sets_applied_date(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "interview-date@example.com")
    client.put("/profile", headers=headers, json=ml_profile_payload())
    _patch_sources(monkeypatch, default_jobs())
    job_id = client.post("/jobs/discover", headers=headers, json={}).json()["jobs"][0]["id"]

    response = client.put(
        f"/jobs/{job_id}/tracker",
        headers=headers,
        json={"status": "interview"},
    )
    assert response.status_code == 200
    tracked = client.get("/jobs/tracker/all", headers=headers).json()["applications"]
    row = next(item for item in tracked if item["job_id"] == job_id)
    assert row["applied_at"] is not None


def test_empty_profile_discovery_does_not_crash(client: TestClient, monkeypatch) -> None:
    headers = signup(client, "empty@example.com")  # no profile saved
    _patch_sources(monkeypatch, default_jobs())
    response = client.post("/jobs/discover", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["profile_complete"] is False
