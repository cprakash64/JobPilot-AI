"""Request/response schemas for assisted auto-apply."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class CreateSessionIn(BaseModel):
    job_id: int


class ExchangeTokenIn(BaseModel):
    launch_token: str = Field(min_length=10, max_length=4000)


class StatusPatchIn(BaseModel):
    status: str


class CompleteSessionIn(BaseModel):
    confirmed: bool = False


class SessionEventIn(BaseModel):
    action_type: str = Field(min_length=1, max_length=60)
    field_key: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=60)
    status: str | None = Field(default=None, max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] | None = None


class AnswerUpsertIn(BaseModel):
    value: str | None = None
    display_value: str | None = None
    source: str | None = None
    is_user_verified: bool | None = None
    allow_auto_fill: bool | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    scope: str | None = None
    company_key: str | None = None


class SessionAnswerUpsertIn(BaseModel):
    """Save-for-future-applications from the extension's review widget. Always
    an explicit, user-initiated confirmation — the caller (background.ts)
    only sends this after the user picks "Save and fill" on an unresolved
    question, so it is always recorded as ``source="user_confirmed"`` and
    verified."""

    value: str = Field(min_length=1, max_length=4000)
    display_value: str | None = Field(default=None, max_length=4000)
    scope: str | None = None
    company_key: str | None = Field(default=None, max_length=160)


class SessionNameConfirmIn(BaseModel):
    """Session-scoped structured-name confirmation. ``given_name``/
    ``family_name`` stay accepted as aliases for older extension builds."""

    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(
        min_length=1, max_length=120,
        validation_alias=AliasChoices("first_name", "given_name"),
    )
    last_name: str = Field(
        min_length=1, max_length=120,
        validation_alias=AliasChoices("last_name", "family_name"),
    )
    middle_name: str | None = Field(default=None, max_length=120)
    preferred_first_name: str | None = Field(default=None, max_length=120)
    preferred_last_name: str | None = Field(default=None, max_length=120)


class AutofillFailure(BaseModel):
    """A single field the extension could not confidently fill. Carries only a
    canonical key and a machine reason code — never the employer value/HTML."""

    field_key: str = Field(min_length=1, max_length=120)
    reason_code: str = Field(min_length=1, max_length=60)


class MapOptionIn(BaseModel):
    """Request to translate a confirmed answer into an employer's exact option
    wording. Carries ONLY the question, its options, the canonical key, and the
    confirmed answer — never profile data, tokens, or document content."""

    question_label: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=1, max_length=200)
    canonical_key: str = Field(min_length=1, max_length=100)
    confirmed_answer: str | None = Field(default=None, max_length=500)
    help_text: str | None = Field(default=None, max_length=1000)


class AutofillResultIn(BaseModel):
    """Safe, PII-free summary the extension reports after an autofill pass.

    Deliberately counts + codes only: no field values, no page HTML, no personal
    data. Used for the tracker and debugging.
    """

    status: str = Field(min_length=1, max_length=40)
    ats: str | None = Field(default=None, max_length=40)
    fields_discovered: int = Field(default=0, ge=0, le=1000)
    fields_filled: int = Field(default=0, ge=0, le=1000)
    documents_uploaded: list[str] = Field(default_factory=list, max_length=10)
    review_items: int = Field(default=0, ge=0, le=1000)
    failures: list[AutofillFailure] = Field(default_factory=list, max_length=100)
