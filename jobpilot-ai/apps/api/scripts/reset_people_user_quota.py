"""Zero one user's People discovery quota for a day.

Touches exactly one table — ``people_user_discovery_quota`` — and only the rows
for the named user. Provider-account budgets, discovery runs, recommendations,
contacts, and Redis are all left alone: this restores a person's ability to
search, it does not forgive provider spend.

    python -m scripts.reset_people_user_quota --user someone@example.com --dry-run
    python -m scripts.reset_people_user_quota --user someone@example.com --apply

Dry run is the default. Re-running after an apply is a no-op.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import PeopleUserDiscoveryQuota
from app.people.quota import _safe_reference, quota_day
from scripts.inspect_people_quota import resolve_user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="User id or email address.")
    parser.add_argument(
        "--date",
        help="ISO quota date to reset (defaults to today in the reset timezone).",
    )
    parser.add_argument(
        "--all-days",
        action="store_true",
        help="Reset every stored day for this user, not just one.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only (default).")
    parser.add_argument("--apply", action="store_true", help="Perform the reset.")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if not args.apply:
        args.dry_run = True

    session = SessionLocal()
    try:
        user = resolve_user(session, args.user)
        if user is None:
            print(json.dumps({"error": "user_not_found"}))
            raise SystemExit(1)

        statement = select(PeopleUserDiscoveryQuota).where(
            PeopleUserDiscoveryQuota.user_id == user.id
        )
        if not args.all_days:
            day = args.date or quota_day().isoformat()
            statement = statement.where(PeopleUserDiscoveryQuota.quota_date == day)
        rows = [row for row in session.scalars(statement) if row.discoveries_used]

        summary = {
            "user_ref": _safe_reference(user.id),
            "table": "people_user_discovery_quota",
            "mode": "dry_run" if args.dry_run else "apply",
            "matched": len(rows),
            "records": [
                {
                    "id": row.id,
                    "quota_date": row.quota_date,
                    "discoveries_used": row.discoveries_used,
                }
                for row in rows
            ],
            "untouched": [
                "people_provider_operation_usage",
                "people_discovery_runs",
                "job_people_candidates",
                "user_job_people_recommendations",
                "redis",
            ],
        }
        if args.dry_run:
            summary["reset"] = 0
            print(json.dumps(summary, indent=2, default=str))
            return

        for row in rows:
            row.discoveries_used = 0
            row.updated_at = datetime.now(UTC)
        session.commit()
        summary["reset"] = len(rows)
        print(json.dumps(summary, indent=2, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
