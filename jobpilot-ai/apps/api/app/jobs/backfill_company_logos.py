"""Backfill company_domain / company_logo_url on existing job postings.

Rows ingested before logo resolution existed (or before the columns were added)
have no branding, so their cards fall back to an initial. This one-off command
resolves a logo from the curated domain map / source catalog by normalized
company name and fills the columns in place.

Usage:
    python -m app.jobs.backfill_company_logos           # fill only missing logos
    python -m app.jobs.backfill_company_logos --force   # also refresh existing logos

Only companies we can confidently tie to a domain are updated; unknown companies
are left untouched (the frontend keeps showing their initial). Prints a summary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.company_logo_service import resolve_company_logo
from app.models.entities import JobPosting


@dataclass
class BackfillSummary:
    updated: int = 0
    skipped_unknown: int = 0
    skipped_existing: int = 0

    def __str__(self) -> str:
        return (
            f"Updated {self.updated} job postings with company domains/logos.\n"
            f"Skipped {self.skipped_unknown} unknown companies.\n"
            f"Skipped {self.skipped_existing} postings that already had a logo."
        )


def backfill_company_logos(db: Session, *, force: bool = False) -> BackfillSummary:
    summary = BackfillSummary()
    for job in db.scalars(select(JobPosting)).all():
        if job.company_logo_url and not force:
            summary.skipped_existing += 1
            continue
        resolved = resolve_company_logo(
            job.company or "",
            catalog_domain=job.company_domain or None,
        )
        if not resolved["company_logo_url"]:
            summary.skipped_unknown += 1
            continue
        job.company_domain = resolved["company_domain"] or None
        job.company_logo_url = resolved["company_logo_url"] or None
        summary.updated += 1
    db.commit()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill company logos on job postings.")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing logo URLs, not just missing ones."
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        summary = backfill_company_logos(db, force=args.force)
    finally:
        db.close()
    print(summary)


if __name__ == "__main__":
    main()
