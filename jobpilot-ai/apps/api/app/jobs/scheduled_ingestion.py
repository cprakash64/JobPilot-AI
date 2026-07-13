"""System-wide daily ingestion orchestration with run tracking.

Unlike ``discover_jobs`` (which is scoped to one logged-in user and used for the
interactive "Find fresh jobs" action), this module ingests from *all* enabled
sources on a schedule, independent of any browser session. It:

* holds a single-run distributed lock (Redis, with a Postgres advisory-lock
  fallback) so multiple API/worker replicas never ingest concurrently,
* isolates per-source failures so one bad board never fails the whole run,
* marks jobs that disappeared from their source as inactive (Part 9),
* enqueues background scoring for newly-changed jobs across all active users,
* records an ``IngestionRun`` row with safe, summarized tallies (Part 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs.job_ingestion_service import (
    _fetch_all,
    _persist_job,
    enqueue_scoring_for_jobs,
)
from app.jobs.job_normalization_service import is_fresh, normalize_jobs
from app.jobs.source_registry import build_adapters
from app.models.entities import IngestionRun, IngestionStatus, IngestionTrigger, JobPosting

logger = logging.getLogger("jobpilot.ingestion")

INGESTION_LOCK_KEY = "jobpilot:ingestion:daily"


@dataclass
class RunOutcome:
    run_id: int | None
    status: str
    skipped_locked: bool = False
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_expired: int = 0
    scoring_tasks_queued: int = 0


# --------------------------------------------------------------------------- #
# Distributed lock
# --------------------------------------------------------------------------- #
class _RedisLock:
    """Best-effort single-holder lock using Redis SET NX EX. Returns False from
    ``acquire`` when another holder is active. Safe no-op if Redis is down (the
    caller then relies on the Postgres advisory lock)."""

    def __init__(self, key: str, ttl_seconds: int) -> None:
        self.key = key
        self.ttl = ttl_seconds
        self._client = None
        self._token = str(datetime.now(UTC).timestamp())

    def acquire(self) -> bool:
        try:
            import redis

            self._client = redis.Redis.from_url(settings.redis_url)
            return bool(self._client.set(self.key, self._token, nx=True, ex=self.ttl))
        except Exception as exc:  # noqa: BLE001 - Redis optional; fall through
            logger.warning("Redis lock unavailable (%s); relying on DB lock.", type(exc).__name__)
            self._client = None
            return True

    def release(self) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            # Only release if we still own it.
            if self._client.get(self.key) == self._token.encode():
                self._client.delete(self.key)


@contextlib.contextmanager
def _pg_advisory_lock(db: Session):
    """Postgres advisory lock (transaction-independent). No-op on SQLite."""
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect != "postgresql":
        yield True
        return
    from sqlalchemy import text

    lock_id = 0x0B_C0_FF_EE  # stable app-specific advisory lock id
    got = db.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
    try:
        yield bool(got)
    finally:
        if got:
            with contextlib.suppress(Exception):
                db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                db.commit()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_daily_ingestion(
    db: Session,
    *,
    trigger: str = IngestionTrigger.scheduled.value,
    days: int | None = None,
    max_companies: int | None = None,
) -> RunOutcome:
    """Ingest from all enabled sources once, under a single-run lock."""
    days = days if days is not None else settings.job_posted_within_days
    lock = _RedisLock(INGESTION_LOCK_KEY, settings.job_ingestion_lock_ttl_seconds)
    if not lock.acquire():
        logger.info("Daily ingestion already running elsewhere; skipping.")
        return RunOutcome(run_id=None, status="skipped_locked", skipped_locked=True)

    with _pg_advisory_lock(db) as got_db_lock:
        if not got_db_lock:
            lock.release()
            logger.info("Daily ingestion advisory lock held elsewhere; skipping.")
            return RunOutcome(run_id=None, status="skipped_locked", skipped_locked=True)
        try:
            return _run_locked(db, trigger=trigger, days=days, max_companies=max_companies)
        finally:
            lock.release()


def _run_locked(
    db: Session, *, trigger: str, days: int, max_companies: int | None
) -> RunOutcome:
    run = IngestionRun(trigger=trigger, status=IngestionStatus.running.value, errors=[], detail={})
    db.add(run)
    db.commit()
    db.refresh(run)

    adapters = build_adapters(max_companies or settings.job_discovery_max_companies)
    timeout = settings.job_ingestion_source_timeout_seconds

    fetched_jobs, warnings, stats = asyncio.run(_fetch_all_with_timeout(adapters, days, timeout))

    normalized = normalize_jobs(fetched_jobs)
    fresh_jobs = [j for j in normalized if is_fresh(j.posted_at, days, include_unknown=True)]

    inserted = updated = 0
    changed_ids: list[int] = []
    seen_ids: list[int] = []
    for job in fresh_jobs:
        outcome = _persist_job(db, job)
        if outcome is None:
            continue
        record, is_new, content_changed = outcome
        seen_ids.append(record.id)
        if is_new:
            inserted += 1
        else:
            updated += 1
        if is_new or content_changed:
            changed_ids.append(record.id)
    db.commit()

    expired = _expire_stale_jobs(db)

    queued = enqueue_scoring_for_jobs(changed_ids) if changed_ids else 0

    succeeded = int(stats.get("sources_succeeded", 0))
    failed = int(stats.get("sources_failed", 0))
    run.completed_at = datetime.now(UTC)
    run.sources_attempted = int(stats.get("sources_searched", 0))
    run.sources_succeeded = succeeded
    run.sources_failed = failed
    run.jobs_fetched = len(fetched_jobs)
    run.jobs_inserted = inserted
    run.jobs_updated = updated
    run.jobs_skipped = max(0, len(normalized) - len(fresh_jobs))
    run.jobs_expired = expired
    run.scoring_tasks_queued = queued
    run.errors = warnings[:50]
    run.detail = {"by_provider": stats.get("by_provider", {})}
    if failed and succeeded:
        run.status = IngestionStatus.partial.value
    elif failed and not succeeded:
        run.status = IngestionStatus.failed.value
    else:
        run.status = IngestionStatus.succeeded.value
    db.commit()

    logger.info(
        "Ingestion run %s: %s inserted, %s updated, %s expired, %s scoring jobs queued, "
        "%s/%s sources ok",
        run.id, inserted, updated, expired, queued, succeeded, run.sources_attempted,
    )
    return RunOutcome(
        run_id=run.id,
        status=run.status,
        jobs_inserted=inserted,
        jobs_updated=updated,
        jobs_expired=expired,
        scoring_tasks_queued=queued,
    )


async def _fetch_all_with_timeout(adapters, days: int, timeout: float):
    # ``_fetch_all`` uses ``settings.job_discovery_timeout_seconds``; the daily run
    # wants its own (usually larger) per-source timeout. Temporarily override.
    original = settings.job_discovery_timeout_seconds
    try:
        settings.job_discovery_timeout_seconds = timeout
        return await _fetch_all(adapters, days)
    finally:
        settings.job_discovery_timeout_seconds = original


def _expire_stale_jobs(db: Session) -> int:
    """Mark active jobs that have not been seen on their source within the grace
    period as inactive. Never deletes rows; saved/tracked jobs keep their data."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_expiry_grace_days)
    expired = 0
    for job in db.scalars(select(JobPosting).where(JobPosting.is_active.is_(True))):
        last_seen = job.last_seen_at or job.discovered_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        if last_seen is not None and last_seen < cutoff:
            job.is_active = False
            expired += 1
    if expired:
        db.commit()
    return expired


def recent_runs(db: Session, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "started_at": r.started_at,
            "completed_at": r.completed_at,
            "sources_attempted": r.sources_attempted,
            "sources_succeeded": r.sources_succeeded,
            "sources_failed": r.sources_failed,
            "jobs_fetched": r.jobs_fetched,
            "jobs_inserted": r.jobs_inserted,
            "jobs_updated": r.jobs_updated,
            "jobs_skipped": r.jobs_skipped,
            "jobs_expired": r.jobs_expired,
            "scoring_tasks_queued": r.scoring_tasks_queued,
            "errors": r.errors or [],
        }
        for r in rows
    ]
