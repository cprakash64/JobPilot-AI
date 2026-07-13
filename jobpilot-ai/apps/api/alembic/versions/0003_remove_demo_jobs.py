"""remove demo/placeholder job data

Purges any legacy demo jobs (DemoCo, source type "demo", example.com URLs) that
were seeded by the removed "Ingest demo jobs" flow, along with their matches and
tracker rows. This is a data-only migration and is safe to re-run.

Revision ID: 0003_remove_demo_jobs
Revises: 0002_job_discovery
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_remove_demo_jobs"
down_revision: str | None = "0002_job_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLACEHOLDER_HOSTS = ("example.com", "example.org", "localhost", "127.0.0.1", "test.com", "demo.com")


def upgrade() -> None:
    bind = op.get_bind()

    url_predicate = " OR ".join(
        f"application_url ILIKE '%{host}%' OR source_url ILIKE '%{host}%'"
        for host in _PLACEHOLDER_HOSTS
    )
    demo_posting_filter = (
        "SELECT id FROM job_postings WHERE "
        "LOWER(company) = 'democo' "
        f"OR {url_predicate} "
        "OR source_id IN (SELECT id FROM job_sources WHERE LOWER(type) = 'demo')"
    )

    op.execute(f"DELETE FROM job_matches WHERE job_id IN ({demo_posting_filter})")
    op.execute(f"DELETE FROM application_tracker WHERE job_id IN ({demo_posting_filter})")
    op.execute(f"DELETE FROM job_postings WHERE id IN ({demo_posting_filter})")
    op.execute("DELETE FROM job_sources WHERE LOWER(type) = 'demo' OR LOWER(name) = 'democo'")

    _ = bind  # bind is available for future dialect-specific tweaks


def downgrade() -> None:
    # Demo data is intentionally not restorable.
    pass
