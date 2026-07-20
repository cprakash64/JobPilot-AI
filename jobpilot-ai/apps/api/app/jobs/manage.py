"""Admin CLI for ingestion & scoring operations (Part 11).

    python -m app.jobs.manage run-all            # ingest every enabled source now
    python -m app.jobs.manage run-ats greenhouse # ingest one ATS provider
    python -m app.jobs.manage run-company stripe  # ingest one company slug
    python -m app.jobs.manage validate-registry  # verify the source catalog live
    python -m app.jobs.manage runs [--limit N]    # show recent ingestion runs
    python -m app.jobs.manage backfill [...]       # delegate to backfill_scores

These operate on the same code paths as the scheduled worker, under the same
single-run distributed lock, so an admin trigger can never race the daily run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.db.session import SessionLocal
from app.jobs.scheduled_ingestion import recent_runs, run_daily_ingestion
from app.models.entities import IngestionTrigger


def _run_ingestion(*, provider: str | None = None, company: str | None = None) -> int:
    """Ingest all sources, or narrow to one provider/company via env-independent
    filtering of the built adapter list."""
    from app.jobs import scheduled_ingestion
    from app.jobs.source_registry import build_adapters

    original = scheduled_ingestion.build_adapters

    def filtered(limit=None, tags=None):
        adapters = build_adapters(limit=limit, tags=tags)
        if provider:
            adapters = [a for a in adapters if a.source_type == provider]
        if company:
            adapters = [a for a in adapters if a.company_slug.lower() == company.lower()]
        return adapters

    scheduled_ingestion.build_adapters = filtered  # type: ignore[assignment]
    db = SessionLocal()
    try:
        outcome = run_daily_ingestion(db, trigger=IngestionTrigger.manual.value)
        print(json.dumps(
            {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "jobs_inserted": outcome.jobs_inserted,
                "jobs_updated": outcome.jobs_updated,
                "jobs_expired": outcome.jobs_expired,
                "scoring_tasks_queued": outcome.scoring_tasks_queued,
            },
            indent=2,
        ))
        return 0 if outcome.status not in ("failed",) else 1
    finally:
        scheduled_ingestion.build_adapters = original  # type: ignore[assignment]
        db.close()


def _validate_registry(provider: str | None, limit: int | None) -> int:
    from app.jobs.verify_sources import _summary, verify

    checks = asyncio.run(verify(provider, limit))
    summary = _summary(checks)
    print(json.dumps({k: v for k, v in summary.items() if k != "broken"}, indent=2))
    for line in summary.get("broken", []):
        print(f"  broken: {line}")
    return 0


def _show_runs(limit: int) -> int:
    db = SessionLocal()
    try:
        print(json.dumps(recent_runs(db, limit=limit), indent=2, default=str))
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run-all", help="Ingest every enabled source now")
    p_ats = sub.add_parser("run-ats", help="Ingest a single ATS provider")
    p_ats.add_argument("provider")
    p_co = sub.add_parser("run-company", help="Ingest a single company slug")
    p_co.add_argument("company")
    p_val = sub.add_parser("validate-registry", help="Verify sources against live ATS endpoints")
    p_val.add_argument("--source", default=None)
    p_val.add_argument("--limit", type=int, default=None)
    p_runs = sub.add_parser("runs", help="Show recent ingestion runs")
    p_runs.add_argument("--limit", type=int, default=20)
    sub.add_parser("backfill", help="Backfill scores (see app.jobs.backfill_scores)")

    args, rest = parser.parse_known_args(argv)

    if args.command == "run-all":
        return _run_ingestion()
    if args.command == "run-ats":
        return _run_ingestion(provider=args.provider)
    if args.command == "run-company":
        return _run_ingestion(company=args.company)
    if args.command == "validate-registry":
        return _validate_registry(args.source, args.limit)
    if args.command == "runs":
        return _show_runs(args.limit)
    if args.command == "backfill":
        from app.jobs.backfill_scores import main as backfill_main

        return backfill_main(rest)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
