"""Backfill fit scores for eligible jobs currently showing "Not scored".

Usage::

    python -m app.jobs.backfill_scores \
        --posted-within-days 7 \
        --only-missing \
        --batch-size 100 \
        [--dry-run] [--force] [--user-id N] [--include-unknown-dates]

The command is resumable and idempotent: it reuses the scoring service's
change-detection and score-version guard, so re-running never double-writes and
never overwrites a newer valid score with an older result. A failure on one
(user, job) pair is recorded on the row and does not abort the run.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.config import settings
from app.db.session import SessionLocal
from app.jobs.scoring_service import ScoringStats, active_user_ids, score_jobs_for_user

logger = logging.getLogger("jobpilot.backfill")


def run_backfill(
    *,
    posted_within_days: int,
    only_missing: bool,
    batch_size: int,
    dry_run: bool,
    force: bool,
    user_id: int | None,
    include_unknown_dates: bool,
) -> ScoringStats:
    db = SessionLocal()
    totals = ScoringStats()
    try:
        user_ids = [user_id] if user_id is not None else active_user_ids(db)
        logger.info(
            "Backfill starting: users=%d posted_within_days=%d only_missing=%s force=%s dry_run=%s",
            len(user_ids), posted_within_days, only_missing, force, dry_run,
        )
        for uid in user_ids:
            if dry_run:
                # Score into a session we roll back, so nothing is persisted but we
                # still get accurate would-score counts.
                stats = score_jobs_for_user(
                    db, uid,
                    days=posted_within_days,
                    include_unknown=include_unknown_dates,
                    only_missing=only_missing,
                    force=force,
                    batch_size=batch_size,
                    commit=False,
                )
                db.rollback()
            else:
                stats = score_jobs_for_user(
                    db, uid,
                    days=posted_within_days,
                    include_unknown=include_unknown_dates,
                    only_missing=only_missing,
                    force=force,
                    batch_size=batch_size,
                    commit=True,
                )
            for key in ("scanned", "selected", "scored", "skipped", "failed", "profile_incomplete"):
                setattr(totals, key, getattr(totals, key) + getattr(stats, key))
            logger.info(
                "  user=%s scanned=%d selected=%d scored=%d skipped=%d failed=%d profile_incomplete=%d",
                uid, stats.scanned, stats.selected, stats.scored, stats.skipped,
                stats.failed, stats.profile_incomplete,
            )
        logger.info(
            "Backfill complete%s: jobs_scanned(pairs)=%d selected=%d scored=%d skipped=%d "
            "failed=%d profile_incomplete=%d",
            " (dry-run, nothing written)" if dry_run else "",
            totals.scanned, totals.selected, totals.scored, totals.skipped,
            totals.failed, totals.profile_incomplete,
        )
        return totals
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_scores", description=__doc__)
    parser.add_argument("--posted-within-days", type=int, default=settings.job_posted_within_days)
    parser.add_argument("--only-missing", action="store_true",
                        help="Only score pairs that have no up-to-date scored row.")
    parser.add_argument("--batch-size", type=int, default=settings.job_scoring_batch_size)
    parser.add_argument("--dry-run", action="store_true", help="Compute counts without writing.")
    parser.add_argument("--force", action="store_true",
                        help="Ignore change-detection and rescore everything.")
    parser.add_argument("--user-id", type=int, default=None, help="Restrict to a single user.")
    parser.add_argument("--include-unknown-dates", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    stats = run_backfill(
        posted_within_days=args.posted_within_days,
        only_missing=args.only_missing,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        force=args.force,
        user_id=args.user_id,
        include_unknown_dates=args.include_unknown_dates,
    )
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
