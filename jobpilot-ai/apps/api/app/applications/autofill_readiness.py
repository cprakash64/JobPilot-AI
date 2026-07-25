"""Is this profile actually ready to autofill an application?

The live failure this closes: the preparation modal said the application was
ready to fill while the profile had no confirmed name and no usable application
email. The extension then opened the employer page, found nothing to fill, and
left every identity field blank — with the UI still claiming everything was fine.

Readiness is computed from the CURRENT profile, never from a cached client
state, and reports the missing pieces using user-facing labels so the UI can say
exactly what to fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.profile.emails import is_reserved_email_domain, resolve_application_email


@dataclass
class ReadinessResult:
    ready: bool
    # Canonical keys that block autofill entirely.
    missing_required: list[str] = field(default_factory=list)
    # Things worth fixing that do not block.
    missing_optional: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def missing_labels(self) -> list[str]:
        return [self.labels.get(key, key) for key in self.missing_required]

    def to_payload(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "missing_required": self.missing_required,
            "missing_required_labels": self.missing_labels,
            "missing_optional": self.missing_optional,
        }


# User-facing labels, so the UI never has to translate canonical keys itself.
LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "application_email": "Application email",
    "phone": "Phone number",
    "location": "City",
    "resume": "Resume",
    "work_authorization": "Work authorization",
    "sponsorship": "Sponsorship requirement",
}


def evaluate_autofill_readiness(
    *,
    profile: dict[str, Any] | None,
    account_email: str | None,
    has_resume: bool,
) -> ReadinessResult:
    """Assess whether autofill can produce a usable application.

    Blocking: the identity and contact facts an application cannot go without.
    Non-blocking: things that degrade quality but still leave a fillable form.
    """
    profile = profile or {}
    missing: list[str] = []
    optional: list[str] = []

    # A name is only usable when the user CONFIRMED the structured split — an
    # unconfirmed name is never autofilled, so it is functionally missing.
    confirmed = bool(profile.get("name_confirmed"))
    if not confirmed or not (profile.get("first_name") or "").strip():
        missing.append("first_name")
    if not confirmed or not (profile.get("last_name") or "").strip():
        missing.append("last_name")

    email = resolve_application_email(
        application_email=profile.get("application_email"),
        application_email_confirmed=bool(profile.get("application_email_confirmed")),
        account_email=account_email,
    )
    # `usable` already excludes reserved/fixture domains, but check explicitly
    # so the reason is unambiguous in diagnostics.
    if not email.usable or is_reserved_email_domain(email.value):
        missing.append("application_email")

    if not (profile.get("phone_e164") or "").strip():
        missing.append("phone")

    if not (profile.get("location_city") or "").strip():
        missing.append("location")

    # A resume is required by essentially every ATS; without one the application
    # cannot be completed, so this blocks rather than warns.
    if not has_resume:
        missing.append("resume")

    # Consequential but not blocking: the form can still be filled and reviewed.
    if not (profile.get("work_authorization") or "").strip():
        optional.append("work_authorization")
    if profile.get("requires_sponsorship") is None:
        optional.append("sponsorship")

    return ReadinessResult(
        ready=not missing,
        missing_required=missing,
        missing_optional=optional,
        labels=LABELS,
    )
