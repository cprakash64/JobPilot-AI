from sqlalchemy.orm import Session

from app.models.entities import AuditLog


def record_audit(db: Session, user_id: int, action: str, metadata: dict | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, metadata_json=metadata or {}))
