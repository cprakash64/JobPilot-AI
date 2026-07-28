from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.entities import PeopleProviderOperationUsage


class ProviderUsagePersistenceError(RuntimeError):
    """Fail closed before an unaccounted external provider request."""


@dataclass(frozen=True)
class ProviderUsageContext:
    user_id: int
    job_id: int
    discovery_run_id: int
    adapter_version: str


SessionFactory = Callable[[], Session]


def operation_idempotency_key(
    context: ProviderUsageContext,
    *,
    provider: str,
    operation_type: str,
    ordinal: int,
) -> str:
    material = (
        f"people-provider-operation:{context.discovery_run_id}:{provider}:"
        f"{operation_type}:{ordinal}:{context.adapter_version}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


class ProviderUsageRecorder:
    def __init__(
        self,
        context: ProviderUsageContext,
        *,
        session_factory: SessionFactory = SessionLocal,
        unknown_credit_budget_units: int = 1,
    ) -> None:
        self.context = context
        self.session_factory = session_factory
        self.unknown_credit_budget_units = max(0, unknown_credit_budget_units)

    def start(
        self,
        *,
        idempotency_key: str,
        provider: str,
        operation_type: str,
    ) -> bool:
        session = self.session_factory()
        try:
            session.add(
                PeopleProviderOperationUsage(
                    idempotency_key=idempotency_key,
                    user_id=self.context.user_id,
                    job_id=self.context.job_id,
                    discovery_run_id=self.context.discovery_run_id,
                    provider=provider,
                    operation_type=operation_type,
                    request_count=1,
                    http_outcome="request_started",
                    credits_reported=None,
                    credits_estimated=None,
                    budget_units=self.unknown_credit_budget_units,
                    credit_status="unknown",
                    adapter_version=self.context.adapter_version,
                    occurred_at=datetime.now(UTC),
                )
            )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
        except SQLAlchemyError as exc:
            session.rollback()
            raise ProviderUsagePersistenceError(
                "provider usage could not be recorded before request"
            ) from exc
        finally:
            session.close()

    def finish(
        self,
        *,
        idempotency_key: str,
        http_outcome: str,
        credits_reported: int | None,
        credits_estimated: int | None = None,
        estimate_when_unknown: bool = True,
    ) -> None:
        session = self.session_factory()
        try:
            row = session.scalar(
                select(PeopleProviderOperationUsage).where(
                    PeopleProviderOperationUsage.idempotency_key
                    == idempotency_key
                )
            )
            if row is None:
                raise ProviderUsagePersistenceError(
                    "provider usage start record is missing"
                )
            row.http_outcome = http_outcome[:96]
            if credits_reported is not None:
                row.credits_reported = max(0, credits_reported)
                row.credits_estimated = None
                row.budget_units = row.credits_reported
                row.credit_status = "reported"
            elif credits_estimated is not None:
                row.credits_reported = None
                row.credits_estimated = max(0, credits_estimated)
                row.budget_units = row.credits_estimated
                row.credit_status = "estimated"
            elif estimate_when_unknown:
                row.credits_reported = None
                row.credits_estimated = self.unknown_credit_budget_units
                row.budget_units = self.unknown_credit_budget_units
                row.credit_status = "estimated"
            else:
                row.credits_reported = None
                row.credits_estimated = None
                row.budget_units = self.unknown_credit_budget_units
                row.credit_status = "unknown"
            session.commit()
        except ProviderUsagePersistenceError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise ProviderUsagePersistenceError(
                "provider usage outcome could not be recorded"
            ) from exc
        finally:
            session.close()


def reported_credits(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("credits_consumed")
    if value is None and isinstance(payload.get("data"), dict):
        value = payload["data"].get("credits_consumed")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def reconcile_unknown_operations(
    *,
    context: ProviderUsageContext,
    provider: str,
    operation_counts: dict[str, int],
    safe_http_outcomes: dict[str, str],
    session_factory: SessionFactory = SessionLocal,
    unknown_credit_budget_units: int = 1,
) -> int:
    """Idempotently restore identifier-free call facts without inventing credits."""

    inserted = 0
    recorder = ProviderUsageRecorder(
        context,
        session_factory=session_factory,
        unknown_credit_budget_units=unknown_credit_budget_units,
    )
    for operation_type, count in operation_counts.items():
        for ordinal in range(1, max(0, count) + 1):
            key = hashlib.sha256(
                (
                    f"people-provider-reconciliation:{context.discovery_run_id}:"
                    f"{provider}:{operation_type}:{ordinal}:"
                    f"{context.adapter_version}"
                ).encode()
            ).hexdigest()
            if not recorder.start(
                idempotency_key=key,
                provider=provider,
                operation_type=operation_type,
            ):
                continue
            recorder.finish(
                idempotency_key=key,
                http_outcome=safe_http_outcomes.get(
                    operation_type, "outcome_unknown"
                ),
                credits_reported=None,
                estimate_when_unknown=False,
            )
            inserted += 1
    return inserted
