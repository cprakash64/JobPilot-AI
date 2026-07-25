"""Job card logo fields on the API + the backfill command for existing rows."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.jobs.backfill_company_logos import backfill_company_logos
from app.jobs.company_logo_service import is_untrusted_simplify_logo_url
from app.main import app
from app.models import entities as E
from app.models import entities  # noqa: F401


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


def auth(client: TestClient, email: str = "logo@example.com") -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": email, "password": "password123"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def complete_profile(client: TestClient, headers: dict[str, str]) -> None:
    client.put("/profile", headers=headers, json={
        "full_name": "Chandra Pandey", "target_roles": ["Backend Engineer"], "target_levels": ["Junior"],
        "preferred_locations": ["United States"], "remote_preference": "everything",
        "skills": ["Python", "FastAPI"],
    })


def seed_job(company: str, *, domain: str | None = None, logo: str | None = None) -> int:
    db = next(app.dependency_overrides[get_db]())
    src = db.scalar(select(E.JobSource).where(E.JobSource.name == company))
    if src is None:
        src = E.JobSource(name=company, type="greenhouse", base_url="x", enabled=True, supports_api=True)
        db.add(src)
        db.flush()
    job = E.JobPosting(
        source_id=src.id, external_id=f"be-{company}", title="Backend Engineer", company=company,
        company_domain=domain, company_logo_url=logo,
        location="Remote, United States", remote_type="remote",
        posted_at=datetime.now(UTC) - timedelta(days=1), discovered_at=datetime.now(UTC),
        application_url=f"https://boards.greenhouse.io/{company.lower()}/1",
        source_url=f"https://boards.greenhouse.io/{company.lower()}/1",
        description_raw="", description_clean="Backend Engineer. Requirements: Python, FastAPI.",
        required_skills=["Python", "FastAPI"], hash_for_deduplication=f"h-{company}",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


def _card(client, headers, company: str) -> dict:
    jobs = client.get("/jobs?posted_within_days=7", headers=headers).json()["jobs"]
    return next(j for j in jobs if j["company"] == company)


# --------------------------------------------------------------------------- #
# API response includes logo fields
# --------------------------------------------------------------------------- #
def test_api_card_includes_logo_for_known_company(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job("OpenAI")  # legacy row: no stored logo -> resolved at read time
    card = _card(client, headers, "OpenAI")
    assert "company_domain" in card and "company_logo_url" in card
    assert card["company_domain"] == "openai.com"
    assert "openai.com" in card["company_logo_url"]


def test_api_card_logo_null_for_unknown_company(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job("Totally Unknown Startup ZZZ")
    card = _card(client, headers, "Totally Unknown Startup ZZZ")
    assert card["company_logo_url"] is None


def test_legacy_simplify_card_rejects_guessed_mirror_logo(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job("Medtronic")
    company_id = "49ecfd11-34f2-4b53-bf06-6f2f48f24429"
    db = next(app.dependency_overrides[get_db]())
    job = db.get(E.JobPosting, job_id)
    source = db.get(E.JobSource, job.source_id)
    source.type = "simplifyjobs"
    job.raw_json = {"company_url": f"https://simplify.jobs/c/{company_id}/Medtronic"}
    job.company_logo_url = (
        f"https://storage.googleapis.com/simplify-imgs/companies/{company_id}/logo.png"
    )
    db.commit()
    db.close()

    card = _card(client, headers, "Medtronic")
    assert card["company_domain"] == "medtronic.com"
    assert "medtronic.com" in card["company_logo_url"]


def test_simplify_image_mirror_is_not_an_identity_source() -> None:
    assert is_untrusted_simplify_logo_url(
        "https://storage.googleapis.com/simplify-imgs/companies/"
        "49ecfd11-34f2-4b53-bf06-6f2f48f24429/logo.png"
    )
    assert not is_untrusted_simplify_logo_url("https://www.medtronic.com/logo.png")


def test_api_prefers_stored_logo_over_resolver(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job("Acme", domain="acme.dev", logo="https://cdn.example.com/acme.png")
    card = _card(client, headers, "Acme")
    assert card["company_logo_url"] == "https://cdn.example.com/acme.png"


# --------------------------------------------------------------------------- #
# Backfill command
# --------------------------------------------------------------------------- #
def test_backfill_updates_existing_jobs(client: TestClient) -> None:
    seed_job("OpenAI")  # known, missing logo
    seed_job("Totally Unknown Startup ZZZ")  # unknown, stays empty
    db = next(app.dependency_overrides[get_db]())
    summary = backfill_company_logos(db, force=False)
    db.close()

    assert summary.resolved == 1
    assert summary.unresolved == 1

    db = next(app.dependency_overrides[get_db]())
    openai = db.scalar(select(E.JobPosting).where(E.JobPosting.company == "OpenAI"))
    unknown = db.scalar(select(E.JobPosting).where(E.JobPosting.company == "Totally Unknown Startup ZZZ"))
    assert openai.company_domain == "openai.com"
    assert "openai.com" in openai.company_logo_url
    assert unknown.company_logo_url is None
    db.close()


def test_backfill_replaces_legacy_simplify_logo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = seed_job("Northrop Grumman")
    company_id = "9244244a-6c66-4bb4-83de-b4a2d4f98537"
    db = next(app.dependency_overrides[get_db]())
    job = db.get(E.JobPosting, job_id)
    job.raw_json = {"company_url": f"https://simplify.jobs/c/{company_id}/Northrop-Grumman"}
    job.company_logo_url = (
        f"https://storage.googleapis.com/simplify-imgs/companies/{company_id}/logo.png"
    )
    db.commit()

    monkeypatch.setattr(
        "app.jobs.backfill_company_logos.fetch_verified_company_domain",
        lambda *_args: "northropgrumman.com",
    )
    summary = backfill_company_logos(db, force=True)
    db.refresh(job)
    assert summary.resolved == 1
    assert job.company_domain == "northropgrumman.com"
    assert "northropgrumman.com" in job.company_logo_url
    assert "simplify-imgs" not in job.company_logo_url
    db.close()


def test_forced_backfill_clears_unverified_simplify_logo_without_replacement(
    client: TestClient,
) -> None:
    bad_logo = (
        "https://storage.googleapis.com/simplify-imgs/companies/"
        "49ecfd11-34f2-4b53-bf06-6f2f48f24429/logo.png"
    )
    job_id = seed_job("Unknown Mirror Company", logo=bad_logo)
    db = next(app.dependency_overrides[get_db]())

    backfill_company_logos(db, force=True)
    job = db.get(E.JobPosting, job_id)
    assert job.company_logo_url is None
    db.close()


def test_backfill_skips_existing_without_force(client: TestClient) -> None:
    seed_job("Acme", domain="acme.dev", logo="https://cdn.example.com/acme.png")
    db = next(app.dependency_overrides[get_db]())
    summary = backfill_company_logos(db, force=False)
    db.close()
    assert summary.already_present == 1
    # The company-level resolution still runs (using the job's own logo as a
    # catalog hint) — it's the per-job copy that's skipped without --force.
    assert summary.resolved == 1
