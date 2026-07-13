"""Authenticated, secret-free diagnostics endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.provider import ai_provider
from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.scheduled_ingestion import recent_runs
from app.jobs.scoring_service import SCORE_VERSION
from app.models.entities import JobMatch, JobPosting, ScoreState, User

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/openai-status")
def openai_status(user: User = Depends(get_current_user)) -> dict:
    """Report whether OpenAI is configured and which models are set.

    Never returns the API key itself — only whether one is present, the
    configured model names, and the last sanitized error (if any).
    """
    return ai_provider.status()


@router.get("/ingestion-runs")
def ingestion_runs(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Recent ingestion-run history (Part 6). Secret-free, summarized tallies."""
    return {"runs": recent_runs(db, limit=limit)}


@router.get("/scoring-status")
def scoring_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Observability snapshot: score-state backlog and oldest pending score."""
    by_state = {
        state: int(
            db.scalar(select(func.count(JobMatch.id)).where(JobMatch.score_state == state)) or 0
        )
        for state in (s.value for s in ScoreState)
    }
    oldest_pending = db.scalar(
        select(func.min(JobMatch.updated_at)).where(
            JobMatch.score_state.in_([ScoreState.pending.value, ScoreState.scoring.value])
        )
    )
    active_jobs = int(db.scalar(select(func.count(JobPosting.id)).where(JobPosting.is_active.is_(True))) or 0)
    return {
        "score_version": SCORE_VERSION,
        "by_state": by_state,
        "pending_backlog": by_state.get(ScoreState.pending.value, 0)
        + by_state.get(ScoreState.scoring.value, 0),
        "oldest_pending_at": oldest_pending,
        "active_jobs": active_jobs,
    }
