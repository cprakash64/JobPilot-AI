# ruff: noqa: B008, E501
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import User
from app.people.feature_flags import recommendations_availability, recommendations_enabled
from app.people.schemas import FeedbackRequest, OutreachDraftRequest
from app.people.service import (
    diagnostics_payload,
    discover,
    find_email,
    mark_contacted,
    outreach_draft,
    recommendations_payload,
    set_saved,
    submit_feedback,
)

router = APIRouter(prefix="/jobs/{job_id}/people", tags=["people"])


def _require_enabled(user: User) -> None:
    if not recommendations_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PEOPLE_FEATURE_DISABLED", "message": "People recommendations are disabled."},
        )


@router.get("")
def get_people(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    availability = recommendations_availability(user)
    if availability != "available":
        return {
            "status": "disabled", "availability_reason": availability, "beta": False,
            "categories": {"likely_recruiters": [], "potential_hiring_managers": [], "potential_referrers": []},
            "warnings": [], "controls": {"email_discovery": False, "outreach_drafting": False},
        }
    return recommendations_payload(db, user, job_id)


@router.post("/discover")
async def discover_people(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    _require_enabled(user)
    return await discover(db, user, job_id)


@router.get("/diagnostics")
def get_people_diagnostics(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    _require_enabled(user)
    return diagnostics_payload(db, user, job_id)


@router.post("/{recommendation_id}/email")
async def discover_email(
    job_id: int, recommendation_id: int, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return await find_email(db, user, job_id, recommendation_id)


@router.post("/{recommendation_id}/outreach-draft")
def draft_outreach(
    job_id: int, recommendation_id: int, request: OutreachDraftRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return outreach_draft(db, user, job_id, recommendation_id, request)


@router.post("/{recommendation_id}/feedback")
def feedback(
    job_id: int, recommendation_id: int, request: FeedbackRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return submit_feedback(db, user, job_id, recommendation_id, request)


@router.post("/{recommendation_id}/save")
def save(
    job_id: int, recommendation_id: int, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return set_saved(db, user, job_id, recommendation_id, True)


@router.delete("/{recommendation_id}/save")
def unsave(
    job_id: int, recommendation_id: int, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return set_saved(db, user, job_id, recommendation_id, False)


@router.post("/{recommendation_id}/contacted")
def contacted(
    job_id: int, recommendation_id: int, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_enabled(user)
    return mark_contacted(db, user, job_id, recommendation_id)
