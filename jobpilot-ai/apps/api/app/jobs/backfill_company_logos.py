"""Backfill company_domain / company_logo_url on existing job postings, using
the persisted CompanyBranding pipeline (one resolution per employer, reused
across every job row from that employer — never a synchronous per-job-view
resolution in a browser request).

Rows ingested before logo resolution existed (or before the columns were
added) have no branding, so their cards fall back to the neutral placeholder.
This command resolves each DISTINCT company at most once (idempotent — a
company already resolved is skipped unless --force), then copies the result
onto every job row for that company.

Usage:
    python -m app.jobs.backfill_company_logos           # fill only missing logos
    python -m app.jobs.backfill_company_logos --force   # re-resolve every company
    python -m app.jobs.backfill_company_logos --company "Example Corp" \
        --discover-official --force

Only companies we can confidently tie to a domain are updated; unknown
companies are left for the neutral placeholder. Prints a summary of
resolved / already-present / unresolved / failed counts.
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.company_logo_service import (
    get_or_create_company_branding,
    is_untrusted_simplify_logo_url,
    refresh_official_company_logo,
)
from app.jobs.sources.simplifyjobs import fetch_verified_company_domain
from app.models.entities import JobPosting

logger = logging.getLogger("jobpilot.logos")

# Best-effort politeness limit on external favicon lookups per run — this is
# a one-off/cron command, never invoked from a request path, so a small delay
# per NEW resolution is a reasonable trade for not hammering an external
# endpoint across hundreds of companies in one run.
RATE_LIMIT_SECONDS = 0.05
PROFILE_FETCH_CONCURRENCY = 8


@dataclass
class BackfillSummary:
    resolved: int = 0
    already_present: int = 0
    unresolved: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"Resolved {self.resolved} compan{'y' if self.resolved == 1 else 'ies'}.\n"
            f"Already present: {self.already_present}.\n"
            f"Unresolved (no known domain — neutral placeholder shown): {self.unresolved}.\n"
            f"Failed: {self.failed}."
        )


def backfill_company_logos(
    db: Session,
    *,
    force: bool = False,
    company_names: set[str] | None = None,
    discover_official: bool = False,
) -> BackfillSummary:
    summary = BackfillSummary()
    companies = sorted({c for (c,) in db.execute(select(JobPosting.company)).all() if c})
    if company_names is not None:
        companies = [company for company in companies if company in company_names]
    simplify_urls: dict[str, object] = {}
    for company, raw_json in db.execute(
        select(JobPosting.company, JobPosting.raw_json).where(
            JobPosting.raw_json.is_not(None)
        )
    ).all():
        if (
            company
            and company not in simplify_urls
            and isinstance(raw_json, dict)
            and raw_json.get("company_url")
        ):
            simplify_urls[company] = raw_json["company_url"]
    if company_names is not None:
        simplify_urls = {
            company: url
            for company, url in simplify_urls.items()
            if company in company_names
        }

    # Profile pages are independent fixed-host reads. Resolve them concurrently
    # before touching ORM state; workers never share the SQLAlchemy session.
    verified_domains: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=PROFILE_FETCH_CONCURRENCY) as pool:
        pending = {
            pool.submit(fetch_verified_company_domain, url, company): company
            for company, url in simplify_urls.items()
        }
        for future in as_completed(pending):
            company = pending[future]
            try:
                verified_domains[company] = future.result()
            except Exception:  # noqa: BLE001 - one profile cannot abort the backfill
                logger.info("Simplify profile enrichment failed for company=%s", company)
                verified_domains[company] = ""

    resolved_by_company: dict[str, tuple[str | None, str | None]] = {}
    for company in companies:
        # A job row already carrying a domain/logo (from an earlier ingest,
        # before persisted CompanyBranding existed) is a legitimate catalog
        # hint for this company's first resolution.
        hint = db.scalar(
            select(JobPosting).where(
                (JobPosting.company == company) & (JobPosting.company_logo_url.is_not(None))
            )
        )
        application_hint = db.scalar(
            select(JobPosting.application_url).where(
                (JobPosting.company == company) & (JobPosting.application_url.is_not(None))
            )
        )
        simplify_company_url = simplify_urls.get(company)
        verified_domain = verified_domains.get(company, "")
        stored_logo = hint.company_logo_url if hint else None
        if is_untrusted_simplify_logo_url(stored_logo):
            stored_logo = None
        try:
            branding = get_or_create_company_branding(
                db, company,
                catalog_domain=verified_domain or (hint.company_domain if hint else None),
                catalog_logo_url=stored_logo,
                application_url=application_hint,
                source_type="simplifyjobs" if simplify_company_url else None,
            )
            if discover_official:
                branding = refresh_official_company_logo(
                    db, branding, force=force
                )
        except Exception:  # noqa: BLE001 - never let one bad company abort the whole run
            logger.exception("logo backfill failed for company=%s", company)
            summary.failed += 1
            continue
        resolved_by_company[company] = (branding.domain, branding.logo_url)
        if branding.resolution_status == "resolved":
            summary.resolved += 1
        else:
            summary.unresolved += 1
        time.sleep(RATE_LIMIT_SECONDS)
    db.commit()

    # Copy each company's resolved branding onto every job row for that
    # company — idempotent (a row already matching is simply rewritten to the
    # same value) and safe to rerun.
    jobs_statement = select(JobPosting)
    if company_names is not None:
        jobs_statement = jobs_statement.where(JobPosting.company.in_(company_names))
    jobs = db.scalars(jobs_statement).all()
    for job in jobs:
        domain, logo_url = resolved_by_company.get(job.company or "", (None, None))
        if force:
            # Force also clears a legacy bad mirror URL when no verified
            # replacement exists. The UI's neutral fallback is preferable to
            # confidently displaying another company's logo.
            job.company_domain = domain
            job.company_logo_url = logo_url
            continue
        if job.company_logo_url and not force:
            summary.already_present += 1
            continue
        if logo_url:
            job.company_domain = domain
            job.company_logo_url = logo_url
    db.commit()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill company logos on job postings.")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing logo URLs, not just missing ones."
    )
    parser.add_argument(
        "--company",
        action="append",
        default=None,
        help="Limit resolution to this exact company name; may be supplied more than once.",
    )
    parser.add_argument(
        "--discover-official",
        action="store_true",
        help="Inspect the canonical official site once for a verified raster logo.",
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        summary = backfill_company_logos(
            db,
            force=args.force,
            company_names=set(args.company) if args.company else None,
            discover_official=args.discover_official,
        )
    finally:
        db.close()
    print(summary)


if __name__ == "__main__":
    main()
