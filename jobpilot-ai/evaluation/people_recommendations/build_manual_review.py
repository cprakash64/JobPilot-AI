#!/usr/bin/env python3
"""Build a safe, manually labelable 20-job People review report.

This reads persisted JobPilot data only. It never invokes a provider and omits
names, emails, profile URLs, provider IDs, and raw payloads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from app.models.entities import (  # noqa: E402
    JobPeopleCandidate,
    JobPosting,
    PeopleDiscoveryRun,
    ProfessionalPerson,
)


def build(limit: int) -> dict:
    with SessionLocal() as db:
        # Put jobs with persisted discovery evidence first, then fill the sample
        # with recent active jobs. This keeps the report useful without creating
        # new provider traffic merely to populate an evaluation artifact.
        run_job_ids = list(
            db.scalars(
                select(PeopleDiscoveryRun.job_id)
                .join(JobPosting, PeopleDiscoveryRun.job_id == JobPosting.id)
                .where(JobPosting.is_active.is_(True))
                .order_by(PeopleDiscoveryRun.started_at.desc())
            ).unique()
        )
        jobs = [job for job_id in run_job_ids if (job := db.get(JobPosting, job_id))]
        seen_job_ids = {job.id for job in jobs}
        recent_jobs = list(
            db.scalars(
                select(JobPosting)
                .where(JobPosting.is_active.is_(True))
                .order_by(JobPosting.discovered_at.desc())
            )
        )
        jobs.extend(job for job in recent_jobs if job.id not in seen_job_ids)
        jobs = jobs[:limit]
        cases = []
        for job in jobs:
            run = db.scalar(
                select(PeopleDiscoveryRun)
                .where(PeopleDiscoveryRun.job_id == job.id)
                .order_by(PeopleDiscoveryRun.started_at.desc())
            )
            displayed = db.execute(
                select(JobPeopleCandidate, ProfessionalPerson)
                .join(ProfessionalPerson, JobPeopleCandidate.person_id == ProfessionalPerson.id)
                .where(JobPeopleCandidate.job_id == job.id)
                .order_by(JobPeopleCandidate.category_score.desc())
            ).all()
            candidates = {
                "recruiter": [],
                "manager": [],
                "referral": [],
            }
            category_map = {
                "likely_recruiter": "recruiter",
                "potential_hiring_manager": "manager",
                "potential_referrer": "referral",
            }
            for candidate, person in displayed:
                candidates[category_map[candidate.candidate_category]].append({
                    "candidate_id": candidate.id,
                "current_title": person.current_title,
                "current_company": person.current_company_name,
                "scoring_version": candidate.scoring_version,
                "relevance_score": candidate.category_score,
                    "data_confidence": candidate.data_confidence,
                    "limitations": candidate.recommendation_limitations,
                    "human_relevance_label": None,
                    "human_current_employment_label": None,
                    "human_parent_subsidiary_mismatch": None,
                    "review_notes": "",
                })
            cases.append({
                "job_id": job.id,
                "job_title": job.title,
                "company": job.company,
                "company_domain": job.company_domain,
                "discovery_run_id": run.id if run else None,
                "company_context": run.company_context if run else {},
                "category_funnels": run.category_diagnostics if run else {},
                "credits_consumed": run.provider_credits_used if run else 0,
                "candidates": candidates,
                "review_status": "unreviewed",
            })
    return {
        "schema_version": "manual-people-review-v1",
        "case_count": len(cases),
        "contains_live_accuracy_claim": False,
        "coverage_note": (
            "Existing persisted discovery runs are listed first. Jobs without a run have empty "
            "funnels because this offline report never invokes a provider."
        ),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "results" / "manual-review-latest.json",
    )
    args = parser.parse_args()
    if args.limit < 20:
        raise SystemExit("--limit must be at least 20")
    result = build(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {result['case_count']} safe, unreviewed cases to {args.output}")
    return 0 if result["case_count"] >= 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
