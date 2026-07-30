"""Show one user's People quota alongside the provider calls it actually cost.

The two numbers are deliberately separate. ``user_discoveries`` counts
deliberate actions; ``provider_calls`` counts external PDL/Apollo requests,
whose credit units are measured per record returned. Seeing them side by side is
what makes a "limit reached too early" report diagnosable.

    python -m scripts.inspect_people_quota --user someone@example.com

Prints a hashed user reference rather than the email, and never prints contact
records or credentials.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.entities import (
    PeopleDiscoveryRun,
    PeopleProviderOperationUsage,
    PeopleUserDiscoveryQuota,
    User,
)
from app.people.quota import (
    _safe_reference,
    day_bounds,
    quota_day,
    quota_snapshot,
    reset_timezone,
)


def resolve_user(session, value: str) -> User | None:
    if value.isdigit():
        found = session.get(User, int(value))
        if found is not None:
            return found
    return session.scalar(select(User).where(func.lower(User.email) == value.lower()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="User id or email address.")
    parser.add_argument("--runs", type=int, default=10, help="Recent runs to list.")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        user = resolve_user(session, args.user)
        if user is None:
            print(json.dumps({"error": "user_not_found"}))
            raise SystemExit(1)

        snapshot = quota_snapshot(session, user)
        day = quota_day()
        # A half-open timestamp range rather than date() casting, so the
        # comparison stays typed and index-friendly on Postgres.
        window_start, window_end = day_bounds(day)

        provider_rows = session.execute(
            select(
                PeopleProviderOperationUsage.provider,
                PeopleProviderOperationUsage.operation_type,
                func.count().label("calls"),
                func.coalesce(func.sum(PeopleProviderOperationUsage.budget_units), 0),
            )
            .where(
                PeopleProviderOperationUsage.user_id == user.id,
                PeopleProviderOperationUsage.occurred_at >= window_start,
                PeopleProviderOperationUsage.occurred_at < window_end,
            )
            .group_by(
                PeopleProviderOperationUsage.provider,
                PeopleProviderOperationUsage.operation_type,
            )
        ).all()

        runs = session.scalars(
            select(PeopleDiscoveryRun)
            .where(PeopleDiscoveryRun.user_id == user.id)
            .order_by(PeopleDiscoveryRun.id.desc())
            .limit(max(1, args.runs))
        ).all()

        quota_rows = session.scalars(
            select(PeopleUserDiscoveryQuota)
            .where(PeopleUserDiscoveryQuota.user_id == user.id)
            .order_by(PeopleUserDiscoveryQuota.quota_date.desc())
            .limit(7)
        ).all()

        cache_hits = sum(1 for run in runs if run.cache_hit or run.provider == "cache")
        print(
            json.dumps(
                {
                    "user_ref": _safe_reference(user.id),
                    "internal_account": snapshot.is_internal,
                    "quota_date": day.isoformat(),
                    "reset_timezone": str(reset_timezone()),
                    "user_quota": {
                        "daily_limit": snapshot.daily_limit,
                        "daily_used": snapshot.used,
                        "daily_remaining": snapshot.remaining,
                        "resets_at": snapshot.resets_at.isoformat(),
                        "hourly_limit": snapshot.hourly_limit,
                    },
                    "provider_calls_today": [
                        {
                            "provider": provider,
                            "operation": operation,
                            "calls": int(calls),
                            "credit_units": int(units),
                        }
                        for provider, operation, calls, units in provider_rows
                    ],
                    "provider_calls_total": sum(int(row[2]) for row in provider_rows),
                    "provider_credit_units_total": sum(
                        int(row[3]) for row in provider_rows
                    ),
                    "recent_runs": [
                        {
                            "id": run.id,
                            "job_id": run.job_id,
                            "status": run.status,
                            "provider": run.provider,
                            "cache_hit": bool(run.cache_hit),
                            # A cache-served run never charges a user unit.
                            "user_quota_charged": not (
                                run.cache_hit or run.provider == "cache"
                            ),
                            "started_at": run.started_at.isoformat()
                            if run.started_at
                            else None,
                        }
                        for run in runs
                    ],
                    "recent_cache_hits": cache_hits,
                    "quota_history": [
                        {"date": row.quota_date, "used": row.discoveries_used}
                        for row in quota_rows
                    ],
                },
                indent=2,
                default=str,
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
