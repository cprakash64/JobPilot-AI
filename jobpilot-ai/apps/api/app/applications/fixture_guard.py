"""Production guard against known development/seed fixtures reaching a real
application package.

Root cause of the "demo@example.com filled into a real application" report:
there was never a code-level fallback that substitutes demo data when a real
value is missing (confirmed by inspection of answer_vault_service.py) — the
seeded ``scripts/seed_demo_data.py`` account is the ONLY user row in this
project's single shared database, and its profile's ``full_name`` was later
edited to a real name via the normal profile-edit flow while its email
(``demo@example.com``, not editable via any route) stayed the seeded value.

This module adds a narrow, explicit denylist check — not a broad heuristic —
so a *literal known dev fixture* can never prepare a real (production)
application. It intentionally does NOT reject "unusual-looking" emails in
general (e.g. a real user who happens to use an @example.com address in a
dev/staging environment): only the exact identities this repo's own seed
script creates.

Gated by ``app_env``: this only blocks in "production". In development (the
only environment this project currently runs), the seeded demo account
remains usable for manual testing, matching existing behavior — the guard is
a forward-looking safety rail for when a real production deployment exists,
not a retroactive lockout of the shared dev account.
"""

from __future__ import annotations

from app.core.config import settings
from app.models.entities import User

# Exact identities created by scripts/seed_demo_data.py. Deliberately a small,
# explicit set — never a pattern/heuristic on domain or name.
KNOWN_DEV_FIXTURE_EMAILS: frozenset[str] = frozenset({"demo@example.com"})

# Shown in the review widget when a real employer application still carries a
# seeded demo email. The user replaces it through the authenticated profile flow.
DEMO_EMAIL_REPLACEMENT_REASON: str = (
    "JobPilot still has a demo email for this profile. Add your real email before continuing."
)


def is_dev_fixture_identity(user: User) -> bool:
    return (user.email or "").strip().lower() in KNOWN_DEV_FIXTURE_EMAILS


def is_known_fixture_email(value: str | None) -> bool:
    """True only for an EXACT seeded fixture email — never a heuristic. Used to
    keep known demo identities out of a real employer application's answer set
    (they become ``missing_information``, not a verified answer) in EVERY
    environment, not just production."""
    return (value or "").strip().lower() in KNOWN_DEV_FIXTURE_EMAILS


def guard_against_dev_fixture(user: User) -> None:
    """Raise ``PreparationError`` (DEV_FIXTURE_IDENTITY) if this is a known
    development fixture AND the app is configured for production. No-op
    otherwise — never rejects a legitimate value merely because it looks
    unusual."""
    if settings.app_env != "production":
        return
    if not is_dev_fixture_identity(user):
        return
    from app.applications.preparation import dev_fixture_identity_blocked

    raise dev_fixture_identity_blocked()
