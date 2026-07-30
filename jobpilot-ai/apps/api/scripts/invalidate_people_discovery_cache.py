"""Retire People discovery runs recorded under obsolete provider semantics.

Versioning already stops legacy runs from being *served*: a run without the
current ``search_contract_version`` is treated as stale and the user is offered
a fresh search. This command exists for the operator who wants those rows
explicitly retired rather than merely ignored — after a provider-semantics
change, or to clear a specific company or job.

Dry run is the default and prints counts and a small sample without writing.

    python -m scripts.invalidate_people_discovery_cache --provider pdl --legacy-only --dry-run
    python -m scripts.invalidate_people_discovery_cache --provider pdl --legacy-only

Rows are *marked* incompatible, never deleted: the run history stays auditable,
and the discovery path already refuses to reuse them. Nothing outside
``people_discovery_runs`` is touched — no jobs, contacts, applications, or
Redis keys — and re-running is a no-op.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.entities import JobPosting, PeopleDiscoveryRun
from app.people.observability import metric
from app.people.service import (
    CONTRACT_VERSION_KEY,
    PEOPLE_SEARCH_CONTRACT_VERSION,
    run_contract_version,
)

# Marker written onto retired rows. Its presence is what makes the command
# idempotent: an already-retired row is never selected again.
INVALIDATED_KEY = "contract_invalidated_at"
INVALIDATED_STATUS = "superseded_contract"

# Failure codes whose *meaning* changed with the PDL 404 correction. A stored
# "provider_request_invalid" from before that change may well have been a
# perfectly valid query that simply matched nobody.
LEGACY_FAILURE_CODES = frozenset(
    {"provider_request_invalid", "provider_route_invalid", "provider_unavailable"}
)


def _selected(session, args) -> list[PeopleDiscoveryRun]:
    statement = select(PeopleDiscoveryRun)
    if args.provider:
        statement = statement.where(PeopleDiscoveryRun.provider == args.provider)
    if args.job_id:
        statement = statement.where(PeopleDiscoveryRun.job_id == args.job_id)
    if args.failure_code:
        statement = statement.where(PeopleDiscoveryRun.failure_code == args.failure_code)
    if args.created_before:
        statement = statement.where(
            PeopleDiscoveryRun.started_at < _parse_date(args.created_before)
        )
    if args.company:
        statement = statement.where(
            PeopleDiscoveryRun.job_id.in_(
                select(JobPosting.id).where(
                    func.lower(JobPosting.company).like(f"%{args.company.lower()}%")
                )
            )
        )
    rows = list(session.scalars(statement))

    def keep(run: PeopleDiscoveryRun) -> bool:
        context = run.company_context or {}
        if context.get(INVALIDATED_KEY):
            return False  # already retired; keeps the command idempotent
        version = run_contract_version(run)
        if args.before_version:
            return version != args.before_version
        if args.legacy_only:
            # Legacy means "not produced under the contract in force now".
            return version != PEOPLE_SEARCH_CONTRACT_VERSION
        return True

    return [run for run in rows if keep(run)]


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _summarize(rows: list[PeopleDiscoveryRun]) -> dict[str, object]:
    by_status: dict[str, int] = {}
    by_failure: dict[str, int] = {}
    by_version: dict[str, int] = {}
    for run in rows:
        by_status[run.status] = by_status.get(run.status, 0) + 1
        code = run.failure_code or "none"
        by_failure[code] = by_failure.get(code, 0) + 1
        version = run_contract_version(run) or "legacy_unversioned"
        by_version[version] = by_version.get(version, 0) + 1
    return {
        "matched": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_failure_code": dict(sorted(by_failure.items())),
        "by_contract_version": dict(sorted(by_version.items())),
        "reinterpreted_failure_codes": sum(
            1 for run in rows if (run.failure_code or "") in LEGACY_FAILURE_CODES
        ),
        "sample_run_ids": [run.id for run in rows[:20]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", help="Limit to one provider, e.g. pdl.")
    parser.add_argument(
        "--legacy-only",
        action="store_true",
        help="Only runs not recorded under the current contract version.",
    )
    parser.add_argument("--company", help="Substring match on the job's company name.")
    parser.add_argument("--job-id", type=int, help="Limit to one job.")
    parser.add_argument(
        "--before-version",
        help="Only runs whose contract version differs from this value.",
    )
    parser.add_argument("--failure-code", help="Limit to one stored failure code.")
    parser.add_argument(
        "--created-before",
        help="ISO date/time; only runs started before it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (the safe default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mark the matched runs as superseded.",
    )
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        # Neither flag given: behave as a dry run rather than guessing.
        args.dry_run = True
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    session = SessionLocal()
    try:
        rows = _selected(session, args)
        summary = _summarize(rows)
        summary["current_contract_version"] = PEOPLE_SEARCH_CONTRACT_VERSION
        summary["mode"] = "dry_run" if args.dry_run else "apply"
        if args.dry_run:
            summary["invalidated"] = 0
            print(json.dumps(summary, indent=2, default=str))
            return

        now = datetime.now(UTC).isoformat()
        for run in rows:
            context = dict(run.company_context or {})
            context[INVALIDATED_KEY] = now
            context["contract_invalidated_from"] = (
                run_contract_version(run) or "legacy_unversioned"
            )
            context["contract_invalidated_to"] = PEOPLE_SEARCH_CONTRACT_VERSION
            # Retiring the recorded contract is what makes the run unusable to
            # the reuse gates; the row itself is preserved for audit.
            context.pop(CONTRACT_VERSION_KEY, None)
            run.company_context = context
            if run.status not in {"complete", "partial"}:
                # A failure recorded under obsolete semantics should not keep
                # its old status either.
                run.status = INVALIDATED_STATUS
                run.safe_failure_message = None
        session.commit()
        metric(
            "people_legacy_cache_invalidations_total",
            len(rows),
            provider=args.provider or "all",
            status="applied",
        )
        summary["invalidated"] = len(rows)
        print(json.dumps(summary, indent=2, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
