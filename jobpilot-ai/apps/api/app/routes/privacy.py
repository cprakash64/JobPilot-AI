from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.audit import record_audit
from app.db.session import get_db
from app.models.entities import (
    ApplicationTracker,
    Award,
    Certification,
    Education,
    Experience,
    GeneratedDocument,
    JobMatch,
    Project,
    SensitiveDemographics,
    User,
    UserProfile,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("/export")
def export_user_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    data = {
        "user": {"id": user.id, "email": user.email, "created_at": user.created_at},
        "profile": db.scalar(select(UserProfile).where(UserProfile.user_id == user.id)),
        "career": {
            "education": db.scalars(select(Education).where(Education.user_id == user.id)).all(),
            "experience": db.scalars(select(Experience).where(Experience.user_id == user.id)).all(),
            "projects": db.scalars(select(Project).where(Project.user_id == user.id)).all(),
            "certifications": db.scalars(select(Certification).where(Certification.user_id == user.id)).all(),
            "awards": db.scalars(select(Award).where(Award.user_id == user.id)).all(),
        },
        "sensitive_demographics": db.scalar(
            select(SensitiveDemographics).where(SensitiveDemographics.user_id == user.id)
        ),
        "matches": db.scalars(select(JobMatch).where(JobMatch.user_id == user.id)).all(),
        "documents": db.scalars(select(GeneratedDocument).where(GeneratedDocument.user_id == user.id)).all(),
        "applications": db.scalars(select(ApplicationTracker).where(ApplicationTracker.user_id == user.id)).all(),
    }
    record_audit(db, user.id, "data_exported")
    db.commit()
    return data


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    record_audit(db, user.id, "account_deleted")
    db.flush()
    db.execute(delete(User).where(User.id == user.id))
    db.commit()

