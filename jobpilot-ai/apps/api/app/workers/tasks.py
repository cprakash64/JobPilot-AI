"""Celery worker tasks + beat schedule for JobPilot.

Runs in dedicated ``worker`` and ``scheduler`` (beat) containers, never inside
the API replicas, so multiple API instances can never trigger duplicate daily
ingestion (the beat scheduler is a single service and the run itself is guarded
by a distributed lock).
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.db.session import SessionLocal
from app.jobs.scoring_service import active_user_ids, score_users_for_job

logger = logging.getLogger("jobpilot.worker")

celery_app = Celery("jobpilot", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.timezone = settings.job_ingestion_timezone
celery_app.conf.task_default_retry_delay = 30
celery_app.conf.broker_connection_retry_on_startup = True


def _crontab_from_expr(expr: str) -> crontab:
    """Parse a 5-field cron expression ``m h dom mon dow`` into a Celery crontab.
    Falls back to a daily 06:00 schedule if the expression is malformed."""
    parts = (expr or "").split()
    if len(parts) != 5:
        logger.warning("Invalid JOB_INGESTION_SCHEDULE %r; defaulting to '0 6 * * *'.", expr)
        parts = ["0", "6", "*", "*", "*"]
    minute, hour, dom, month, dow = parts
    return crontab(minute=minute, hour=hour, day_of_month=dom, month_of_year=month, day_of_week=dow)


if settings.job_ingestion_enabled:
    celery_app.conf.beat_schedule = {
        "daily-job-ingestion": {
            "task": "run_daily_ingestion",
            "schedule": _crontab_from_expr(settings.job_ingestion_schedule),
        }
    }


@celery_app.task(name="match_jobs_for_user")
def match_jobs_for_user_task(user_id: int) -> int:
    """On-demand rescore for one user (kept for backward compatibility)."""
    from app.jobs.job_ingestion_service import rematch_user

    db = SessionLocal()
    try:
        return rematch_user(db, user_id)
    finally:
        db.close()


@celery_app.task(
    name="score_jobs_for_users",
    bind=True,
    max_retries=3,
    acks_late=True,
)
def score_jobs_for_users_task(self, job_ids: list[int], exclude_user_ids: list[int] | None = None) -> dict:
    """Score a batch of newly ingested/changed jobs for all active users.

    Idempotent: the scoring service only writes when something changed, so a
    retry after a partial failure never double-writes or clobbers newer scores.
    A failure on one (user, job) pair is captured on the row and never aborts the
    batch."""
    exclude = set(exclude_user_ids or [])
    db = SessionLocal()
    totals = {"scored": 0, "skipped": 0, "failed": 0, "profile_incomplete": 0}
    try:
        targets = [uid for uid in active_user_ids(db) if uid not in exclude]
        for job_id in job_ids:
            stats = score_users_for_job(db, job_id, targets)
            for key in totals:
                totals[key] += getattr(stats, key)
        return totals
    except Exception as exc:  # noqa: BLE001 - retry transient infra errors
        logger.exception("score_jobs_for_users failed; retrying")
        raise self.retry(exc=exc, countdown=30) from exc
    finally:
        db.close()


@celery_app.task(name="run_daily_ingestion", bind=True, max_retries=0)
def run_daily_ingestion_task(self, trigger: str = "scheduled") -> dict:
    """Beat-triggered daily ingestion across all sources."""
    from app.jobs.scheduled_ingestion import run_daily_ingestion

    db = SessionLocal()
    try:
        outcome = run_daily_ingestion(db, trigger=trigger)
        return {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "jobs_inserted": outcome.jobs_inserted,
            "jobs_updated": outcome.jobs_updated,
            "jobs_expired": outcome.jobs_expired,
            "scoring_tasks_queued": outcome.scoring_tasks_queued,
        }
    finally:
        db.close()
