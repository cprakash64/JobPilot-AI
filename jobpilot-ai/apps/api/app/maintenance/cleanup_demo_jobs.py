"""Remove legacy fake/demo job data from the database.

Deletes any ``job_postings`` (and their dependent matches/tracker rows) plus
``job_sources`` that look like demo/placeholder data:

- company == "DemoCo" (or other known demo companies)
- job source type == "demo"
- application_url / source_url pointing at example.com, localhost, test.com, etc.

Run it directly against the configured database::

    python -m app.maintenance.cleanup_demo_jobs

It is also invoked by Alembic migration ``0003_remove_demo_jobs`` so a normal
``alembic upgrade head`` purges demo data automatically.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.jobs.job_normalization_service import DEMO_COMPANIES, PLACEHOLDER_HOSTS
from app.models.entities import (
    ApplicationTracker,
    JobMatch,
    JobPosting,
    JobSource,
)


def cleanup_demo_jobs(db: Session) -> dict[str, int]:
    """Delete demo/placeholder postings and sources. Returns counts removed."""
    demo_source_ids = [
        source.id
        for source in db.scalars(select(JobSource)).all()
        if (source.type or "").strip().lower() == "demo"
        or (source.name or "").strip().lower() in DEMO_COMPANIES
    ]

    url_clauses = []
    for host in PLACEHOLDER_HOSTS:
        url_clauses.append(JobPosting.application_url.ilike(f"%{host}%"))
        url_clauses.append(JobPosting.source_url.ilike(f"%{host}%"))

    conditions = [*url_clauses]
    for company in DEMO_COMPANIES:
        conditions.append(JobPosting.company.ilike(company))
    if demo_source_ids:
        conditions.append(JobPosting.source_id.in_(demo_source_ids))

    posting_ids = [
        row.id for row in db.scalars(select(JobPosting).where(or_(*conditions))).all()
    ]

    removed_matches = 0
    removed_tracker = 0
    if posting_ids:
        removed_matches = db.execute(
            delete(JobMatch).where(JobMatch.job_id.in_(posting_ids))
        ).rowcount or 0
        removed_tracker = db.execute(
            delete(ApplicationTracker).where(ApplicationTracker.job_id.in_(posting_ids))
        ).rowcount or 0
        db.execute(delete(JobPosting).where(JobPosting.id.in_(posting_ids)))

    removed_sources = 0
    if demo_source_ids:
        removed_sources = db.execute(
            delete(JobSource).where(JobSource.id.in_(demo_source_ids))
        ).rowcount or 0

    db.commit()
    return {
        "postings": len(posting_ids),
        "matches": removed_matches,
        "tracker": removed_tracker,
        "sources": removed_sources,
    }


def main() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        result = cleanup_demo_jobs(db)
    finally:
        db.close()
    print(
        "Removed demo data: "
        f"{result['postings']} postings, {result['matches']} matches, "
        f"{result['tracker']} tracker rows, {result['sources']} sources."
    )


if __name__ == "__main__":
    main()
