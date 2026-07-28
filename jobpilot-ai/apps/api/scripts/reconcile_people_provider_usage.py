"""Reconcile identifier-free provider call facts for a failed People run."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import PeopleDiscoveryRun
from app.people.provider_usage import (
    ProviderUsageContext,
    reconcile_unknown_operations,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--discovery-run-id", type=int)
    target.add_argument("--latest-failed", action="store_true")
    parser.add_argument("--search-calls", type=int, default=0)
    parser.add_argument("--bulk-calls", type=int, default=0)
    parser.add_argument("--single-calls", type=int, default=0)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        run = (
            db.scalar(
                select(PeopleDiscoveryRun)
                .where(
                    PeopleDiscoveryRun.provider == "apollo",
                    PeopleDiscoveryRun.status.in_(
                        {"provider_unavailable", "persistence_error"}
                    ),
                )
                .order_by(
                    PeopleDiscoveryRun.started_at.desc(),
                    PeopleDiscoveryRun.id.desc(),
                )
            )
            if args.latest_failed
            else db.get(PeopleDiscoveryRun, args.discovery_run_id)
        )
        if run is None or run.status not in {
            "provider_unavailable",
            "persistence_error",
        }:
            raise SystemExit("A failed People discovery run is required.")
        adapter_version = str(
            (run.company_context or {}).get(
                "provider_adapter_version",
                "provider-neutral-v1",
            )
        )
        inserted = reconcile_unknown_operations(
            context=ProviderUsageContext(
                user_id=run.user_id,
                job_id=run.job_id,
                discovery_run_id=run.id,
                adapter_version=adapter_version,
            ),
            provider=run.provider,
            operation_counts={
                "people_search": max(0, args.search_calls),
                "bulk_enrichment": max(0, args.bulk_calls),
                "single_person_enrichment": max(0, args.single_calls),
            },
            safe_http_outcomes={
                "people_search": "http_200",
                "bulk_enrichment": "http_422",
                "single_person_enrichment": "outcome_unknown",
            },
        )
        print(json.dumps({
            "inserted_operations": inserted,
            "credits_known": False,
        }))
    finally:
        db.close()


if __name__ == "__main__":
    main()
