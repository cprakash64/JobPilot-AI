"""Persisted company-branding pipeline: reuse across jobs, backfill
idempotency, and the SSRF-safe fetcher used by the logo proxy."""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.jobs.backfill_company_logos import backfill_company_logos
from app.jobs.company_logo_service import get_or_create_company_branding, normalize_company_key
from app.jobs.safe_fetch import UnsafeUrlError, _validate_url
from app.models.entities import CompanyBranding, JobPosting, JobSource


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_job(db: Session, *, company: str, external_id: str, logo_url: str | None = None) -> JobPosting:
    source = db.scalar(select(JobSource).where(JobSource.name == "TestSource"))
    if source is None:
        source = JobSource(name="TestSource", type="greenhouse", base_url="https://example.com", enabled=True, supports_api=True)
        db.add(source)
        db.flush()
    job = JobPosting(
        source_id=source.id, external_id=external_id, title="Engineer", company=company,
        company_logo_url=logo_url, location="Remote", application_url=f"https://example.com/{external_id}",
        source_url=f"https://example.com/{external_id}", hash_for_deduplication=f"h-{external_id}",
    )
    db.add(job)
    db.commit()
    return job


class TestCompanyBrandingReuse:
    def test_two_jobs_from_the_same_company_reuse_one_branding_row(self, db: Session):
        get_or_create_company_branding(db, "Upstart")
        get_or_create_company_branding(db, "Upstart, Inc.")  # same company, different raw name
        db.commit()
        rows = db.scalars(select(CompanyBranding).where(CompanyBranding.normalized_key == normalize_company_key("Upstart"))).all()
        assert len(rows) == 1
        assert rows[0].logo_url and "upstart.com" in rows[0].logo_url

    def test_upstart_and_taskrabbit_now_resolve_a_real_logo(self, db: Session):
        # Regression for the reported bug: these two specifically showed
        # initials instead of a real logo.
        for company, domain in [("Upstart", "upstart.com"), ("TaskRabbit", "taskrabbit.com")]:
            branding = get_or_create_company_branding(db, company)
            assert branding.resolution_status == "resolved"
            assert branding.domain == domain
            assert branding.logo_url

    def test_a_resolved_branding_row_is_not_re_resolved_on_a_plain_reuse_call(self, db: Session):
        first = get_or_create_company_branding(db, "Upstart")
        db.commit()
        first_verified_at = first.last_verified_at
        second = get_or_create_company_branding(db, "Upstart")
        assert second.id == first.id
        assert second.last_verified_at == first_verified_at


class TestBackfillIdempotency:
    def test_backfill_resolves_previously_unbranded_jobs_and_is_safe_to_rerun(self, db: Session):
        make_job(db, company="Upstart", external_id="up-1")
        make_job(db, company="Upstart", external_id="up-2")
        make_job(db, company="Totally Unknown Startup XYZ", external_id="unk-1")

        first_run = backfill_company_logos(db)
        assert first_run.resolved == 1  # one distinct known company (Upstart)
        assert first_run.unresolved == 1  # the unknown company

        jobs = db.scalars(select(JobPosting)).all()
        upstart_jobs = [j for j in jobs if j.company == "Upstart"]
        assert all(j.company_logo_url and "upstart.com" in j.company_logo_url for j in upstart_jobs)

        # Idempotent: rerunning does not change already-branded rows or error.
        second_run = backfill_company_logos(db)
        assert second_run.already_present == len(upstart_jobs)
        jobs_after = db.scalars(select(JobPosting)).all()
        assert [j.company_logo_url for j in jobs_after] == [j.company_logo_url for j in jobs]


class TestSafeFetchSSRF:
    def test_rejects_loopback_target(self):
        # 127.0.0.1 is a literal IP, so this needs no DNS and stays hermetic.
        with pytest.raises(UnsafeUrlError):
            _validate_url("http://127.0.0.1/evil.png")

    def test_rejects_link_local_cloud_metadata_target(self):
        with pytest.raises(UnsafeUrlError):
            _validate_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_private_network_target(self):
        with pytest.raises(UnsafeUrlError):
            _validate_url("http://10.0.0.5/internal")

    def test_rejects_unsupported_scheme(self):
        with pytest.raises(UnsafeUrlError):
            _validate_url("file:///etc/passwd")

    def test_accepts_a_public_ip_target(self, monkeypatch):
        # Stub DNS so this stays hermetic (no real network call) while still
        # exercising the public-IP acceptance path.
        monkeypatch.setattr(
            "app.jobs.safe_fetch.socket.getaddrinfo",
            lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))],
        )
        _validate_url("https://example.com/logo.png")
