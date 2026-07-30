"""Per-user People discovery quota, counted in deliberate user actions.

There are three independent controls, and conflating them is what produced the
original defect:

``user discovery quota`` (this module)
    One unit per explicit "Find people" or "Broaden search". A single action
    stays one unit no matter how many company-enrichment calls, category
    searches, ladder rungs, or fallback attempts it performs internally.

``provider credit budget`` (:mod:`app.people.provider_usage`)
    Every external PDL/Apollo request, measured in provider credit units — one
    per record returned for a PDL search. This is a cost control, not a user
    entitlement, and its exhaustion has its own message.

``burst rate limit`` (``rate_limit`` in :mod:`app.people.service`)
    Explicit requests per hour. A burst rejection never consumes daily quota.

The user's limit was previously charged against the provider ledger, so a
single search returning 20 records cost 20 units. Measured on production data,
14 user actions consumed 85 of 100 units.

Reservation is atomic: the day's row is locked with ``SELECT ... FOR UPDATE``
before the count is read, so two concurrent discoveries cannot both see the
same remaining value. Failures that happen before meaningful provider work
refund the reservation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import PeopleUserDiscoveryQuota, User
from app.people.feature_flags import is_internal
from app.people.observability import metric

logger = logging.getLogger("jobpilot.people.quota")


def reset_timezone() -> ZoneInfo:
    """The timezone whose midnight resets the daily quota."""

    name = (settings.people_quota_reset_timezone or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("people_quota_invalid_timezone name=%s falling_back=UTC", name)
        return ZoneInfo("UTC")


def quota_day(now: datetime | None = None) -> date:
    moment = (now or datetime.now(UTC)).astimezone(reset_timezone())
    return moment.date()


def next_reset(now: datetime | None = None) -> datetime:
    """Midnight in the reset timezone, expressed in UTC."""

    zone = reset_timezone()
    moment = (now or datetime.now(UTC)).astimezone(zone)
    tomorrow = (moment + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=zone).astimezone(UTC)


def day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """Half-open UTC window covering one quota day in the reset timezone."""

    zone = reset_timezone()
    target = day or quota_day()
    start = datetime.combine(target, datetime.min.time(), tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def daily_limit_for(user: User) -> int:
    """Allowlisted internal accounts get the higher testing allowance."""

    if is_internal(user):
        return max(
            settings.people_internal_user_daily_discovery_limit,
            settings.people_user_daily_discovery_limit,
        )
    return settings.people_user_daily_discovery_limit


@dataclass(frozen=True)
class QuotaSnapshot:
    """What the UI needs to explain the user's remaining allowance."""

    daily_limit: int
    used: int
    remaining: int
    resets_at: datetime
    hourly_limit: int
    is_internal: bool

    def as_payload(self) -> dict[str, object]:
        return {
            "daily_limit": self.daily_limit,
            "daily_used": self.used,
            "daily_remaining": self.remaining,
            "resets_at": self.resets_at.isoformat(),
            "hourly_limit": self.hourly_limit,
            "broadened_search_cost": 1,
        }


def user_discoveries_used(db: Session, user_id: int, *, on: date | None = None) -> int:
    """Actions charged to this user for the given quota day."""

    return int(
        db.scalar(
            select(func.coalesce(PeopleUserDiscoveryQuota.discoveries_used, 0)).where(
                PeopleUserDiscoveryQuota.user_id == user_id,
                PeopleUserDiscoveryQuota.quota_date == (on or quota_day()).isoformat(),
            )
        )
        or 0
    )


def quota_snapshot(db: Session, user: User) -> QuotaSnapshot:
    limit = daily_limit_for(user)
    used = user_discoveries_used(db, user.id)
    return QuotaSnapshot(
        daily_limit=limit,
        used=used,
        remaining=max(0, limit - used) if limit else 0,
        resets_at=next_reset(),
        hourly_limit=settings.people_discovery_rate_limit_per_hour,
        is_internal=is_internal(user),
    )


def _locked_row(db: Session, user_id: int, day: date) -> PeopleUserDiscoveryQuota:
    """Fetch-or-create the day's row, locked against concurrent reservations."""

    statement = select(PeopleUserDiscoveryQuota).where(
        PeopleUserDiscoveryQuota.user_id == user_id,
        PeopleUserDiscoveryQuota.quota_date == day.isoformat(),
    )
    try:
        # SQLite ignores FOR UPDATE; Postgres serializes concurrent reservations
        # on this row, which is what stops two requests reading the same
        # remaining count.
        row = db.scalar(statement.with_for_update())
    except Exception:
        row = db.scalar(statement)
    if row is None:
        row = PeopleUserDiscoveryQuota(
            user_id=user_id, quota_date=day.isoformat(), discoveries_used=0
        )
        db.add(row)
        db.flush()
    return row


@dataclass
class QuotaReservation:
    """A held unit. Commit keeps it; refund gives it back."""

    user_id: int
    quota_date: date
    charged: bool = True

    def refund(self, db: Session, *, reason: str) -> None:
        """Return the unit when no meaningful provider work happened."""

        if not self.charged:
            return
        row = _locked_row(db, self.user_id, self.quota_date)
        row.discoveries_used = max(0, int(row.discoveries_used or 0) - 1)
        row.updated_at = datetime.now(UTC)
        db.flush()
        self.charged = False
        metric("people_user_quota_refunded_total", status=reason)
        logger.info(
            "people_user_quota_refunded user_ref=%s reason=%s remaining_used=%s",
            _safe_reference(self.user_id),
            reason,
            row.discoveries_used,
        )


def reserve_user_discovery(db: Session, user: User) -> QuotaReservation:
    """Atomically take one unit, or reject with a typed 429.

    Called once per explicit user action, after cache checks have already had
    their chance to satisfy the request for free.
    """

    limit = daily_limit_for(user)
    day = quota_day()
    row = _locked_row(db, user.id, day)
    used = int(row.discoveries_used or 0)
    if limit and used >= limit:
        metric("people_user_quota_rejections_total", status="daily_limit")
        logger.info(
            "people_user_quota_rejected user_ref=%s used=%s limit=%s",
            _safe_reference(user.id),
            used,
            limit,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PEOPLE_USER_DAILY_LIMIT_REACHED",
                "message": (
                    f"You have used all {limit} people searches for today."
                ),
                "availability_reason": "user_daily_limit_reached",
                "retryable": False,
                "daily_limit": limit,
                "daily_used": used,
                "daily_remaining": 0,
                "resets_at": next_reset().isoformat(),
            },
        )
    row.discoveries_used = used + 1
    row.updated_at = datetime.now(UTC)
    db.flush()
    metric("people_user_quota_charged_total", status="reserved")
    return QuotaReservation(user_id=user.id, quota_date=day)


def reset_user_quota(db: Session, user_id: int, *, on: date | None = None) -> int:
    """Zero one user's quota for one day. Never touches results or budgets."""

    day = (on or quota_day()).isoformat()
    row = db.scalar(
        select(PeopleUserDiscoveryQuota).where(
            PeopleUserDiscoveryQuota.user_id == user_id,
            PeopleUserDiscoveryQuota.quota_date == day,
        )
    )
    if row is None or not row.discoveries_used:
        return 0
    row.discoveries_used = 0
    row.updated_at = datetime.now(UTC)
    db.flush()
    return 1


def _safe_reference(user_id: int) -> str:
    import hashlib

    return hashlib.sha256(f"people-log-user:{user_id}".encode()).hexdigest()[:12]
