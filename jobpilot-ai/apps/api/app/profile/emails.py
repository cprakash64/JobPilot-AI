"""Which email an application should carry.

The account email is a LOGIN identity. The application email is contact
information an employer will use. Conflating them meant a demo/fixture login
(demo@example.com) blocked autofill outright, because fixture_guard correctly
refuses to put a fixture address on a real application.

Precedence, highest first:
  1. a CONFIRMED application_email — the user said "use this on applications";
  2. an unconfirmed application_email that is a real address — proposed, but
     reported as unconfirmed so the UI can ask for one explicit save;
  3. the account email, only when it is not a known fixture address.

Nothing is ever silently promoted into a confirmed value.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.applications.fixture_guard import is_known_fixture_email

# RFC 2606 / RFC 6761 reserve these for documentation and examples. They can
# never receive mail, so an employer using one would simply never reach the
# candidate — which is worse than leaving the field blank, because the
# application looks complete while being undeliverable.
RESERVED_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {"example.com", "example.net", "example.org", "example.edu", "invalid", "localhost", "test"}
)

RESERVED_EMAIL_MESSAGE = "Enter a real email address that can receive employer messages."


def is_reserved_email_domain(value: str | None) -> bool:
    """True for RFC-reserved example/test domains, including subdomains.

    Subdomains count: mail.example.com is just as undeliverable as example.com.
    """
    address = (value or "").strip().lower()
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].strip().strip(".")
    if not domain:
        return False
    return any(
        domain == reserved or domain.endswith(f".{reserved}")
        for reserved in RESERVED_EMAIL_DOMAINS
    )


def is_usable_application_email(value: str | None) -> bool:
    """An address that could actually reach the candidate."""
    address = (value or "").strip()
    if not address or "@" not in address:
        return False
    return not is_reserved_email_domain(address) and not is_known_fixture_email(address)


@dataclass(frozen=True)
class ResolvedEmail:
    value: str
    source: str  # "application_email" | "account_email" | "none"
    confirmed: bool

    @property
    def usable(self) -> bool:
        """Safe to put on a real application."""
        return bool(self.value) and self.source != "none"


def resolve_application_email(
    *,
    application_email: str | None,
    application_email_confirmed: bool,
    account_email: str | None,
) -> ResolvedEmail:
    app_email = (application_email or "").strip()
    account = (account_email or "").strip()

    # A confirmed value still cannot be a reserved/undeliverable domain: the
    # user confirmed an address that no employer could ever reply to.
    if app_email and application_email_confirmed and is_usable_application_email(app_email):
        return ResolvedEmail(app_email, "application_email", True)

    # An unconfirmed application email is still preferred over the account
    # email, but it is reported unconfirmed so the UI can ask for one save.
    if app_email and is_usable_application_email(app_email):
        return ResolvedEmail(app_email, "application_email", False)

    # A fixture login must never reach an employer.
    if account and is_usable_application_email(account):
        return ResolvedEmail(account, "account_email", False)

    return ResolvedEmail("", "none", False)
