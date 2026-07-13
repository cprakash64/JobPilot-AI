from datetime import UTC, datetime, timedelta

from app.job_sources.base import NormalizedJob


def test_normalized_job_hash_is_stable():
    job = NormalizedJob(
        external_id="1",
        title="Engineer",
        company="Demo",
        location="Remote",
        remote_type="remote",
        employment_type="full-time",
        seniority_level="junior",
        posted_at=datetime.now(UTC) - timedelta(days=1),
        application_url="https://example.com/apply",
        source_url="https://example.com/job",
        description_raw="Build things",
        description_clean="Build things",
    )
    assert len(job.dedupe_hash) == 64
    assert job.dedupe_hash == job.dedupe_hash

