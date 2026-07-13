"""Structured errors and stage names for the assisted-application pipeline.

Preparing an assisted application runs through a small, independently-traceable
set of stages. When a stage fails for a *known* reason we raise a
``PreparationError`` carrying a stable machine code, the failing stage, the HTTP
status the route should return, and whether the client may retry. The route
serializes this into a consistent envelope::

    {"error": {"code": ..., "message": ..., "stage": ..., "retryable": ...,
               "request_id": ...}}

Unknown/unexpected failures are never dressed up as a friendly error — they
propagate and become a 500 so they are visible in logs and monitoring.
"""

from __future__ import annotations

from enum import StrEnum


class PreparationStage(StrEnum):
    """Traceable stages of assisted-application preparation."""

    load_job = "load_job"
    load_candidate_profile = "load_candidate_profile"
    select_base_resume = "select_base_resume"
    generate_tailored_resume = "generate_tailored_resume"
    generate_cover_letter = "generate_cover_letter"
    generate_application_answers = "generate_application_answers"
    persist_application_package = "persist_application_package"


class PreparationError(Exception):
    """A known, user-actionable failure of a preparation stage.

    Carries everything the route needs to build a structured response. Never used
    to mask an unexpected bug — those stay as unhandled 500s on purpose.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        stage: PreparationStage,
        status_code: int,
        retryable: bool,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def to_payload(self, request_id: str | None = None) -> dict:
        error: dict = {
            "code": self.code,
            "message": self.message,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }
        if self.details:
            error["details"] = self.details
        if request_id:
            error["request_id"] = request_id
        return {"error": error}


# --------------------------------------------------------------------------- #
# Factory helpers for the known failure modes.
# --------------------------------------------------------------------------- #
def profile_incomplete(missing_sections: list[str]) -> PreparationError:
    readable = {
        "basic_info": "your name and contact details",
        "skills": "your skills",
        "experience": "your work experience",
    }
    parts = [readable.get(s, s) for s in missing_sections] or ["your profile"]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"
    return PreparationError(
        code="PROFILE_INCOMPLETE",
        message=f"Complete your profile before preparing an application. Add {joined}.",
        stage=PreparationStage.load_candidate_profile,
        status_code=422,
        retryable=False,
        details={"missing_sections": missing_sections},
    )


def invalid_application_url(message: str) -> PreparationError:
    return PreparationError(
        code="INVALID_APPLICATION_URL",
        message=message or "This job does not have a valid official application URL.",
        stage=PreparationStage.load_job,
        status_code=422,
        retryable=False,
    )


def job_not_found() -> PreparationError:
    return PreparationError(
        code="JOB_NOT_FOUND",
        message="This job is no longer available in JobPilot.",
        stage=PreparationStage.load_job,
        status_code=404,
        retryable=False,
    )


def database_unavailable() -> PreparationError:
    return PreparationError(
        code="DATABASE_UNAVAILABLE",
        message="The application service is temporarily unavailable. Please try again.",
        stage=PreparationStage.persist_application_package,
        status_code=503,
        retryable=True,
    )
